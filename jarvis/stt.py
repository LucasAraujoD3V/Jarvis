"""
Transcrição da voz (frase do microfone → texto).

Dois jeitos, os dois grátis e sem chave:
- Whisper local (faster-whisper): roda no PC, sem internet, entende bem voz baixa e com ruído. Na placa NVIDIA
  (CUDA) leva ~0,5 s por frase; só na CPU, o modelo "small" leva ~4–6 s (medido num i7-1360P em 22/11/2026).
- Google (SpeechRecognition): rápido (~1 s), precisa de internet e falha em voz fraca (devolve "não entendi").

Modo "auto" (padrão, config.json → transcricao.motor):
  GPU NVIDIA funcionando → Whisper na GPU é o principal.
  Sem GPU → Google é o principal e o Whisper na CPU entra quando o Google não entende a frase (antes ela se perdia).
A GPU é testada num PROCESSO SEPARADO (falta de DLL do CUDA pode derrubar o processo inteiro); o resultado fica salvo
em preferencias.json → whisper_cuda e só é refeito se a versão do ctranslate2 mudar.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

import numpy as np

log = logging.getLogger("jarvis.stt")

TAXA = 16000
SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Frases que o Whisper "inventa" em silêncio/ruído (vêm das legendas com que ele foi treinado).
ALUCINACOES = (
    "legendas pela comunidade", "amara.org", "obrigado por assistir", "obrigada por assistir", "inscreva-se",
    "se inscreva", "ative o sininho", "legendado por", "legendas por", "tradução e legendas", "transcrição por",
    "subtitles by", "thanks for watching", "e aí pessoal", "até o próximo vídeo", "até a próxima",
)


def normalizar(audio: np.ndarray, pico_alvo: float = 0.6, ganho_max: float = 400.0) -> np.ndarray:
    """int16 (ou float) → float32 em [-1, 1] com o pico em `pico_alvo`. O microfone do PC fixo manda a fala com rms ~10–40
    (de 32768): sem isso a voz chega quase no nível do chiado."""
    a = audio.astype(np.float32)
    if audio.dtype == np.int16:
        a /= 32768.0
    pico = float(np.max(np.abs(a))) if len(a) else 0.0
    if pico <= 1e-6:
        return a
    return np.clip(a * min(pico_alvo / pico, ganho_max), -1.0, 1.0).astype(np.float32)


def para_int16(audio_float: np.ndarray) -> np.ndarray:
    return (np.clip(audio_float, -1.0, 1.0) * 32767).astype(np.int16)


def e_alucinacao(texto: str) -> bool:
    t = (texto or "").strip().lower()
    if not t:
        return True
    if any(a in t for a in ALUCINACOES):
        return True
    return not re.search(r"[a-zà-ú0-9]", t)  # só pontuação ("...", "!")


def juntar_segmentos(segmentos) -> str:
    """Texto dos segmentos que parecem fala de verdade (descarta silêncio transcrito e alucinação)."""
    partes = []
    for s in segmentos:
        sem_fala = getattr(s, "no_speech_prob", 0.0) or 0.0
        confianca = getattr(s, "avg_logprob", 0.0) or 0.0
        if sem_fala > 0.6 and confianca < -1.0:
            continue
        t = (s.text or "").strip()
        if t and not e_alucinacao(t):
            partes.append(t)
    return " ".join(partes).strip()


# ----------------------------------------------------------------------------------------------- CUDA (NVIDIA)
def preparar_dlls_cuda() -> list[str]:
    """`pip install nvidia-cublas-cu12 nvidia-cudnn-cu12` põe as DLLs do CUDA em site-packages/nvidia/*/bin, fora do PATH
    do Windows. Registra essas pastas para o ctranslate2 achar. Devolve as pastas registradas."""
    pastas = []
    for base in {Path(p) for p in sys.path if p and p.endswith("site-packages")}:
        nvidia = base / "nvidia"
        if nvidia.is_dir():
            for bin_ in nvidia.glob("*/bin"):
                if bin_.is_dir():
                    pastas.append(str(bin_))
    for p in pastas:
        try:
            os.add_dll_directory(p)
        except (OSError, AttributeError):
            pass
    if pastas:
        os.environ["PATH"] = os.pathsep.join(pastas + [os.environ.get("PATH", "")])
    return pastas


