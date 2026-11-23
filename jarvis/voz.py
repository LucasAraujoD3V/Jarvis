"""
Áudio do Jarvis.

Ouvinte: microfone contínuo (blocos de 30 ms) → detector de fala por energia com limiar que se adapta ao ruído →
cada frase vira um áudio → transcrição (stt.Transcritor: Whisper local e/ou Google) → callback com o texto. Fica MUDO
enquanto o Jarvis fala, para não ouvir a própria voz.

Falante: Edge TTS (voz neural, precisa de internet) decodificado em memória e tocado pelo sounddevice, medindo o
volume a cada instante — é isso que faz a animação "falar" no ritmo da voz. Sem internet, cai na voz do Windows (SAPI).
`falar_fila` fala trechos que vão chegando (IA em streaming), sintetizando o próximo enquanto toca o atual.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import os
import queue
import tempfile
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import numpy as np
import sounddevice as sd

from .texto import dividir_frases

log = logging.getLogger("jarvis.voz")

TAXA_MIC = 16000
BLOCO_S = 0.03
BLOCO = int(TAXA_MIC * BLOCO_S)


def rms(a: np.ndarray) -> float:
    if a is None or len(a) == 0:
        return 0.0
    return float(np.sqrt(np.mean(a.astype(np.float32) ** 2)))


def aparar_silencio(audio: np.ndarray, taxa: int, limiar: int = 200, margem_s: float = 0.08) -> np.ndarray:
    """Tira o silêncio do começo e do fim (o Edge põe ~0,5 s em cada frase: 'Sim?' vinha com 1,9 s)."""
    if len(audio) == 0:
        return audio
    alto = np.flatnonzero(np.abs(audio.astype(np.int32)) > limiar)
    if len(alto) == 0:
        return audio
    m = int(taxa * margem_s)
    return audio[max(0, alto[0] - m): min(len(audio), alto[-1] + m + 1)].copy()


# ===================================================================================================== microfones
# Silêncio digital: entrada sem nada plugado, microfone virtual parado (Camo) ou fone Bluetooth desconectado dão rms < 1.
# Um microfone de verdade, mesmo em sala quieta, passa disso. Medido em 18-20/11/2026: entradas vazias = 0,0 a 0,5.
RMS_SEM_SINAL = 3.0
RMS_COM_SINAL = 5.0

# Roda em OUTRO processo: o PortAudio só lê a lista de dispositivos quando inicia, então um fone Bluetooth conectado
# depois que o Jarvis abriu só aparece num processo novo.
_SONDA = r"""
import json, numpy as np, sounddevice as sd
apis = sd.query_hostapis()
saida = []
for i, d in enumerate(sd.query_devices()):
    if d["max_input_channels"] < 1 or apis[d["hostapi"]]["name"] != "MME":
        continue
    n = d["name"]
    if "mapeador" in n.lower() or "mapper" in n.lower():
        continue
    try:
        taxa = int(d["default_samplerate"])
        a = sd.rec(int(taxa * 0.6), samplerate=taxa, channels=1, dtype="int16", device=i)
        sd.wait()
        v = a[:, 0].astype(np.float32)
        saida.append({"nome": n, "api": "MME", "rms": float(np.sqrt(np.mean(v ** 2)))})
    except Exception:
        saida.append({"nome": n, "api": "MME", "rms": -1.0})
