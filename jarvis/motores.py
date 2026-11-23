"""
Motores de IA do Jarvis. Todos têm a mesma cara: `perguntar(texto, cancelar) -> str`.

- MotorNemotron: modelos da NVIDIA pela API hospedada grátis (build.nvidia.com), formato OpenAI. Serve para o
  Nemotron lightning (rápido, ~0,5 s) e para o modelo "pro" (o mais inteligente da lista grátis). Não tem internet
  nem acesso ao PC: quando precisa disso responde ENCAMINHAR e o Jarvis repassa a uma IA com ferramentas.
- MotorClaude: o Claude Code instalado no PC (`claude -p`), usando a CONTA JÁ LOGADA — sem chave de API. Tem
  ferramentas (arquivos, terminal, busca na web), então faz coisas de verdade. Mais lento (~5 s ou mais).
- MotorCLI: qualquer outra IA de linha de comando logada no PC (Gemini CLI, Codex CLI...), configurada em
  config.json → motores_extras. O pedido vai pela entrada padrão (stdin), junto com a memória e a conversa recente.

Todos recebem a MEMÓRIA (fatos que o usuário ensinou) e a CONVERSA recente (de qualquer IA): atributos `memoria` e
`conversa`, preenchidos pelo Assistente (ver memoria.py).
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from .texto import MARCA_TEXTO

log = logging.getLogger("jarvis.motores")

SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SINAL_ENCAMINHAR = "ENCAMINHAR"
SINAL_IGNORAR = "IGNORAR"  # a fala não era para o Jarvis (conversa com outra pessoa): ele fica quieto

DIAS = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]


class ErroMotor(Exception):
    pass


class Cancelado(Exception):
    pass


def agora_por_extenso() -> str:
    d = _dt.datetime.now()
    return f"{DIAS[d.weekday()]}, {d:%d/%m/%Y}, {d:%H:%M}"


def regras_voz(nome: str, persona: list[str]) -> str:
    """Prompt base de TODAS as IAs: quem o Jarvis é (persona do config.json) + regras de quem fala por voz."""
    linhas = [
        f"Você é {nome}, assistente pessoal por voz que vive no computador Windows do usuário.",
        *persona,
        "Responda sempre em português do Brasil.",
        "A FALA é lida em voz alta por síntese de voz, então nela: nada de markdown, listas, tabelas, emojis, código ou links.",
        "IMPORTANTE: isto é conversa falada, como o modo de voz do ChatGPT. Comece pela FALA: o que você diria em voz "
        "alta, curto e didático, como um bom professor explicaria (1 a 3 frases, umas 40 palavras), sem listas nem títulos. "
        f"Se o assunto merecer mais (explicação, passos, números, comparação, código), depois da fala escreva uma linha só "
        f"com {MARCA_TEXTO} e em seguida a versão ESCRITA completa e organizada (markdown, listas e detalhes à vontade): "
        f"ela aparece na tela e não é falada. Papo, ações e respostas simples: só a fala, sem {MARCA_TEXTO}.",
        "Escreva em letras normais, nunca tudo em maiúsculas. Horas no formato de 24 horas. Números por extenso só se forem curtos.",
        "Fale datas como gente ('hoje', '22 de novembro'), nunca em números com barra, e evite parênteses.",
        "Nem toda fala é um pedido. Comentário, reação ou elogio (ex.: 'agora sim hein', 'boa', 'show', 'nossa', "
        "'que legal', 'ficou bom') recebe resposta curta e natural, como uma pessoa responderia ('Que bom!', "
        "'Pois é.', 'Às ordens.'), levando em conta a conversa recente. Nunca responda 'não entendi o pedido' "
        "nem pergunte o que a pessoa quer quando for só um comentário.",
    ]
    return "\n".join(linhas)


# Só para quem tem ferramentas de verdade no PC (Claude Code).
REGRAS_ACAO = (
    "Você tem acesso de verdade a este computador: terminal, arquivos e internet. Quando pedirem para fazer algo, "
    "faça com as ferramentas em vez de explicar como se faz.",
    "Antes de uma tarefa que demora, diga em poucas palavras o que vai fazer ('Abrindo o Chrome.', 'Vou dar uma "
    "olhada.'), porque você é ouvido enquanto trabalha. Ao terminar, diga em uma frase o que fez e o resultado.",
    "Para abrir programas, sites, pastas ou arquivos no Windows use: "
    "powershell -NoProfile -Command \"Start-Process '<programa, caminho ou endereço>'\".",
    "Nada irreversível (apagar, enviar mensagem ou e-mail, publicar, comprar, mexer em servidor de produção) sem "
    "perguntar antes e ouvir um sim.",
    "Os pedidos chegam por voz (transcrição automática: pode haver palavras trocadas, interprete pelo sentido).",
    "Quando pesquisar na web, dê só a resposta: não liste fontes, links nem 'Sources' (tudo que você escreve é falado).",
    "Se não achar o que pediram em poucos segundos, pergunte em vez de vasculhar o disco inteiro: ele está esperando ouvir.",
)


def nota_seguimento(nome: str) -> str:
    """Vai junto do pedido quando a frase foi captada SEM o nome, na janela logo depois de uma resposta."""
    return (
        f"\n\n(Observação do sistema: esta frase foi captada pelo microfone sem o usuário dizer '{nome}', logo depois "
        "da sua última resposta. Pode ser continuação da conversa, um comentário, ou alguém falando com OUTRA pessoa "
        f"ou a TV. Se claramente não foi dita para você, responda exatamente e somente: {SINAL_IGNORAR})"
    )


def contexto_extra(motor, conversa_turnos: list[dict] | None = None) -> str:
    """Memória do usuário + (opcional) conversa recente, em texto, para ir no prompt."""
    partes = []
    mem = getattr(motor, "memoria", None)
    if mem is not None:
        bloco = mem.para_prompt()
        if bloco:
            partes.append(bloco)
    if conversa_turnos:
        from .memoria import Conversa

        partes.append(Conversa.como_texto(conversa_turnos))
    return "\n\n".join(partes)


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
    """Um modelo da API grátis da NVIDIA. `secao` = parte do config.json com url/modelo/etc. ("nemotron", "nvidia_pro")."""

    lento = False

    def __init__(self, cfg: dict, chave: str, nome_assistente: str, persona: list[str], turnos: int = 10,
                 nome: str = "nemotron", secao: str | None = None):
        c = cfg.get(secao or nome, {})
        self.nome = nome
        self.rotulo = c.get("rotulo") or "Nemotron"
        self.ativo = bool(c.get("ativo", True))
        self.url = str(c.get("url") or cfg.get("nemotron", {}).get("url") or "https://integrate.api.nvidia.com/v1").rstrip("/")
        self.modelo = c.get("modelo", "nvidia/nemotron-3.5-lightning-30b-a3b")
        # False = manda desligar o "pensar" (Nemotron/Qwen); True = manda ligar; None = não manda nada (outros modelos)
        self.raciocinio = c.get("raciocinio", False)
        self.temperatura = float(c.get("temperatura", 0.5))
        self.max_tokens = int(c.get("max_tokens", 700))
        self.timeout_s = float(c.get("timeout_s", 30))
        self.lento = bool(c.get("lento", False))
        self.chave = (chave or "").strip()
        self.nome_assistente = nome_assistente
        self.persona = persona
        self.turnos = turnos
        self.encaminhar = True  # o Assistente desliga isso quando não há IA com ferramentas ou o modo é fixo
        self.historico: list[dict] = []  # só usado quando não há `conversa` compartilhada (testes, uso avulso)
        self.memoria = None
        self.conversa = None
        self.ultimo_erro = ""

    @property
    def disponivel(self) -> bool:
        return self.ativo and bool(self.chave)

    @property
    def motivo(self) -> str:
        if not self.ativo:
            return "desativado no config.json"
        return "" if self.chave else "sem NVIDIA_API_KEY no .env"

    def nova_conversa(self) -> None:
        self.historico.clear()

    def _sistema(self) -> str:
        s = regras_voz(self.nome_assistente, self.persona) + f"\nAgora é {agora_por_extenso()} (horário de Brasília)."
        if self.encaminhar:
            s += (
                "\nVocê NÃO tem internet nem acesso ao computador. Se o pedido exigir isso (abrir ou mexer em programa, "
                "arquivo, pasta ou projeto; pesquisar na web; notícias, clima, cotações ou dado do dia; executar algo), "
                f"responda exatamente e somente: {SINAL_ENCAMINHAR}. Nunca diga que não tem internet: encaminhe."
                "\nConhecimento geral (história, ciência, geografia, população aproximada, contas, explicações) e "
                "comentários/papo você responde direto, sem encaminhar."
            )
        extra = contexto_extra(self)
        return s + ("\n\n" + extra if extra else "")

    def _historico(self) -> list[dict]:
        if self.conversa is not None:
            return self.conversa.mensagens(self.turnos)
        return self.historico[-2 * self.turnos:]

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
                raise ErroMotor(f"{self.rotulo} HTTP {e.code} {detalhe}".strip()) from None
            except urllib.error.URLError as e:
                raise ErroMotor(f"{self.rotulo} sem conexão ({e.reason})") from None
            except TimeoutError:
                raise ErroMotor(f"{self.rotulo} demorou mais de {int(self.timeout_s)} s") from None
        raise ErroMotor(f"{self.rotulo} ocupado")

    def perguntar(self, texto: str, cancelar: threading.Event) -> str:
        if not self.disponivel:
            raise ErroMotor(self.motivo)
        msgs = [{"role": "system", "content": self._sistema()}, *self._historico(), {"role": "user", "content": texto}]
        corpo = {"model": self.modelo, "messages": msgs, "temperature": self.temperatura, "max_tokens": self.max_tokens, "stream": False}
        if self.raciocinio is not None:
            corpo["chat_template_kwargs"] = {"enable_thinking": bool(self.raciocinio)}
        try:
            j = _esperar(lambda: self._post(corpo), cancelar, self.timeout_s + 5)
        except ErroMotor as e:
            self.ultimo_erro = str(e)
            raise
        try:
            msg = j["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            raise ErroMotor(f"{self.rotulo}: resposta em formato inesperado") from None
        resposta = _sem_pensamento(msg.get("content") or "")
        if not resposta:
            raise ErroMotor(f"{self.rotulo} respondeu vazio")
        letras = [c for c in resposta if c.isalpha()]
        if (len(letras) >= 4 and sum(c.isupper() for c in letras) / len(letras) > 0.8
                and resposta.strip().upper() not in (SINAL_ENCAMINHAR, SINAL_IGNORAR)):
            resposta = resposta[0].upper() + resposta[1:].lower()  # o modelo às vezes grita EM MAIÚSCULAS
        sinal = resposta.strip().upper()
        if self.conversa is None and not sinal.startswith((SINAL_ENCAMINHAR, SINAL_IGNORAR)):
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


class _Reiniciar(Exception):
    """O processo do Claude morreu ou a sessão antiga não abriu: sobe outro e tenta de novo (uma vez)."""

    def __init__(self, msg: str, sem_sessao: bool = False, ja_falou: bool = False):
        super().__init__(msg)
        self.sem_sessao = sem_sessao
        self.ja_falou = ja_falou


_ERRO_LOGIN = re.compile(r"authenticat|oauth|not logged|log ?in|/login|401|invalid api key", re.I)


class MotorClaude:
    """
    Claude Code logado no PC (assinatura, sem chave de API), em UM processo que fica aberto:
      claude -p --input-format stream-json --output-format stream-json --include-partial-messages ...
    Cada pedido é uma linha JSON na entrada; a resposta volta em pedaços (`ao_parcial`), então o Jarvis começa a falar
    a 1ª frase enquanto o resto ainda está sendo escrito. A sessão (`sessao`) sobrevive a reinício via `--resume`.
    Cancelar = matar o processo; o próximo pedido sobe outro já retomando a mesma sessão.
    """

    nome = "claude"
    rotulo = "Claude"
    lento = True
    fluxo = True  # entrega a resposta em pedaços (fala enquanto gera)

    def __init__(self, cfg: dict, pasta_trabalho: Path, nome_assistente: str, persona: list[str]):
        c = cfg.get("claude", {})
        self.modelo = c.get("modelo") or ""
        self.permissao = c.get("permissao") or "auto"
        self.usar_mcp = bool(c.get("usar_mcp", False))
        # --safe-mode: sem CLAUDE.md global, plugins, skills e hooks do Claude Code do usuário (são regras do chat de
        # programação: "commita e dá push", "descobre o repo pelo git"...). Medido em 22/11: contexto 51k → 32k tokens.
        self.modo_limpo = bool(c.get("modo_limpo", True))
        self.timeout_s = float(c.get("timeout_s", 300))
        self.pasta = pasta_trabalho
        self.exe = achar_claude()
        self.prefixo: list[str] | None = None  # testes: [python, claude_falso.py] no lugar do executável
        self._ajuda: str | None = None  # saída do `claude --help` (para só usar flags que essa versão conhece)
        self.nome_assistente = nome_assistente
        self.persona = persona
        self.sessao: str | None = None  # o Assistente grava em preferencias.json → claude_sessao (sobrevive a reinício)
        self.memoria = None
        self.conversa = None
        self.ultimo_erro = ""
        self._proc: subprocess.Popen | None = None
        self._eventos: queue.Queue | None = None
        self._trava = threading.RLock()
        self._mem_conhecida = ""  # memória que o processo atual já recebeu (no sistema ou numa mensagem)
        self._retomando = False   # processo atual subiu com --resume e ainda não respondeu nada
        self._sem_login_ate = 0.0

    # ------------------------------------------------------------------ estado
    @property
    def disponivel(self) -> bool:
        return bool(self.prefixo or self.exe) and time.monotonic() >= self._sem_login_ate

    @property
    def motivo(self) -> str:
        if not (self.prefixo or self.exe):
            return "comando 'claude' não encontrado (instale o Claude Code e faça login)"
        if time.monotonic() < self._sem_login_ate:
            return "Claude sem login (no terminal: claude, depois /login)"
        return ""

    def vivo(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def nova_conversa(self) -> None:
        self.sessao = None
        self.encerrar()

    def encerrar(self) -> None:
        proc, self._proc, self._eventos = self._proc, None, None
        if proc is None:
            return
        if proc.poll() is None:
            _matar_arvore(proc)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        try:
            proc.stdin.close()
        except (OSError, ValueError, AttributeError):
            pass

    def aquecer(self) -> None:
        """Sobe o processo antes do 1º pedido (a partida leva ~2 s)."""
        with self._trava:
            if self.disponivel and not self.vivo():
                try:
                    self._iniciar(retomar=True)
                except OSError as e:
                    log.warning("Não consegui abrir o Claude: %s", e)

    # ------------------------------------------------------------------ processo
    def _memoria_texto(self) -> str:
        return self.memoria.para_prompt() if self.memoria is not None else ""

    def _sistema(self) -> str:
        """Fixo enquanto o processo vive: quem ele é + regras de ação + memória do momento da partida."""
        s = (regras_voz(self.nome_assistente, self.persona) + "\n" + "\n".join(REGRAS_ACAO)
             + f"\nSua pasta atual é {self.pasta}: é onde ficam os projetos do usuário. Pasta ou arquivo que ele citar "
             "(ex.: 'o projeto Kairos', 'a pasta x/y') está ali dentro: rode `ls` na pasta atual ANTES de qualquer busca. "
             "Nunca comece com `find ~` ou busca no disco inteiro; só procure fora se ele pedir.")
        conteudo = self._conteudo_pasta()
        if conteudo:
            s += f"\nO que tem na pasta atual agora (caminhos relativos a ela): {conteudo}."
        mem = self._memoria_texto()
        return s + ("\n\n" + mem if mem else "")

    def _conteudo_pasta(self, limite: int = 80) -> str:
        """Nível de cima da pasta de trabalho (pastas primeiro). Num teste real, só a regra "olhe a pasta atual" não
        bastou: o Claude saiu varrendo o disco (174 s). Com a lista no prompt ele já sabe onde está cada projeto."""
        try:
            itens = [p for p in self.pasta.iterdir() if not p.name.startswith((".", "__"))]
        except OSError:
            return ""
        itens.sort(key=lambda p: (not p.is_dir(), p.name.lower()))
        nomes = [p.name + ("/" if p.is_dir() else "") for p in itens[:limite]]
        if len(itens) > limite:
            nomes.append(f"... e mais {len(itens) - limite}")
        return ", ".join(nomes)

    def suporta(self, flag: str) -> bool:
        """Essa versão do Claude Code conhece a flag? (uma flag desconhecida faria o processo sair na hora)"""
        if self.prefixo:
            return True
        if self._ajuda is None:
            try:
                r = subprocess.run([self.exe, "--help"], capture_output=True, text=True, timeout=30,
                                   creationflags=SEM_JANELA, encoding="utf-8", errors="replace")
                self._ajuda = (r.stdout or "") + (r.stderr or "")
            except (OSError, subprocess.TimeoutExpired):
                self._ajuda = ""
        return flag in self._ajuda

    def _comando(self, retomar: bool) -> list[str]:
        args = [*(self.prefixo or [self.exe]), "-p", "--input-format", "stream-json", "--output-format", "stream-json",
                "--verbose", "--include-partial-messages", "--permission-mode", self.permissao,
                "--append-system-prompt", self._sistema()]
        if self.modelo:
            args += ["--model", self.modelo]
        if not self.usar_mcp:
            args += ["--strict-mcp-config"]
        if self.modo_limpo and self.suporta("--safe-mode"):
            args += ["--safe-mode"]
        if retomar and self.sessao:
            args += ["--resume", self.sessao]
        return args

    def _iniciar(self, retomar: bool) -> None:
        self.encerrar()
        args = self._comando(retomar)
        proc = subprocess.Popen(args, cwd=str(self.pasta), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, creationflags=SEM_JANELA, text=True, encoding="utf-8",
                                errors="replace", bufsize=1)
        eventos: queue.Queue = queue.Queue()
        threading.Thread(target=self._ler_saida, args=(proc, eventos), name="jarvis-claude-saida", daemon=True).start()
        threading.Thread(target=self._ler_erros, args=(proc,), name="jarvis-claude-erros", daemon=True).start()
        self._proc, self._eventos = proc, eventos
        self._mem_conhecida = self._memoria_texto()
        self._retomando = bool(retomar and self.sessao)
        log.info("Claude aberto (processo %s%s)", proc.pid, f", retomando {self.sessao}" if self._retomando else "")

    @staticmethod
    def _ler_saida(proc: subprocess.Popen, eventos: queue.Queue) -> None:
        try:
            for linha in proc.stdout:
                linha = linha.strip()
                if linha.startswith("{"):
                    try:
                        eventos.put(json.loads(linha))
                    except json.JSONDecodeError:
                        pass
        except (OSError, ValueError):
            pass
        finally:
            try:
                proc.stdout.close()
            except (OSError, ValueError):
                pass
        eventos.put(None)  # o processo terminou

    @staticmethod
    def _ler_erros(proc: subprocess.Popen) -> None:
        try:
            for linha in proc.stderr:
                if linha.strip():
                    log.debug("claude stderr: %s", linha.rstrip()[:300])
        except (OSError, ValueError):
            pass
        finally:
            try:
                proc.stderr.close()
            except (OSError, ValueError):
                pass

    def _mensagem(self, texto: str) -> str:
        """O pedido + o que mudou desde que o processo subiu (hora, memória nova, trocas feitas por outras IAs)."""
        partes = [f"(Agora é {agora_por_extenso()}.)"]
        mem = self._memoria_texto()
        if mem != self._mem_conhecida:
            partes.append("(A memória sobre o usuário mudou. Vale esta versão:)\n" + mem if mem
                          else "(O usuário apagou a memória sobre ele.)")
            self._mem_conhecida = mem
        if self.conversa is not None:
            # a sessão do Claude já tem as trocas que ELE respondeu; aqui vão só as que outras IAs responderam depois
            from .memoria import Conversa

            turnos = self.conversa.recente(6, depois_do_motor=self.nome if self.sessao else None)
            if turnos:
                partes.append(Conversa.como_texto(turnos))
        partes.append(texto)
        return "\n\n".join(partes)

    # ------------------------------------------------------------------ pedido
    def perguntar(self, texto: str, cancelar: threading.Event, ao_parcial=None, ao_ferramenta=None) -> str:
        """Bloqueia até o fim da resposta. `ao_parcial(texto)` recebe cada pedaço; `ao_ferramenta(nome)` cada ferramenta."""
        if not self.disponivel:
            raise ErroMotor(self.motivo)
        with self._trava:
            for tentativa in (1, 2):
                try:
                    if not self.vivo():
                        self._iniciar(retomar=True)
                    resposta = self._turno(texto, cancelar, ao_parcial, ao_ferramenta)
                    self.ultimo_erro = ""
                    return resposta
                except _Reiniciar as e:
                    log.warning("Claude: %s (tentativa %d)", e, tentativa)
                    self.encerrar()
                    if e.sem_sessao:
                        self.sessao = None
                    if tentativa == 2 or e.ja_falou:
                        self.ultimo_erro = str(e)
                        raise ErroMotor(f"Claude: {e}") from None
                except ErroMotor as e:
                    self.ultimo_erro = str(e)
                    raise
                except OSError as e:
                    self.ultimo_erro = str(e)
                    raise ErroMotor(f"Claude não abriu: {e}") from None
        raise ErroMotor("Claude: falhou duas vezes")

    def _turno(self, texto: str, cancelar: threading.Event, ao_parcial, ao_ferramenta) -> str:
        proc, eventos = self._proc, self._eventos
        linha = {"type": "user", "message": {"role": "user", "content": self._mensagem(texto)}}
        try:
            proc.stdin.write(json.dumps(linha, ensure_ascii=False) + "\n")
            proc.stdin.flush()
        except (OSError, ValueError, AttributeError):
            raise _Reiniciar("o processo fechou antes do pedido", sem_sessao=self._retomando) from None
        limite = time.monotonic() + self.timeout_s
        pedacos: list[str] = []
        while True:
            if cancelar.is_set():
                self.encerrar()
                raise Cancelado()
            if time.monotonic() > limite:
                self.encerrar()
                raise ErroMotor(f"Claude sem resposta em {int(self.timeout_s)} s")
            try:
                ev = eventos.get(timeout=0.1)
            except queue.Empty:
                continue
            if ev is None:
                raise _Reiniciar("o processo terminou no meio da resposta",
                                 sem_sessao=self._retomando and not pedacos, ja_falou=bool(pedacos))
            tipo = ev.get("type")
            if tipo == "system" and ev.get("subtype") == "init" and ev.get("session_id"):
                self.sessao = ev["session_id"]
            elif tipo == "stream_event" and not ev.get("parent_tool_use_id"):
                e = ev.get("event") or {}
                if e.get("type") == "content_block_start":
                    bloco = e.get("content_block") or {}
                    if bloco.get("type") == "tool_use" and ao_ferramenta:
                        ao_ferramenta(str(bloco.get("name") or ""))
                    elif bloco.get("type") == "text" and pedacos:
                        pedacos.append("\n")  # texto novo depois de uma ferramenta: separa da frase anterior
                        if ao_parcial:
                            ao_parcial("\n")
                elif e.get("type") == "content_block_delta" and (e.get("delta") or {}).get("type") == "text_delta":
                    t = e["delta"].get("text") or ""
                    if t:
                        pedacos.append(t)
                        if ao_parcial:
                            ao_parcial(t)
            elif tipo == "result":
                self.sessao = ev.get("session_id") or self.sessao
                if ev.get("is_error"):
                    msg = str(ev.get("result") or ev.get("subtype") or "erro")
                    if _ERRO_LOGIN.search(msg):
                        self._sem_login_ate = time.monotonic() + 300
                        self.encerrar()
                        raise ErroMotor("Claude sem login (no terminal: claude, depois /login)")
                    if self._retomando and not pedacos:
                        raise _Reiniciar(f"a sessão antiga não abriu ({msg[:120]})", sem_sessao=True)
                    raise ErroMotor(f"Claude: {msg[:200]}")
                self._retomando = False
                completo = "".join(pedacos).strip()
                return completo or str(ev.get("result") or "").strip() or "Feito."


# ===================================================================================================== CLI genérico
# Onde procurar CLIs do npm além do PATH. ATENÇÃO: o `npm install -g` rodado de dentro do app do Claude (pacote MSIX)
# cai numa pasta AppData VIRTUAL que o resto do Windows não enxerga; por isso Gemini/Codex foram instalados com
# `npm install -g --prefix %USERPROFILE%\.local\npm` (pasta de verdade).
PASTAS_CLI = (Path.home() / ".local" / "npm", Path(os.environ.get("APPDATA", "")) / "npm", Path.home() / ".local" / "bin")


def achar_cli(comando: str) -> str | None:
    exe = shutil.which(comando)
    if exe:
        return exe
    for pasta in PASTAS_CLI:
        for ext in (".cmd", ".exe", ""):
            p = pasta / f"{comando}{ext}"
            if p.is_file():
                return str(p)
    return None


def _ultimo_json(texto: str) -> dict | None:
    """O último objeto JSON da saída (CLIs às vezes escrevem avisos antes)."""
    texto = texto or ""
    i = texto.find("{")
    while i != -1:
        try:
            obj, _ = json.JSONDecoder().raw_decode(texto[i:])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        i = texto.find("{", i + 1)
    return None


class MotorCLI:
    """
    IA de linha de comando já logada no PC (Gemini CLI, Codex CLI...). Config (motores_extras.<nome>):
      comando     lista de argumentos. Se algum tiver "{prompt}", o pedido vai ali; senão vai pela entrada padrão (stdin)
      saida_json  campo do JSON de saída com a resposta (ex.: "response" no `gemini -o json`); vazio = texto puro
      pasta       pasta onde roda (padrão: a mesma do Claude)
      apelidos    nomes falados ("pergunta pro Gemini ...")
    Sem sessão própria: a memória e a conversa recente vão junto de cada pedido.
    """

    lento = True

    def __init__(self, nome: str, c: dict, pasta_trabalho: Path, nome_assistente: str = "Jarvis", persona: list[str] | None = None):
        self.nome = nome
        self.rotulo = c.get("rotulo") or nome.capitalize()
        self.comando = list(c.get("comando") or [])
        self.timeout_s = float(c.get("timeout_s", 300))
        self.ativo = bool(c.get("ativo", False))
        self.saida_json = c.get("saida_json") or ""
        self.pasta = Path(c["pasta"]) if c.get("pasta") else pasta_trabalho
        self.exe = achar_cli(self.comando[0]) if self.comando else None
        self.nome_assistente = nome_assistente
        self.persona = persona or []
        self.memoria = None
        self.conversa = None
        self.ultimo_erro = ""
        self._pausado_ate = 0.0  # sem login: sai da fila por um tempo, para não atrasar todo pedido
        # "logado" = este arquivo existe e contém este texto (ex.: ~/.gemini/settings.json com "selectedType").
        # Conferido a cada 30 s: depois do login a IA entra na fila sozinha, sem reiniciar o Jarvis.
        self.login_arquivo = Path(os.path.expanduser(c["login_arquivo"])) if c.get("login_arquivo") else None
        self.login_contem = c.get("login_contem") or ""
        self._login_ok = False
        self._login_visto = -1e9

    def logado(self) -> bool:
        if self.login_arquivo is None:
            return True
        if time.monotonic() - self._login_visto > 30:
            self._login_visto = time.monotonic()
            try:
                self._login_ok = self.login_arquivo.is_file() and self.login_contem in self.login_arquivo.read_text(
                    encoding="utf-8", errors="replace")
            except OSError:
                self._login_ok = False
        return self._login_ok

    @property
    def disponivel(self) -> bool:
        return self.ativo and bool(self.exe) and time.monotonic() >= self._pausado_ate and self.logado()

    @property
    def motivo(self) -> str:
        if not self.ativo:
            return "desativado no config.json"
        if not self.exe:
            return f"comando '{self.comando[0] if self.comando else '?'}' não encontrado"
        if time.monotonic() < self._pausado_ate or not self.logado():
            return f"sem login (rode '{self.comando[0]}' no terminal uma vez)"
        return ""

    def nova_conversa(self) -> None:
        pass

    def _falhou(self, msg: str) -> ErroMotor:
        self.ultimo_erro = msg[:200]
        if re.search(r"auth method|not logged|log ?in|unauthenticated|401", msg, re.I):
            self._pausado_ate = time.monotonic() + 600
        return ErroMotor(f"{self.rotulo}: {self.ultimo_erro}")

    def montar_prompt(self, texto: str) -> str:
        turnos = self.conversa.recente(6) if self.conversa is not None else None
        partes = [
            regras_voz(self.nome_assistente, self.persona),
            f"Agora é {agora_por_extenso()}.",
            "O pedido chegou por VOZ (transcrição automática: pode ter palavras trocadas, interprete pelo sentido).",
            "Se algo for arriscado ou irreversível, não faça: explique e peça confirmação.",
        ]
        extra = contexto_extra(self, turnos)
        if extra:
            partes.append(extra)
        partes.append(f"Pedido do usuário: {texto}")
        return "\n\n".join(partes)

    def perguntar(self, texto: str, cancelar: threading.Event) -> str:
        if not self.disponivel:
            raise ErroMotor(self.motivo)
        prompt = self.montar_prompt(texto)
        pelo_argumento = any("{prompt}" in a for a in self.comando[1:])
        args = [self.exe] + [a.replace("{prompt}", prompt) for a in self.comando[1:]]
        env = {**os.environ, "NO_COLOR": "1", "TERM": "dumb"}
        proc = subprocess.Popen(args, cwd=str(self.pasta), stdin=subprocess.DEVNULL if pelo_argumento else subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=SEM_JANELA, text=True,
                                encoding="utf-8", errors="replace", env=env)
        entrada = None if pelo_argumento else prompt
        try:
            out, err = _esperar(lambda: proc.communicate(entrada), cancelar, self.timeout_s,
                                ao_cancelar=lambda: _matar_arvore(proc))
        except ErroMotor as e:
            self.ultimo_erro = str(e)
            raise ErroMotor(f"{self.rotulo}: {e}") from None
        resposta = (out or "").strip()
        if self.saida_json:
            dados = _ultimo_json(out) or _ultimo_json(err) or {}  # o Gemini manda o JSON de erro pelo stderr
            if dados.get("error"):
                erro = dados["error"]
                raise self._falhou(str(erro.get("message") if isinstance(erro, dict) else erro))
            resposta = str(dados.get(self.saida_json) or "").strip()
        if not resposta:
            raise self._falhou(((err or "").strip().splitlines() or ["sem resposta"])[-1])
        self.ultimo_erro = ""
        return resposta