_SONDA_CUDA = r"""
import sys
sys.path[:0] = [p for p in sys.argv[2:] if p]
from jarvis.stt import preparar_dlls_cuda
preparar_dlls_cuda()
import numpy as np, ctranslate2
if ctranslate2.get_cuda_device_count() < 1:
    print("SEM_GPU"); sys.exit(3)
from faster_whisper import WhisperModel
m = WhisperModel(sys.argv[1], device="cuda", compute_type="float16")
segs, _ = m.transcribe(np.zeros(16000, dtype=np.float32), language="pt", beam_size=1)
list(segs)
print("CUDA_OK")
"""


def sondar_cuda(modelo: str, raiz: Path, timeout_s: float = 600) -> tuple[bool, str]:
    """Carrega o Whisper na GPU e transcreve 1 s de silêncio num processo separado. (ok, detalhe)"""
    try:
        r = subprocess.run([sys.executable, "-c", _SONDA_CUDA, modelo, str(raiz)], capture_output=True, text=True,
                           timeout=timeout_s, cwd=str(raiz), creationflags=SEM_JANELA, encoding="utf-8", errors="replace")
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, f"sonda da GPU falhou: {e}"
    if "CUDA_OK" in (r.stdout or ""):
        return True, "GPU NVIDIA"
    if "SEM_GPU" in (r.stdout or ""):
        return False, "sem placa NVIDIA"
    erro = ((r.stderr or "").strip().splitlines() or [f"código {r.returncode}"])[-1]
    return False, erro[:200]


def versao_ct2() -> str:
    try:
        import ctranslate2

        return str(ctranslate2.__version__)
    except Exception:  # noqa: BLE001
        return ""


# ----------------------------------------------------------------------------------------------- Whisper
class Whisper:
    def __init__(self, modelo: str = "small", dispositivo: str = "cpu", threads: int = 0, dica: str = "Jarvis",
                 idioma: str = "pt", feixe: int = 1):
        self.modelo = modelo
        self.dispositivo = dispositivo
        self.threads = threads or min(8, os.cpu_count() or 4)  # 8 threads: 5,4 s → 3,9 s por frase (medido)
        self.dica = dica
        self.idioma = idioma
        self.feixe = feixe
        self._m = None
        self._trava = threading.Lock()

    @property
    def carregado(self) -> bool:
        return self._m is not None

    def carregar(self) -> None:
        with self._trava:
            if self._m is not None:
                return
            from faster_whisper import WhisperModel

            if self.dispositivo == "cuda":
                preparar_dlls_cuda()
                self._m = WhisperModel(self.modelo, device="cuda", compute_type="float16")
            else:
                self._m = WhisperModel(self.modelo, device="cpu", compute_type="int8", cpu_threads=self.threads)
            log.info("Whisper '%s' carregado (%s)", self.modelo, self.dispositivo)

    def transcrever(self, audio: np.ndarray) -> str:
        self.carregar()
        segs, _ = self._m.transcribe(
            normalizar(audio), language=self.idioma, beam_size=self.feixe, vad_filter=True,
            condition_on_previous_text=False, without_timestamps=True, hotwords=self.dica or None,
        )
        return juntar_segmentos(segs)


# ----------------------------------------------------------------------------------------------- estratégia
def whisper_instalado() -> bool:
    try:
        import faster_whisper  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


