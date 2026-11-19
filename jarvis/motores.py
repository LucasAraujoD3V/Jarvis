"""
Motores de IA do Jarvis. Todos têm a mesma cara: `perguntar(texto, cancelar) -> str`.

- MotorNemotron: modelos Nemotron da NVIDIA pela API hospedada (build.nvidia.com), formato OpenAI. Rápido (~1 s com
  o raciocínio desligado). Não tem internet nem acesso ao PC: quando precisa disso responde ENCAMINHAR e o Jarvis
  repassa ao Claude (modo automático).
- MotorClaude: o Claude Code instalado no PC (`claude -p`), usando a CONTA JÁ LOGADA — sem chave de API. Tem
  ferramentas (arquivos, terminal, busca na web), então faz coisas de verdade. Mais lento (~5 s ou mais).
- MotorCLI: qualquer outra IA de linha de comando (codex, gemini...), configurada em config.json → motores_extras.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger("jarvis.motores")

SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SINAL_ENCAMINHAR = "ENCAMINHAR"

DIAS = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]


class ErroMotor(Exception):
    pass


class Cancelado(Exception):
    pass


def agora_por_extenso() -> str:
    d = _dt.datetime.now()
    return f"{DIAS[d.weekday()]}, {d:%d/%m/%Y}, {d:%H:%M}"


def regras_voz(nome: str, estilo: list[str]) -> str:
    linhas = [
        f"Você é {nome}, assistente de voz pessoal rodando no computador Windows do usuário.",
        "Responda sempre em português do Brasil.",
        "Sua resposta será lida em voz alta por síntese de voz, então: nada de markdown, listas, tabelas, emojis, código ou links.",
        "No máximo 3 frases, a não ser que peçam detalhes.",
        "Escreva em letras normais, nunca tudo em maiúsculas. Horas no formato de 24 horas. Números por extenso só se forem curtos.",
        *estilo,
    ]
    return "\n".join(linhas)


def _sem_pensamento(t: str) -> str:
    return re.sub(r"<think>[\s\S]*?</think>", "", t or "").strip()


def _matar_arvore(proc: subprocess.Popen) -> None:
    """Mata o processo e os filhos (o Claude pode ter aberto terminal/ferramentas)."""
    try:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, creationflags=SEM_JANELA, timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _esperar(func, cancelar: threading.Event, timeout_s: float, ao_cancelar=None):
    """Roda `func` numa thread e espera o resultado, saindo cedo se `cancelar` for acionado."""
    caixa: dict = {}

    def alvo():
        try:
            caixa["ok"] = func()
        except BaseException as e:  # noqa: BLE001 — repassado para quem chamou
            caixa["erro"] = e

    t = threading.Thread(target=alvo, daemon=True)
    t.start()
    limite = time.monotonic() + timeout_s
    while t.is_alive():
        if cancelar.is_set():
            if ao_cancelar:
                ao_cancelar()
            raise Cancelado()
        if time.monotonic() > limite:
            if ao_cancelar:
                ao_cancelar()
            raise ErroMotor(f"sem resposta em {int(timeout_s)} s")
        t.join(0.1)
    if "erro" in caixa:
        raise caixa["erro"]
    return caixa.get("ok")


# ===================================================================================================== Nemotron
class MotorNemotron:
    nome = "nemotron"
    rotulo = "Nemotron"
    lento = False

    def __init__(self, cfg: dict, chave: str, nome_assistente: str, estilo: list[str], turnos: int = 10):
        c = cfg.get("nemotron", {})
        self.url = str(c.get("url", "https://integrate.api.nvidia.com/v1")).rstrip("/")
        self.modelo = c.get("modelo", "nvidia/nemotron-3.5-lightning-30b-a3b")
        self.raciocinio = bool(c.get("raciocinio", False))
        self.temperatura = float(c.get("temperatura", 0.5))
        self.max_tokens = int(c.get("max_tokens", 700))
        self.timeout_s = float(c.get("timeout_s", 30))
        self.chave = (chave or "").strip()
        self.nome_assistente = nome_assistente
        self.estilo = estilo
        self.turnos = turnos
        self.encaminhar = True  # o Assistente desliga isso quando o Claude não está disponível ou o modo é fixo
        self.historico: list[dict] = []
        self.ultimo_erro = ""

    @property
    def disponivel(self) -> bool:
        return bool(self.chave)

    @property
    def motivo(self) -> str:
        return "" if self.chave else "sem NVIDIA_API_KEY no .env"

    def nova_conversa(self) -> None:
        self.historico.clear()

    def _sistema(self) -> str:
        s = regras_voz(self.nome_assistente, self.estilo) + f"\nAgora é {agora_por_extenso()} (horário de Brasília)."
        if self.encaminhar:
            s += (
                "\nVocê NÃO tem internet nem acesso ao computador. Se o pedido exigir isso (abrir ou mexer em programa, "
                "arquivo, pasta ou projeto; pesquisar na web; notícias, clima, cotações ou dado do dia; executar algo), "
                f"responda exatamente e somente: {SINAL_ENCAMINHAR}. Nunca diga que não tem internet: encaminhe."
                "\nConhecimento geral (história, ciência, geografia, população aproximada, contas, explicações) você responde direto."
            )
        return s

    def _post(self, corpo: dict) -> dict:
        req = urllib.request.Request(
            f"{self.url}/chat/completions",
            data=json.dumps(corpo).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.chave}", "Content-Type": "application/json", "Accept": "application/json"},
        )
        for tentativa in (1, 2):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and tentativa == 1:
                    time.sleep(1.5)
                    continue
                detalhe = {401: "chave inválida", 403: "chave sem permissão", 404: "modelo não encontrado",
                           410: "modelo descontinuado (troque nemotron.modelo no config.json)", 429: "limite de pedidos"}.get(e.code, "")
                raise ErroMotor(f"Nemotron HTTP {e.code} {detalhe}".strip()) from None
            except urllib.error.URLError as e:
                raise ErroMotor(f"Nemotron sem conexão ({e.reason})") from None
        raise ErroMotor("Nemotron ocupado")

    def perguntar(self, texto: str, cancelar: threading.Event) -> str:
        if not self.disponivel:
            raise ErroMotor(self.motivo)
        msgs = [{"role": "system", "content": self._sistema()}, *self.historico[-2 * self.turnos:], {"role": "user", "content": texto}]
        corpo = {"model": self.modelo, "messages": msgs, "temperature": self.temperatura, "max_tokens": self.max_tokens, "stream": False}
        if not self.raciocinio:
            corpo["chat_template_kwargs"] = {"enable_thinking": False}
        try:
            j = _esperar(lambda: self._post(corpo), cancelar, self.timeout_s + 5)
        except ErroMotor as e:
            self.ultimo_erro = str(e)
            raise
        try:
            msg = j["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            raise ErroMotor("Nemotron: resposta em formato inesperado") from None
        resposta = _sem_pensamento(msg.get("content") or "")
        if not resposta:
            raise ErroMotor("Nemotron respondeu vazio")
        letras = [c for c in resposta if c.isalpha()]
        if len(letras) >= 4 and sum(c.isupper() for c in letras) / len(letras) > 0.8 and resposta.upper() != SINAL_ENCAMINHAR:
            resposta = resposta[0].upper() + resposta[1:].lower()  # o modelo às vezes grita EM MAIÚSCULAS
        if not resposta.strip().upper().startswith(SINAL_ENCAMINHAR):
            self.historico += [{"role": "user", "content": texto}, {"role": "assistant", "content": resposta}]
            self.historico = self.historico[-2 * self.turnos:]
        self.ultimo_erro = ""
        return resposta


# ===================================================================================================== Claude Code
def achar_claude() -> str | None:
    exe = shutil.which("claude")
    if exe:
        return exe
    for p in (Path.home() / ".local" / "bin" / "claude.exe", Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd"):
        if p.exists():
            return str(p)
    return None


class MotorClaude:
    nome = "claude"
    rotulo = "Claude"
    lento = True

    def __init__(self, cfg: dict, pasta_trabalho: Path, nome_assistente: str, estilo: list[str]):
        c = cfg.get("claude", {})
        self.modelo = c.get("modelo") or ""
        self.permissao = c.get("permissao") or "auto"
        self.usar_mcp = bool(c.get("usar_mcp", False))
        self.timeout_s = float(c.get("timeout_s", 300))
        self.pasta = pasta_trabalho
        self.exe = achar_claude()
        self.nome_assistente = nome_assistente
        self.estilo = estilo
        self.sessao: str | None = None
        self.ultimo_erro = ""
        self._proc: subprocess.Popen | None = None

    @property
    def disponivel(self) -> bool:
        return bool(self.exe)

    @property
    def motivo(self) -> str:
        return "" if self.exe else "comando 'claude' não encontrado (instale o Claude Code e faça login)"

    def nova_conversa(self) -> None:
        self.sessao = None

    def _sistema(self) -> str:
        return (
            regras_voz(self.nome_assistente, self.estilo)
            + f"\nAgora é {agora_por_extenso()}."
            + "\nO pedido chegou por VOZ (transcrição automática: pode ter palavras trocadas, interprete pelo sentido)."
            + "\nSe executar ações, diga em uma frase o que fez e o resultado. Se algo for arriscado ou irreversível, não faça: explique e peça confirmação."
        )

    def _comando(self, texto: str, retomar: bool) -> list[str]:
        args = [self.exe, "-p", texto.lstrip("- "), "--output-format", "json", "--permission-mode", self.permissao,
                "--append-system-prompt", self._sistema()]
        if self.modelo:
            args += ["--model", self.modelo]
        if not self.usar_mcp:
            args += ["--strict-mcp-config"]
        if retomar and self.sessao:
            args += ["--resume", self.sessao]
        return args

    def _rodar(self, args: list[str], cancelar: threading.Event) -> dict:
        proc = subprocess.Popen(
            args, cwd=str(self.pasta), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=SEM_JANELA, text=True, encoding="utf-8", errors="replace",
        )
        self._proc = proc
        try:
            out, err = _esperar(proc.communicate, cancelar, self.timeout_s, ao_cancelar=lambda: _matar_arvore(proc))
        finally:
            self._proc = None
        dados = None
        for linha in reversed((out or "").strip().splitlines()):
            linha = linha.strip()
            if linha.startswith("{"):
                try:
                    dados = json.loads(linha)
                    break
                except json.JSONDecodeError:
                    continue
        if dados is None:
            raise ErroMotor(f"Claude saiu com código {proc.returncode}: {(err or out or '').strip()[:200]}")
        return dados

    def perguntar(self, texto: str, cancelar: threading.Event) -> str:
        if not self.disponivel:
            raise ErroMotor(self.motivo)
        try:
            dados = self._rodar(self._comando(texto, retomar=True), cancelar)
            if dados.get("is_error") and self.sessao:
                # sessão antiga sumiu/expirou: tenta de novo numa conversa nova
                log.warning("Claude falhou retomando a sessão %s; tentando conversa nova", self.sessao)
                self.sessao = None
                dados = self._rodar(self._comando(texto, retomar=False), cancelar)
        except ErroMotor as e:
            self.ultimo_erro = str(e)
            raise
        if dados.get("is_error"):
            self.ultimo_erro = str(dados.get("result") or dados.get("subtype") or "erro")[:200]
            raise ErroMotor(f"Claude: {self.ultimo_erro}")
        self.sessao = dados.get("session_id") or self.sessao
        self.ultimo_erro = ""
        return str(dados.get("result") or "").strip() or "Feito."


# ===================================================================================================== CLI genérico
class MotorCLI:
    lento = True

    def __init__(self, nome: str, c: dict, pasta_trabalho: Path):
        self.nome = nome
        self.rotulo = c.get("rotulo") or nome.capitalize()
        self.comando = list(c.get("comando") or [])
        self.timeout_s = float(c.get("timeout_s", 300))
        self.ativo = bool(c.get("ativo", False))
        self.pasta = pasta_trabalho
        self.exe = shutil.which(self.comando[0]) if self.comando else None
        self.ultimo_erro = ""

    @property
    def disponivel(self) -> bool:
        return self.ativo and bool(self.exe)

    @property
    def motivo(self) -> str:
        if not self.ativo:
            return "desativado no config.json"
        return "" if self.exe else f"comando '{self.comando[0] if self.comando else '?'}' não encontrado"

    def nova_conversa(self) -> None:
        pass

    def perguntar(self, texto: str, cancelar: threading.Event) -> str:
        if not self.disponivel:
            raise ErroMotor(self.motivo)
        args = [self.exe] + [a.replace("{prompt}", texto) for a in self.comando[1:]]
        proc = subprocess.Popen(args, cwd=str(self.pasta), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, creationflags=SEM_JANELA, text=True, encoding="utf-8", errors="replace")
        out, err = _esperar(proc.communicate, cancelar, self.timeout_s, ao_cancelar=lambda: _matar_arvore(proc))
        if proc.returncode != 0 and not (out or "").strip():
            self.ultimo_erro = (err or "").strip()[:200]
            raise ErroMotor(f"{self.rotulo}: {self.ultimo_erro or 'falhou'}")
        return (out or "").strip() or "Feito."