print(json.dumps(saida))
"""


def sondar_microfones(timeout_s: float = 40) -> list[dict]:
    """Mede ~0,6 s de cada entrada (MME) num processo separado. Devolve [{nome, api, rms}]."""
    import json
    import subprocess
    import sys
    from pathlib import Path

    exe = Path(sys.executable)
    py = exe.with_name("python.exe") if exe.with_name("python.exe").exists() else exe
    r = subprocess.run([str(py), "-I", "-c", _SONDA], capture_output=True, text=True, timeout=timeout_s,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    linhas = [l for l in (r.stdout or "").strip().splitlines() if l.startswith("[")]
    if not linhas:
        raise RuntimeError(f"sonda de microfones falhou: {(r.stderr or '').strip()[:200]}")
    return json.loads(linhas[-1])


def mesmo_microfone(a: str, b: str) -> bool:
    """MME corta o nome em 31 caracteres: 'Microfone (High Definition Audi' == 'Microfone (High Definition Audio Device)'."""
    a, b = (a or "").strip().lower(), (b or "").strip().lower()
    return bool(a and b) and (a in b or b in a)


def escolher_microfone(sondas: list[dict], atual: str) -> dict | None:
    """Entre as entradas medidas, a com mais som (acima de RMS_COM_SINAL) que não seja a atual. None = nenhuma melhor."""
    candidatas = [s for s in sondas if s.get("rms", -1) >= RMS_COM_SINAL and not mesmo_microfone(s.get("nome", ""), atual)]
    return max(candidatas, key=lambda s: s["rms"]) if candidatas else None


# ===================================================================================================== Ouvinte
class Ouvinte:
    def __init__(self, cfg_mic: dict, transcritor, ao_texto: Callable[[str], None], ao_nivel: Callable[[float], None],
                 ao_aviso: Callable[[str], None], ao_sem_sinal: Callable[[bool], None] | None = None,
                 preferido: dict | None = None):
        self.cfg = cfg_mic
        self.transcritor = transcritor  # stt.Transcritor (Whisper local e/ou Google)
        self.ao_texto = ao_texto
        self.ao_nivel = ao_nivel
        self.ao_aviso = ao_aviso
        self.ao_sem_sinal = ao_sem_sinal
        self.preferido = preferido  # {"nome", "api"} escolhido sozinho (preferencias.json → microfone)
        self.limiar_minimo = float(cfg_mic.get("limiar_minimo", 20))
        self.fator = float(cfg_mic.get("fator_ruido", 2.6))
        self.silencio_fim = float(cfg_mic.get("silencio_fim_s", 0.9))
        self.max_frase = float(cfg_mic.get("max_frase_s", 15))
        self.fala_minima = float(cfg_mic.get("fala_minima_s", 0.3))
        self.limiar = self.limiar_minimo
        # ouve também enquanto fala, para "Jarvis, para" interromper (só frases com o nome valem; o resto é eco)
        self.escutar_falando = bool(cfg_mic.get("interromper_falando", True))
        self.max_frase_falando = float(cfg_mic.get("max_frase_falando_s", 3.0))
        self.piso = 0.0
        self.nome_microfone = ""
        self.sem_sinal = False
        self._vigia_pausado_ate = 0.0
        self.ouvindo_frase = False
        self._mudo_ate = 0.0
        self._mudo = False
        self._ativo = threading.Event()
        self._stream: sd.InputStream | None = None
        self._audio: queue.Queue = queue.Queue(maxsize=400)
        self._frases: queue.Queue = queue.Queue(maxsize=8)
        self._threads: list[threading.Thread] = []

    # mudo enquanto o Jarvis fala (+ um respiro curto depois, para o eco do alto-falante morrer)
    def definir_mudo(self, mudo: bool, respiro_s: float = 0.35) -> None:
        if mudo:
            self._mudo = True
        else:
            self._mudo_ate = time.monotonic() + respiro_s
            self._mudo = False

    @property
    def mudo(self) -> bool:
        return self._mudo or time.monotonic() < self._mudo_ate

    @property
    def ativo(self) -> bool:
        return self._ativo.is_set()

    def iniciar(self) -> None:
        if self._ativo.is_set():
            return
        dev = self._resolver()
        info = sd.query_devices(dev, kind="input") if dev is not None else sd.query_devices(kind="input")
        self.nome_microfone = str(info["name"])
        self._stream = sd.InputStream(samplerate=TAXA_MIC, blocksize=BLOCO, channels=1, dtype="int16", device=dev, callback=self._cb)
        self._stream.start()
        self._ativo.set()
        self._threads = [threading.Thread(target=self._loop_vad, name="jarvis-vad", daemon=True),
                         threading.Thread(target=self._loop_stt, name="jarvis-stt", daemon=True)]
        for t in self._threads:
            t.start()
        log.info("Microfone aberto: %s", self.nome_microfone)

    def _resolver(self):
        """Índice do microfone: config.json (nome ou número) > preferido salvo > padrão do Windows (None).
        Prefere a versão MME do dispositivo (converte a taxa sozinho)."""
        cfg_dev = self.cfg.get("dispositivo")
        if isinstance(cfg_dev, int):
            return cfg_dev
        alvo = str(cfg_dev) if cfg_dev else (self.preferido or {}).get("nome", "")
        if not alvo:
            return None
        apis = sd.query_hostapis()
        achados = []
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] >= 1 and mesmo_microfone(d["name"], alvo):
                achados.append((0 if apis[d["hostapi"]]["name"] == "MME" else 1, i))
        if not achados:
            log.warning("Microfone '%s' não está conectado agora: usando o padrão do Windows", alvo)
            return None
        return min(achados)[1]

    def pausar_vigia(self, segundos: float) -> None:
        self._vigia_pausado_ate = time.monotonic() + segundos

    def _mudar_sinal(self, sem: bool) -> None:
        if sem == self.sem_sinal:
            return
        self.sem_sinal = sem
        self.ao_aviso(f"Nenhum som no microfone há 1 min ({self.nome_microfone}). Se você falou e nada aconteceu, confira "
                      "o microfone. Procurando outro microfone com som…" if sem else
                      f"Microfone captando som ({self.nome_microfone}).")
        if self.ao_sem_sinal:
            self.ao_sem_sinal(sem)

    def parar(self) -> None:
        self._ativo.clear()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        for q in (self._audio, self._frases):
            try:
                q.put_nowait(None)
            except queue.Full:
                pass
        self.ao_nivel(0.0)
        log.info("Microfone fechado")

    def _cb(self, indata, frames, tempo, status) -> None:  # thread do PortAudio: só enfileira
        if not self._ativo.is_set():
            return
        try:
            self._audio.put_nowait(indata[:, 0].copy())
        except queue.Full:
            pass

    def _loop_vad(self) -> None:
        historico = collections.deque(maxlen=int(4.5 / BLOCO_S))  # ~4,5 s de volumes para estimar o ruído de fundo
        pre = collections.deque(maxlen=int(0.45 / BLOCO_S))  # pega o começo da palavra
        recentes = collections.deque(maxlen=10)
        gravando = False
        durante = False  # a frase foi captada enquanto o Jarvis falava (eco + talvez o usuário interrompendo)
        blocos: list[np.ndarray] = []
        voz_s = silencio_s = 0.0
        contador = 0
        # vigia de microfone mudo: percentil 95 do volume em janelas de 10 s. Percentil (e não o máximo) porque
        # entrada vazia solta estalos isolados que enganavam o vigia ("sem sinal" / "voltou" a cada minuto).
        janela = collections.deque(maxlen=int(10 / BLOCO_S))
        quietas = 0
        # calibração: a cada minuto, se houve som bem acima do ruído mas NENHUMA frase foi captada, registra o pico
        # ("Som sem frase") — é a fala que o limiar deixou passar; serve para ajustar microfone.limiar_minimo
        minuto: list[float] = []
        frases_minuto = 0
        ultimo_bloco = time.monotonic()
        self.sem_sinal = False
        while self._ativo.is_set():
            try:
                bloco = self._audio.get(timeout=0.5)
            except queue.Empty:
                if time.monotonic() - ultimo_bloco > 5:  # o dispositivo parou de mandar áudio (ex.: fone desconectado)
                    self._mudar_sinal(True)
                continue
            if bloco is None:
                break
            ultimo_bloco = time.monotonic()
            v = rms(bloco)
            if not self.mudo:  # a própria voz não entra na estimativa do ruído de fundo
                historico.append(v)
            janela.append(v)
            contador += 1
            if time.monotonic() < self._vigia_pausado_ate:  # outro processo mediu este microfone: ignora o tranco
                janela.clear()
            elif contador % int(10 / BLOCO_S) == 0 and len(janela) >= janela.maxlen // 2:
                p95 = float(np.percentile(np.fromiter(janela, dtype=np.float32), 95))
                # O microfone com fio deste PC fica em ~0,5 quando ninguém fala (igual a entrada vazia!), então só
                # silêncio LONGO (6 janelas = 1 min) vira "sem som". Para voltar exige som claro (histerese).
                quietas = quietas + 1 if p95 < RMS_SEM_SINAL else 0
                if not self.sem_sinal and quietas >= 6:
                    self._mudar_sinal(True)
                elif self.sem_sinal and p95 >= RMS_COM_SINAL * 2:
                    self._mudar_sinal(False)
            if contador % 10 == 0 and len(historico) > 20:
                self.piso = float(np.percentile(np.fromiter(historico, dtype=np.float32), 20))
                self.limiar = max(self.limiar_minimo, self.piso * self.fator)
            self.ao_nivel(max(0.0, min(1.0, (v - self.piso) / (self.limiar * 3))))
            if not self.mudo:
                minuto.append(v)
            if contador % int(60 / BLOCO_S) == 0:
                if minuto and frases_minuto == 0:
                    a = np.fromiter(minuto, dtype=np.float32)
                    pico, p99 = float(a.max()), float(np.percentile(a, 99))
                    if p99 >= max(self.piso * 6, 12):
                        log.info("Som sem frase (1 min): pico %.0f, p99 %.0f, piso %.1f, limiar %.0f", pico, p99, self.piso, self.limiar)
                minuto = []
                frases_minuto = 0

            if self.mudo and not self.escutar_falando:
                gravando = False
                self.ouvindo_frase = False
                blocos = []
                pre.clear()
                recentes.clear()
                continue

            fala = v > self.limiar
            if not gravando:
                pre.append(bloco)
                recentes.append(fala)
                if sum(recentes) >= 4:  # ~120 ms de voz nos últimos 300 ms
                    gravando = True
                    durante = self.mudo
                    self.ouvindo_frase = True
                    blocos = list(pre)
                    voz_s = sum(recentes) * BLOCO_S
                    silencio_s = 0.0
                continue

            blocos.append(bloco)
            if fala:
                voz_s += BLOCO_S
                silencio_s = 0.0
            else:
                silencio_s += BLOCO_S
            dur = len(blocos) * BLOCO_S
            durante = durante or self.mudo
            # enquanto ele fala, o eco vira 'fala' sem parar: corta em pedaços curtos para achar o nome rápido
            if silencio_s >= self.silencio_fim or dur >= (self.max_frase_falando if durante else self.max_frase):
                if voz_s >= self.fala_minima:
                    frases_minuto += 1
                    audio = np.concatenate(blocos)
                    # registra o volume das frases: base para calibrar limiar_minimo no microfone real
                    log.info("Frase captada: %.1f s, voz %.1f s, volume pico %.0f (limiar %.0f)",
                             dur, voz_s, max(rms(audio[i:i + BLOCO]) for i in range(0, len(audio), BLOCO)), self.limiar)
                    try:
                        self._frases.put_nowait((audio, durante))
                    except queue.Full:
                        log.warning("Fila de transcrição cheia: frase descartada")
                gravando = False
                self.ouvindo_frase = False
                blocos = []
                pre.clear()
                recentes.clear()

    def _loop_stt(self) -> None:
        while self._ativo.is_set():
            try:
                audio = self._frases.get(timeout=0.5)
            except queue.Empty:
                continue
            if audio is None:
                break
            audio, durante = audio if isinstance(audio, tuple) else (audio, False)
            t0 = time.monotonic()
            try:
                texto = self.transcritor.transcrever(audio, reserva=not durante)
            except Exception as e:  # noqa: BLE001
                log.exception("Erro na transcrição")
                self.ao_aviso(f"Erro na transcrição: {e}")
                continue
            texto = (texto or "").strip()
            if texto:
                log.debug("Transcrito em %.1f s: %s", time.monotonic() - t0, texto)
                self.ao_texto(texto, durante)


# ===================================================================================================== Falante
class Falante:
    def __init__(self, cfg_voz: dict, ao_nivel: Callable[[float], None], ao_trecho: Callable[[str], None]):
        self.voz = cfg_voz.get("nome", "pt-BR-AntonioNeural")
        self.velocidade = cfg_voz.get("velocidade", "+0%")
        self.tom = cfg_voz.get("tom", "+0Hz")
        self.volume = cfg_voz.get("volume", "+0%")
        self.ao_nivel = ao_nivel
        self.ao_trecho = ao_trecho
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jarvis-tts")
        self.ultimo_motor = ""
        # Frases curtas que se repetem ("Sim?", "Abrindo.") ficam guardadas: o Edge leva ~1–2 s por frase (rede).
        self._cache: collections.OrderedDict[str, tuple[np.ndarray, int]] = collections.OrderedDict()
        self._cache_max_chars = 40
        self._cache_max_itens = 64

    def preaquecer(self, frases: list[str]) -> None:
        """Sintetiza já (thread à parte) as frases curtas que ele mais fala, para saírem sem espera."""
        for f in frases:
            if f and len(f) <= self._cache_max_chars and f not in self._cache:
                try:
                    self.sintetizar(f)
                except Exception as e:  # noqa: BLE001
                    log.debug("Pré-aquecimento da voz falhou (%s): %s", f, e)
                    return

    # ---------------------------------------------------------------- síntese
    async def _edge_mp3(self, texto: str) -> bytes:
        import edge_tts

        com = edge_tts.Communicate(texto, self.voz, rate=self.velocidade, pitch=self.tom, volume=self.volume)
        buf = bytearray()
        async for parte in com.stream():
            if parte.get("type") == "audio":
                buf += parte["data"]
        return bytes(buf)

    def _sintetizar_edge(self, texto: str) -> tuple[np.ndarray, int]:
        import miniaudio

        mp3 = b""
        for tentativa in (1, 2):  # o servidor da Microsoft às vezes derruba a conexão ("closing transport")
            try:
                mp3 = asyncio.run(self._edge_mp3(texto))
                break
            except Exception as e:  # noqa: BLE001
                if tentativa == 2 or "403" in str(e):  # 403 = edge-tts desatualizado: insistir não adianta
                    raise
                log.info("Edge TTS falhou (%s); tentando de novo", type(e).__name__)
                time.sleep(0.4)
        if not mp3:
            raise RuntimeError("Edge TTS devolveu áudio vazio")
        dec = miniaudio.decode(mp3, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1, sample_rate=24000)
        return aparar_silencio(np.frombuffer(dec.samples.tobytes(), dtype=np.int16), 24000), 24000

    def _sintetizar_sapi(self, texto: str) -> tuple[np.ndarray, int]:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        try:
            arq = os.path.join(tempfile.gettempdir(), f"jarvis_sapi_{threading.get_ident()}.wav")
            voz = win32com.client.Dispatch("SAPI.SpVoice")
            for v in voz.GetVoices():
                desc = v.GetDescription()
                if "Portug" in desc or "Brazil" in desc or "Maria" in desc or "Daniel" in desc:
                    voz.Voice = v
                    break
            fluxo = win32com.client.Dispatch("SAPI.SpFileStream")
            fluxo.Open(arq, 3)
            voz.AudioOutputStream = fluxo
            voz.Speak(texto)
            fluxo.Close()
            with wave.open(arq, "rb") as w:
                taxa = w.getframerate()
                canais = w.getnchannels()
                dados = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
            if canais > 1:
                dados = dados.reshape(-1, canais).mean(axis=1).astype(np.int16)
            try:
                os.remove(arq)
            except OSError:
                pass
            return dados, taxa
        finally:
            pythoncom.CoUninitialize()

    def sintetizar(self, texto: str) -> tuple[np.ndarray, int]:
        if texto in self._cache:
            self._cache.move_to_end(texto)
            return self._cache[texto]
        try:
            a = self._sintetizar_edge(texto)
            self.ultimo_motor = "edge"
            if len(texto) <= self._cache_max_chars:  # só a voz neural vai para o cache (a do Windows é instantânea)
                self._cache[texto] = a
                while len(self._cache) > self._cache_max_itens:
                    self._cache.popitem(last=False)
            return a
        except Exception as e:  # noqa: BLE001
            log.warning("Edge TTS falhou (%s); usando a voz do Windows", e)
            a = self._sintetizar_sapi(texto)
            self.ultimo_motor = "sapi"
            return a

    # ---------------------------------------------------------------- reprodução
    def _tocar(self, audio: np.ndarray, taxa: int, cancelar: threading.Event) -> None:
        if len(audio) == 0:
            return
        sd.play(audio, taxa)
        dur = len(audio) / taxa
        janela = int(taxa * 0.05)
        t0 = time.monotonic()
        try:
            while True:
                passado = time.monotonic() - t0
                if passado >= dur + 0.08:
                    break
                if cancelar.is_set():
                    sd.stop()
                    return
                pos = int(passado * taxa)
                self.ao_nivel(min(1.0, rms(audio[pos:pos + janela]) / 5500.0))
                time.sleep(0.025)
        finally:
            self.ao_nivel(0.0)

    def falar(self, texto: str, cancelar: threading.Event) -> None:
        """Fala o texto inteiro (bloqueia). Sintetiza o próximo trecho enquanto toca o atual."""
        fila: queue.Queue = queue.Queue()
        for trecho in dividir_frases(texto, 170):  # ~4 linhas no popup
            fila.put(trecho)
        fila.put(None)
        self.falar_fila(fila, cancelar)

    def falar_fila(self, fila: queue.Queue, cancelar: threading.Event) -> bool:
        """Fala os trechos da fila conforme chegam (None = acabou). Enquanto um toca, o próximo que já chegou é
        sintetizado. Devolve True se falou alguma coisa."""
        vazio = object()

        def pegar():
            while not cancelar.is_set():
                try:
                    return fila.get(timeout=0.1)
                except queue.Empty:
                    continue
            return None

        atual = pegar()
        futuro = self._pool.submit(self.sintetizar, atual) if atual is not None else None
        falou = False
        while atual is not None:
            try:
                audio, taxa = futuro.result(timeout=30)
            except Exception as e:  # noqa: BLE001
                log.error("Não consegui sintetizar a fala: %s", e)
                audio, taxa = None, 0
            proximo, prox_futuro = vazio, None
            try:  # o próximo trecho já chegou? começa a sintetizar enquanto este toca
                proximo = fila.get_nowait()
                prox_futuro = self._pool.submit(self.sintetizar, proximo) if proximo is not None else None
            except queue.Empty:
                pass
            if cancelar.is_set():
                return falou
            if audio is not None:
                self.ao_trecho(atual)
                self._tocar(audio, taxa, cancelar)
                falou = True
            if cancelar.is_set():
                return falou
            if proximo is vazio:
                proximo = pegar()
                prox_futuro = self._pool.submit(self.sintetizar, proximo) if proximo is not None else None
            atual, futuro = proximo, prox_futuro
        return falou