class Transcritor:
    """
    Decide e usa o melhor jeito de transcrever neste PC (ver topo do arquivo). config.json → transcricao:
      motor      "auto" | "whisper" | "google"
      modelo     modelo do Whisper ("small" padrão; "base" é 3x mais rápido na CPU e erra bem mais)
      gpu        "auto" (testa a NVIDIA) | "nao" (nunca usa a GPU)
      reserva    true = quando o principal não entende, o outro tenta (padrão true)
    """

    def __init__(self, cfg: dict, idioma: str, dica: str, ao_aviso: Callable[[str], None], prefs=None,
                 raiz: Path | None = None):
        self.cfg = cfg or {}
        self.idioma = idioma
        self.motor = str(self.cfg.get("motor", "auto")).lower()
        self.modelo = str(self.cfg.get("modelo", "small"))
        self.usar_gpu = str(self.cfg.get("gpu", "auto")).lower() != "nao"
        self.reserva = bool(self.cfg.get("reserva", True))
        self.dica = dica
        self.ao_aviso = ao_aviso
        self.prefs = prefs
        self.raiz = raiz or Path(__file__).resolve().parent.parent
        self.principal = "whisper" if self.motor == "whisper" else "google"  # até a preparação terminar
        self.whisper: Whisper | None = None
        self.descricao = "Google"
        self._google = None
        self._pronto = threading.Event()

    # ------------------------------------------------------------------ preparação (thread à parte, no início)
    def preparar(self) -> None:
        try:
            self._preparar()
        except Exception as e:  # noqa: BLE001
            log.exception("Preparação da transcrição falhou")
            self.ao_aviso(f"Whisper indisponível ({e}); usando só o Google.")
            self.principal, self.whisper, self.descricao = "google", None, "Google"
        finally:
            self._pronto.set()

    def _preparar(self) -> None:
        if self.motor == "google" and not self.reserva:
            return
        if not whisper_instalado():
            if self.motor != "google":
                self.ao_aviso("Whisper não instalado (pip install faster-whisper): transcrevendo só pelo Google.")
            self.principal, self.descricao = "google", "Google"
            return
        dispositivo = "cpu"
        if self.usar_gpu and self.motor != "google":
            dispositivo = "cuda" if self._gpu_ok() else "cpu"
        self.whisper = Whisper(self.modelo, dispositivo, dica=self.dica, idioma=self.idioma.split("-")[0])
        if self.motor == "whisper" or (self.motor == "auto" and dispositivo == "cuda"):
            self.principal = "whisper"
        else:
            self.principal = "google"
        self.whisper.carregar()  # já deixa na memória (1ª vez baixa o modelo: ~480 MB o "small")
        reserva = ("Whisper" if self.principal == "google" else "Google") if self.reserva else "nenhuma"
        self.descricao = (f"Whisper {self.modelo} ({'GPU' if dispositivo == 'cuda' else 'CPU'})"
                          if self.principal == "whisper" else "Google") + f", reserva: {reserva}"
        self.ao_aviso(f"Transcrição: {self.descricao}.")

    def _gpu_ok(self) -> bool:
        ct2 = versao_ct2()
        salvo = (self.prefs.get("whisper_cuda") if self.prefs is not None else None) or {}
        if salvo.get("ct2") == ct2 and salvo.get("modelo") == self.modelo and "ok" in salvo:
            return bool(salvo["ok"])
        self.ao_aviso("Testando a placa de vídeo para o Whisper (só na 1ª vez; pode baixar o modelo)…")
        ok, detalhe = sondar_cuda(self.modelo, self.raiz)
        log.info("Sonda CUDA do Whisper: %s (%s)", ok, detalhe)
        if self.prefs is not None:
            self.prefs.set("whisper_cuda", {"ok": ok, "ct2": ct2, "modelo": self.modelo, "detalhe": detalhe})
        return ok

    # ------------------------------------------------------------------ uso (thread de transcrição)
    def _por_google(self, audio: np.ndarray) -> str:
        import speech_recognition as sr

        if self._google is None:
            self._google = sr.Recognizer()
        dados = sr.AudioData(para_int16(normalizar(audio, 0.5)).tobytes(), TAXA, 2)
        try:
            return (self._google.recognize_google(dados, language=self.idioma) or "").strip()
        except sr.UnknownValueError:
            return ""
        except sr.RequestError as e:
            raise ConnectionError(str(e)) from None

    def transcrever(self, audio: np.ndarray) -> str:
        """Texto da frase ('' = não era fala / ninguém entendeu)."""
        ordem = [self.principal]
        if self.reserva and self.whisper is not None:
            ordem.append("whisper" if self.principal == "google" else "google")
        erro_rede = None
        for jeito in ordem:
            try:
                if jeito == "whisper":
                    if self.whisper is None:
                        continue
                    texto = self.whisper.transcrever(audio)
                else:
                    texto = self._por_google(audio)
            except ConnectionError as e:
                erro_rede = e
                continue
            except Exception:  # noqa: BLE001
                log.exception("Erro transcrevendo (%s)", jeito)
                continue
            if texto:
                if jeito != self.principal:
                    log.info("Transcrito pela reserva (%s): %s", jeito, texto)
                return texto
        if erro_rede is not None and (self.whisper is None or not self.reserva):
            self.ao_aviso(f"Transcrição indisponível (sem internet?): {erro_rede}")
        return ""
