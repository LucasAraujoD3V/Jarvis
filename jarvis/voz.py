"""
Áudio do Jarvis.

Ouvinte: microfone contínuo (blocos de 30 ms) → detector de fala por energia com limiar que se adapta ao ruído →
cada frase vira um áudio → transcrição (Google, pt-BR) → callback com o texto. Fica MUDO enquanto o Jarvis fala,
para não ouvir a própria voz.

Falante: Edge TTS (voz neural, precisa de internet) decodificado em memória e tocado pelo sounddevice, medindo o
volume a cada instante — é isso que faz a animação "falar" no ritmo da voz. Sem internet, cai na voz do Windows (SAPI).
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
import speech_recognition as sr

from .texto import dividir_frases

log = logging.getLogger("jarvis.voz")

TAXA_MIC = 16000
BLOCO_S = 0.03
BLOCO = int(TAXA_MIC * BLOCO_S)


def rms(a: np.ndarray) -> float:
    if a is None or len(a) == 0:
        return 0.0
    return float(np.sqrt(np.mean(a.astype(np.float32) ** 2)))


# ===================================================================================================== Ouvinte
class Ouvinte:
    def __init__(self, cfg_mic: dict, idioma: str, ao_texto: Callable[[str], None], ao_nivel: Callable[[float], None],
                 ao_aviso: Callable[[str], None]):
        self.cfg = cfg_mic
        self.idioma = idioma
        self.ao_texto = ao_texto
        self.ao_nivel = ao_nivel
        self.ao_aviso = ao_aviso
        self.limiar_minimo = float(cfg_mic.get("limiar_minimo", 180))
        self.fator = float(cfg_mic.get("fator_ruido", 2.6))
        self.silencio_fim = float(cfg_mic.get("silencio_fim_s", 0.9))
        self.max_frase = float(cfg_mic.get("max_frase_s", 15))
        self.fala_minima = float(cfg_mic.get("fala_minima_s", 0.3))
        self.limiar = self.limiar_minimo
        self.piso = 0.0
        self.nome_microfone = ""
        self.sem_sinal = False
        self.ouvindo_frase = False
        self._mudo_ate = 0.0
        self._mudo = False
        self._ativo = threading.Event()
        self._stream: sd.InputStream | None = None
        self._audio: queue.Queue = queue.Queue(maxsize=400)
        self._frases: queue.Queue = queue.Queue(maxsize=8)
        self._rec = sr.Recognizer()
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
        dev = self.cfg.get("dispositivo")
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
        blocos: list[np.ndarray] = []
        voz_s = silencio_s = 0.0
        contador = 0
        pico_janela = 0.0  # vigia de microfone mudo: maior volume nos últimos ~10 s
        self.sem_sinal = False
        while self._ativo.is_set():
            try:
                bloco = self._audio.get(timeout=0.5)
            except queue.Empty:
                continue
            if bloco is None:
                break
            v = rms(bloco)
            historico.append(v)
            contador += 1
            pico_janela = max(pico_janela, v)
            if contador % int(10 / BLOCO_S) == 0:
                mudo_agora = pico_janela < 3.0  # silêncio digital: nada plugado, mudo no Windows ou microfone virtual parado
                if mudo_agora != self.sem_sinal:
                    self.sem_sinal = mudo_agora
                    self.ao_aviso(
                        f"Microfone sem sinal ({self.nome_microfone}): desconectado ou mudo. Conecte/ative um microfone "
                        "e clique em Reiniciar, ou escolha outro em config.json → microfone.dispositivo (ex.: \"Camo\")."
                        if mudo_agora else "Microfone voltou a captar som.")
                pico_janela = 0.0
            if contador % 10 == 0 and len(historico) > 20:
                self.piso = float(np.percentile(np.fromiter(historico, dtype=np.float32), 20))
                self.limiar = max(self.limiar_minimo, self.piso * self.fator)
            self.ao_nivel(max(0.0, min(1.0, (v - self.piso) / (self.limiar * 3))))

            if self.mudo:
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
            if silencio_s >= self.silencio_fim or dur >= self.max_frase:
                if voz_s >= self.fala_minima:
                    try:
                        self._frases.put_nowait(np.concatenate(blocos))
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
            dados = sr.AudioData(audio.tobytes(), TAXA_MIC, 2)
            try:
                texto = self._rec.recognize_google(dados, language=self.idioma)
            except sr.UnknownValueError:
                continue
            except sr.RequestError as e:
                self.ao_aviso(f"Transcrição indisponível (sem internet?): {e}")
                continue
            except Exception as e:  # noqa: BLE001
                log.exception("Erro na transcrição")
                self.ao_aviso(f"Erro na transcrição: {e}")
                continue
            texto = (texto or "").strip()
            if texto:
                self.ao_texto(texto)


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

        mp3 = asyncio.run(self._edge_mp3(texto))
        if not mp3:
            raise RuntimeError("Edge TTS devolveu áudio vazio")
        dec = miniaudio.decode(mp3, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1, sample_rate=24000)
        return np.frombuffer(dec.samples.tobytes(), dtype=np.int16).copy(), 24000

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
        try:
            a = self._sintetizar_edge(texto)
            self.ultimo_motor = "edge"
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
        trechos = dividir_frases(texto, 170)  # ~4 linhas no popup
        if not trechos:
            return
        futuro = self._pool.submit(self.sintetizar, trechos[0])
        for i, trecho in enumerate(trechos):
            try:
                audio, taxa = futuro.result(timeout=30)
            except Exception as e:  # noqa: BLE001
                log.error("Não consegui sintetizar a fala: %s", e)
                return
            if i + 1 < len(trechos):
                futuro = self._pool.submit(self.sintetizar, trechos[i + 1])
            if cancelar.is_set():
                return
            self.ao_trecho(trecho)
            self._tocar(audio, taxa, cancelar)
            if cancelar.is_set():
                return
