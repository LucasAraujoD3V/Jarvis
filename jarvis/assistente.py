"""
Núcleo do Jarvis: junta ouvido (Ouvinte), voz (Falante) e cérebros (motores de IA).

Fluxo:
  microfone → frase transcrita → tem "Jarvis"? (ou estamos na janela de conversa logo após uma resposta?)
     → comando local (cancelar, trocar motor, desligar...)  OU  pergunta para uma IA
     → resposta → limpa markdown → fala (popup animado acompanha o volume da voz)

Estados (sinal `estado`): desligado | ouvindo | atento | pensando | falando
  ouvindo  = esperando ouvir "Jarvis"
  atento   = acabou de responder / ouviu o nome: o próximo pedido não precisa de "Jarvis" (janela de seguimento)

Tudo que é lento roda em threads; a interface só recebe sinais Qt (seguros entre threads).
"""

from __future__ import annotations

import datetime as _dt
import logging
import queue
import random
import threading
import time

from PySide6.QtCore import QObject, Signal

from .config import RAIZ, caminho, ler_env
from .memoria import Conversa, Memoria
from .motores import (Cancelado, ErroMotor, MotorClaude, MotorCLI, MotorNemotron, SINAL_ENCAMINHAR, SINAL_IGNORAR,
                      nota_seguimento)
from .stt import Transcritor
from .texto import (ROTAS_PADRAO, DivisorFala, comando_local, fala_e_texto, comando_memoria, data_falada, extrair_comando,
                    hora_falada, limpar_para_fala, normalizar_token, pedido_de_motor, rotear)
from .voz import Falante, Ouvinte

JUNTAR_S = 4.0  # frase que chega até 4 s depois, com ele ainda pensando, é continuação da anterior
MOTORES_COM_FERRAMENTAS = ("claude", "gemini", "codex")  # para onde vai o que o modelo sem internet ENCAMINHAR

log = logging.getLogger("jarvis.assistente")

PREVIAS = {
    ("escrev", "digit", "redig"): "Escrevendo.",
    ("pesquis", "busc", "procur", "noticia", "clima", "cotac", "previs"): "Pesquisando.",
    ("cri", "ger", "mont"): "Criando.",
    ("corrig", "consert", "arrum"): "Corrigindo.",
    ("abr", "rod", "execut", "inici"): "Abrindo.",
    ("verific", "chec", "olh", "status"): "Verificando.",
}


def previa_para(texto: str) -> str:
    from .texto import palavras

    toks = palavras(texto)
    for radicais, frase in PREVIAS.items():
        if any(t.startswith(r) for t in toks for r in radicais):
            return frase
    return random.choice(["Certo. Um momento.", "Entendido. Um momento.", "Deixa comigo."])


def periodo_do_dia(hora: int) -> str:
    return "Bom dia" if 5 <= hora < 12 else "Boa tarde" if 12 <= hora < 18 else "Boa noite"


class FalaEmFluxo:
    """
    Fala a resposta ENQUANTO a IA ainda escreve (Claude em streaming). Uma por pedido.
    - Antes da 1ª ferramenta: cada frase pronta vai para a voz na hora. Segura a 1ª até saber que não é IGNORAR.
      Quando a 1ª ferramenta começa, fala na hora o que ele já tinha dito ("Vou procurar a pasta.") — antes a frase
      só saía quando chegava o próximo texto, depois da ferramenta (126 s num teste real). Se não tinha dito nada, fala
      a prévia ("Abrindo.").
    - Entre ferramentas: a narração ("a busca ficou lenta, vou tentar outro caminho") fica só no painel; só vira fala
      (a 1ª frase) se ele estiver há `SILENCIO_MAX_S` sem dizer nada, para mostrar que continua trabalhando.
    - Depois da última ferramenta: a resposta final é SEMPRE falada, com o limite de fala contando do zero (num teste
      real a narração gastou o limite e "São 15 arquivos" não foi dito).
    """

    SILENCIO_MAX_S = 10.0

    def __init__(self, assistente: "Assistente", previa: str = ""):
        self.a = assistente
        self.divisor = DivisorFala(assistente.max_fala)
        self.previa = previa
        self.fila: queue.Queue = queue.Queue()
        self.ignorar = False
        self.comecou = False
        self._decidido = False
        self._thread: threading.Thread | None = None
        self._usou_ferramenta = False
        self._bloco = ""  # texto escrito depois da última ferramenta (narração ou resposta final)
        self._ultima_fala = time.monotonic()

    def _enviar(self, frases: list[str]) -> None:
        for f in frases:
            if not self._decidido:
                self._decidido = True
                self.ignorar = f.strip().upper().startswith(SINAL_IGNORAR)
            if self.ignorar:
                return
            if self._thread is None:
                self.a.ouvinte.definir_mudo(True)
                self._thread = threading.Thread(target=self._falar, name="jarvis-fala-fluxo", daemon=True)
                self._thread.start()
            self.comecou = True
            self._ultima_fala = time.monotonic()
            self.fila.put(f)

    def parcial(self, pedaco: str) -> None:
        if self._usou_ferramenta:
            self._bloco += pedaco
        else:
            self._enviar(self.divisor.alimentar(pedaco))

    def ferramenta(self, _nome: str) -> None:
        if self.ignorar:
            return
        if not self._usou_ferramenta:
            self._usou_ferramenta = True
            self._enviar(self.divisor.terminar())  # o que ele disse antes da ferramenta sai agora, não depois dela
            if not self.comecou and self.previa:
                self._decidido = True
                self._enviar([self.previa])
            self.previa = ""
            return
        narracao, self._bloco = self._bloco, ""
        if narracao.strip() and time.monotonic() - self._ultima_fala >= self.SILENCIO_MAX_S:
            frases = DivisorFala(self.a.max_fala).alimentar(narracao + "\n")
            if frases:
                self._decidido = True
                self._enviar(frases[:1])  # "ainda estou nisso": só a 1ª frase da narração

    def terminar(self) -> bool:
        """A IA acabou: fala o que sobrou e espera a voz terminar. True = falou alguma coisa."""
        if self._usou_ferramenta:
            final = DivisorFala(self.a.max_fala)  # resposta final: limite de fala novo
            self._decidido = True
            self._enviar(final.alimentar(self._bloco) + final.terminar())
            cortado = final.cortado
        else:
            self._enviar(self.divisor.terminar())
            cortado = self.divisor.cortado
        if cortado and self.comecou:
            self.fila.put("O resto está no painel.")
        return self._fechar()

    def abortar(self) -> bool:
        """A IA falhou/foi cancelada: termina só o que já estava na fila. True = tinha falado algo."""
        return self._fechar()

    def _fechar(self) -> bool:
        self.fila.put(None)
        if self._thread is not None:
            self._thread.join()
        return self.comecou

    def _falar(self) -> None:
        try:
            self.a.falante.falar_fila(self.fila, self.a._cancelar)
        finally:
            self.a.ouvinte.definir_mudo(False)


class Assistente(QObject):
    estado = Signal(str)               # desligado | ouvindo | atento | pensando | falando
    nivel = Signal(float)              # 0..1 — volume da voz (falando) ou do microfone (atento)
    legenda = Signal(str, str)         # quem ("voce" | "jarvis" | "status"), texto
    registro = Signal(str, str, str)   # quem, texto, detalhe — histórico do painel
    detalhe = Signal(str)              # versão escrita completa da resposta (cartão ao lado do rosto)
    aviso = Signal(str)                # mensagens de status/erro
    motor_mudou = Signal(str)          # auto | claude | nemotron | ...
    pedir_reinicio = Signal()

    def __init__(self, cfg: dict, prefs):
        super().__init__()
        self.cfg = cfg
        self.prefs = prefs
        self.nome = cfg.get("nome", "Jarvis")
        self.usuario = cfg.get("usuario", "") or ""
        self.chaves = cfg.get("palavras_chave") or [self.nome.lower()]
        self.semelhanca = float(cfg.get("semelhanca_minima", 0.75))
        conv = cfg.get("conversa", {})
        self.janela_s = float(conv.get("janela_seguimento_s", 8))
        self.max_fala = int(conv.get("max_caracteres_fala", 600))
        self.reset_s = float(conv.get("nova_conversa_apos_min", 720)) * 60  # 0 = nunca
        self.previa = bool(conv.get("previa_quando_demora", True))
        turnos = int(conv.get("historico_turnos", 10))
        # quem o Jarvis é (config.json → persona). O antigo "estilo_resposta" (regras de economia de token pensadas para
        # o chat do Claude Code) NÃO é mais lido: deixava a fala robótica ("Eu corrigir código").
        persona = [self._formatar(linha) for linha in (cfg.get("persona") or [])]
        # pasta onde as IAs trabalham: lista de candidatas, vale a 1ª que existir neste PC (o Jarvis roda em mais de um
        # computador e a pasta Data fica em lugares diferentes)
        candidatas = cfg.get("claude", {}).get("pasta_trabalho", "..")
        candidatas = candidatas if isinstance(candidatas, list) else [candidatas]
        pasta = next((caminho(c) for c in candidatas if caminho(c).is_dir()), caminho(".."))

        # memória (fatos ensinados) e conversa (últimas trocas): em disco, valem para todas as IAs
        self.memoria = Memoria(RAIZ / "memoria.json")
        self.conversa = Conversa(RAIZ / "conversa.json", max_turnos=turnos, expira_s=self.reset_s)

        env = ler_env()
        chave = env.get("NVIDIA_API_KEY", "")
        self.motores = {"nemotron": MotorNemotron(cfg, chave, self.nome, persona, turnos)}
        if cfg.get("nvidia_pro"):
            self.motores["nvidia_pro"] = MotorNemotron(cfg, chave, self.nome, persona, turnos, nome="nvidia_pro")
        self.motores["claude"] = MotorClaude(cfg, pasta, self.nome, persona)
        for nome, c in (cfg.get("motores_extras") or {}).items():
            self.motores[nome] = MotorCLI(nome, c, pasta, self.nome, persona)
        for m in self.motores.values():
            m.memoria = self.memoria
            m.conversa = self.conversa
        self.rotas = cfg.get("roteamento") or {}
        # nomes falados → motor ("pergunta pro Kimi", "usa o Gemini")
        self.apelidos: dict[str, str] = {}
        for nome, m in self.motores.items():
            secao = (cfg.get("motores_extras") or {}).get(nome) or cfg.get(nome) or {}
            for falado in [nome, m.rotulo, *(secao.get("apelidos") or [])]:
                n = normalizar_token(falado)
                if n and n not in ("nvidiapro",):
                    self.apelidos.setdefault(n, nome)

        # continuidade: a sessão do Claude sobrevive ao reinício (preferencias.json → claude_sessao)
        if self.conversa.expirou():
            log.info("Conversa salva expirou (sem uso há mais de %.0f min): começando uma nova", self.reset_s / 60)
            self.conversa.limpar()
            prefs.set("claude_sessao", None)
        self.motores["claude"].sessao = prefs.get("claude_sessao") or None

        self.modo = prefs.get("motor") or cfg.get("motor_padrao", "auto")
        self.transcritor = Transcritor(cfg.get("transcricao", {}), cfg.get("idioma", "pt-BR"), self.nome, self._avisar,
                                       prefs=prefs, raiz=RAIZ)
        self._transcritor_preparado = False
        self.ouvinte = Ouvinte(cfg.get("microfone", {}), self.transcritor, self._ao_texto, self._nivel_mic, self._avisar,
                               ao_sem_sinal=self._ao_sem_sinal, preferido=prefs.get("microfone"))
        self._procura: threading.Thread | None = None
        self.falante = Falante(cfg.get("voz", {}), self._nivel_voz, self._ao_trecho)

        self._fila: queue.Queue = queue.Queue()
        self._cancelar = threading.Event()
        self._janela_ate = 0.0
        self._ultimo_uso = time.monotonic()
        self._estado = "desligado"
        self._ocupado = False
        self._ligado = False
        self._tentar_de_novo: threading.Timer | None = None
        threading.Thread(target=self._loop, name="jarvis-cerebro", daemon=True).start()
        self._dormindo = False  # "boa noite, Jarvis": só acorda quando ouvir o nome
        self._saudou = False  # a saudação completa ("Boa noite, Lucas. O que deseja?") é só a 1ª vez acordado
        self._pedido_atual = ""  # o que está sendo pensado agora (para juntar com a continuação da fala)
        self._pedido_t = 0.0
        self._aquecer()

    @staticmethod
    def _aquecer() -> None:
        """Importa as bibliotecas pesadas AQUI, na thread principal, antes das outras threads. Em 22/11/2026 (Python 3.14)
        importar edge_tts/yarl numa thread enquanto outra importava o Whisper derrubou o processo (0xc0000005 no GC)."""
        import importlib

        for mod in ("edge_tts", "aiohttp", "miniaudio", "speech_recognition", "faster_whisper"):
            try:
                importlib.import_module(mod)
            except Exception:  # noqa: BLE001
                pass

    def _formatar(self, texto: str) -> str:
        """Troca {usuario}, {nome} e {periodo} ('Bom dia'...) nas frases do config.json."""
        try:
            return str(texto).format(usuario=self.usuario or "você", nome=self.nome,
                                     periodo=periodo_do_dia(_dt.datetime.now().hour))
        except (KeyError, IndexError, ValueError):
            return str(texto)

    def _frase_do_config(self, chave: str, padrao: str) -> str:
        v = self.cfg.get(chave) or padrao
        if isinstance(v, list):
            v = random.choice(v) if v else padrao
        return self._formatar(v)

    def _resposta_ativacao(self) -> str:
        """1ª vez acordado: saudação completa pelo horário. Depois: só "Sim?" / "Diga." (sem se apresentar de novo)."""
        if self._saudou:
            return self._frase_do_config("respostas_curtas", "Sim?")
        self._saudou = True
        return self._frase_do_config("respostas_ativacao", "Sim?")

    def encerrar(self) -> None:
        """Saída do programa: fecha o processo do Claude que fica aberto entre os pedidos."""
        for m in self.motores.values():
            if hasattr(m, "encerrar"):
                try:
                    m.encerrar()
                except Exception:  # noqa: BLE001
                    pass

    # ================================================================= controle (chamado pela interface)
    @property
    def ligado(self) -> bool:
        return self._ligado

    @property
    def estado_atual(self) -> str:
        return self._estado

    def motores_disponiveis(self) -> set[str]:
        return {n for n, m in self.motores.items() if m.disponivel}

    def ligar(self, saudar: bool = True) -> None:
        if self._ligado:
            return
        try:
            self.ouvinte.iniciar()
        except Exception as e:  # noqa: BLE001 — microfone ocupado/ausente: tenta de novo sozinho
            log.exception("Microfone indisponível")
            self._avisar(f"Microfone indisponível ({e}). Tento de novo em 10 s.")
            self._tentar_de_novo = threading.Timer(10, lambda: self.ligar(saudar))
            self._tentar_de_novo.daemon = True
            self._tentar_de_novo.start()
            return
        self._ligado = True
        self._definir_estado("ouvindo")
        if not self._transcritor_preparado:  # Whisper: testa a GPU e carrega o modelo sem travar o resto
            self._transcritor_preparado = True
            threading.Thread(target=self.transcritor.preparar, name="jarvis-stt-prepara", daemon=True).start()
        claude = self.motores["claude"]
        if claude.disponivel:  # já deixa o processo do Claude aberto: o 1º pedido não paga a partida (~2 s)
            threading.Thread(target=claude.aquecer, name="jarvis-claude-aquece", daemon=True).start()
        # frases curtas que ele mais fala já ficam sintetizadas (o Edge leva ~1–2 s por frase)
        curtas = [self._formatar(f) for f in (self.cfg.get("respostas_ativacao") or [])]
        curtas += list(PREVIAS.values()) + ["Ok.", "Anotado.", "Disponha.", "Às ordens."]
        threading.Thread(target=self.falante.preaquecer, args=(curtas,), name="jarvis-voz-aquece", daemon=True).start()
        disp = self.motores_disponiveis()
        self._avisar(f"Ligado. Microfone: {self.ouvinte.nome_microfone}. IAs: {', '.join(sorted(disp)) or 'nenhuma'}.")
        if not claude.disponivel:
            self._avisar(f"Claude indisponível: {claude.motivo}. Respondendo com as reservas.")
        if saudar and self.cfg.get("saudacao"):
            self.dizer(self._frase_do_config("saudacao", "{periodo}."), abrir_janela=False)

    def desligar(self) -> None:
        if self._tentar_de_novo:
            self._tentar_de_novo.cancel()
            self._tentar_de_novo = None
        self.parar()
        self.ouvinte.parar()
        self._ligado = False
        self._janela_ate = 0
        self._definir_estado("desligado")
        self._avisar("Desligado. Ligue de novo pela bandeja.")

    def parar(self) -> None:
        """Cala a boca e cancela o que estiver pensando."""
        self._esvaziar_fila()
        self._cancelar.set()

    def nova_conversa(self, avisar: bool = True) -> None:
        """Esquece a CONVERSA (não a memória de fatos ensinados)."""
        for m in self.motores.values():
            m.nova_conversa()
        self.conversa.limpar()
        self.prefs.set("claude_sessao", None)
        if avisar:
            self._avisar("Conversa nova: histórico esquecido (a memória do que você ensinou continua).")

    def definir_motor(self, modo: str) -> None:
        if modo != "auto" and modo not in self.motores:
            return
        self.modo = modo
        self.prefs.set("motor", modo)
        self.motor_mudou.emit(modo)
        self._avisar(f"Motor: {self.rotulo_motor(modo)}.")

    def rotulo_motor(self, modo: str) -> str:
        return "Automático" if modo == "auto" else self.motores[modo].rotulo if modo in self.motores else modo

    def recarregar_chave_nemotron(self) -> None:
        chave = ler_env().get("NVIDIA_API_KEY", "")
        for m in self.motores.values():
            if isinstance(m, MotorNemotron):
                m.chave = chave
        ok = self.motores["nemotron"].disponivel
        self._avisar("Chave do Nemotron carregada." if ok else "Nemotron continua sem chave.")

    def enviar_texto(self, texto: str) -> None:
        """Comando digitado no painel: dispensa a palavra de ativação."""
        texto = (texto or "").strip()
        if texto:
            self._tratar_comando(texto)

    def dizer(self, texto: str, abrir_janela: bool = False) -> None:
        self._fila.put(("falar", texto, abrir_janela))

    # ================================================================= callbacks do áudio
    def _avisar(self, msg: str) -> None:
        log.info(msg)
        self.aviso.emit(msg)

    # ================================================================= microfone sem som → procura outro
    def _ao_sem_sinal(self, sem: bool) -> None:
        if sem and (self._procura is None or not self._procura.is_alive()):
            self._procura = threading.Thread(target=self._procurar_microfone, name="jarvis-procura-mic", daemon=True)
            self._procura.start()

    def _procurar_microfone(self) -> None:
        """Enquanto o microfone atual estiver mudo, mede todas as entradas a cada ~20 s (processo separado, que enxerga
        fone Bluetooth recém-conectado). Achou uma com som → salva como preferida e reinicia o Jarvis para usá-la."""
        from .voz import escolher_microfone, sondar_microfones

        while self._ligado and self.ouvinte.sem_sinal:
            if self._estado in ("ouvindo", "atento"):
                try:
                    self.ouvinte.pausar_vigia(12)
                    sondas = sondar_microfones()
                    self.ouvinte.pausar_vigia(2)
                    log.info("Sonda de microfones: %s", ", ".join(f"{s['nome']}={s['rms']:.1f}" for s in sondas))
                except Exception as e:  # noqa: BLE001
                    log.warning("Não consegui medir os microfones: %s", e)
                    sondas = []
                melhor = escolher_microfone(sondas, self.ouvinte.nome_microfone)
                if melhor and self._ligado and self.ouvinte.sem_sinal:
                    self.prefs.set("microfone", {"nome": melhor["nome"], "api": melhor["api"]})
                    self.prefs.set("aviso_inicio", "Microfone conectado. Pode falar.")
                    self._avisar(f"Microfone com som encontrado: {melhor['nome']}. Reiniciando para usar ele.")
                    self.pedir_reinicio.emit()
                    return
            for _ in range(40):
                if not (self._ligado and self.ouvinte.sem_sinal):
                    return
                time.sleep(0.5)

    def _nivel_mic(self, v: float) -> None:
        if self._estado == "atento":
            self.nivel.emit(v * 0.6)

    def _nivel_voz(self, v: float) -> None:
        self.nivel.emit(v)

    def _ao_trecho(self, trecho: str) -> None:
        # só vira "falando" quando o áudio começa (a síntese leva ~1 s; até lá continua "pensando"/"atento")
        self._definir_estado("falando")
        self.legenda.emit("jarvis", trecho)

    def _janela_aberta(self) -> bool:
        return time.monotonic() < self._janela_ate

    def _abrir_janela(self) -> None:
        self._janela_ate = time.monotonic() + self.janela_s
        log.info("Janela de seguimento aberta (%.0f s)", self.janela_s)

    def _ao_texto(self, texto: str, durante_fala: bool = False) -> None:
        """Frase transcrita do microfone (thread de transcrição). durante_fala = captada enquanto ele falava."""
        if not self._ligado:
            return
        achou, resto = extrair_comando(texto, self.chaves, self.semelhanca)
        if durante_fala:  # enquanto ele fala só o nome vale (o resto é eco da própria voz): cala e escuta
            if not achou:
                return
            log.info("Interrompido por: %s", texto)
            self.parar()
        if self._dormindo:  # dormindo: só o nome acorda ("boa noite, Jarvis" de novo não)
            if not achou or comando_local(resto or "") == "desligar":
                return
            self._dormindo = False
            log.info("Acordou: %s", texto)
        if not achou and not self._janela_aberta():
            log.debug("Ignorado (sem palavra de ativação): %s", texto)
            return
        log.info("Ouviu: %s", texto)
        if achou and not resto:
            self._abrir_janela()
            self._definir_estado("atento")
            self.legenda.emit("voce", texto)
            self.dizer(self._resposta_ativacao(), abrir_janela=True)
            return
        # sem o nome = veio na janela de seguimento: pode ser comentário ou conversa com outra pessoa
        self._tratar_comando(resto if achou else texto, seguimento=not achou)

    def _motor_falado(self, falado: str | None) -> str | None:
        if not falado:
            return None
        if falado in ("automatico", "auto"):
            return "auto"
        return self.apelidos.get(normalizar_token(falado))

    def _tratar_comando(self, cmd: str, seguimento: bool = False) -> None:
        self._ultimo_uso_check()
        local = comando_local(cmd)
        if local is None:
            m = self._motor_falado(pedido_de_motor(cmd))
            if m:
                local = f"motor:{m}"
        memoria = None if local else comando_memoria(cmd)
        self.legenda.emit("voce", cmd)
        self.registro.emit("voce", cmd, "")
        if memoria:
            self._fila.put(("memoria", cmd, memoria))
        elif local == "cancelar":
            self.parar()
            self._janela_ate = 0
            self.dizer("Ok.")
        elif local == "encerrar":
            self.parar()
            self._janela_ate = 0
            self.dizer(random.choice(["Disponha.", "Às ordens.", "Por nada."]))
        elif local == "nova_conversa":
            self.nova_conversa()
            self.dizer("Conversa nova.", abrir_janela=True)
        elif local and local.startswith("motor:"):
            modo = local.split(":", 1)[1]
            if modo != "auto" and not self.motores[modo].disponivel:
                self.dizer(f"{self.motores[modo].rotulo} indisponível. {self.motores[modo].motivo}.")
            else:
                self.definir_motor(modo)
                self.dizer(f"Usando {self.rotulo_motor(modo)}.", abrir_janela=True)
        elif local in ("hora", "data"):
            agora = _dt.datetime.now()
            fala = (hora_falada(agora.hour, agora.minute) if local == "hora"
                    else data_falada(agora.weekday(), agora.day, agora.month))
            self.registro.emit("jarvis", fala, "local")
            self.conversa.adicionar(cmd, fala, "local")
            self.dizer(fala, abrir_janela=True)
        elif local == "saudacao":  # "bom dia, Jarvis" / "oi, Jarvis"
            self.dizer(self._resposta_ativacao(), abrir_janela=True)
        elif local == "desligar":  # "boa noite, Jarvis" / "desliga": dorme (o microfone segue ouvindo só o nome)
            self.parar()
            self._janela_ate = 0
            self._dormindo = True
            self._saudou = False  # ao acordar, saúda de novo
            self.dizer(self._formatar(random.choice(["{periodo}, {usuario}. Vou descansar. É só me chamar.",
                                                     "Até mais, {usuario}. Qualquer coisa, me chama."])))
        elif local == "reiniciar":
            self.parar()
            self._fila.put(("falar", "Reiniciando.", False))
            self._fila.put(("reiniciar",))
        else:
            # sem fila: pedido novo substitui o que ainda está sendo pensado. Se chegou logo depois (pausa no meio da
            # fala), junta com o anterior em vez de perder a 1ª parte.
            agora = time.monotonic()
            if self._ocupado and self._estado == "pensando" and agora - self._pedido_t < JUNTAR_S and self._pedido_atual:
                log.info("Continuação da fala: juntando com o pedido anterior")
                cmd = f"{self._pedido_atual} {cmd}"
            if self._ocupado:
                self.parar()
            self._pedido_atual, self._pedido_t = cmd, agora
            self._fila.put(("perguntar", cmd, seguimento))

    def _ultimo_uso_check(self) -> None:
        if self.conversa.expirou():
            self.nova_conversa(avisar=False)
            log.info("Conversa antiga expirou (sem uso há mais de %.0f min): começando uma nova", self.reset_s / 60)
        self._ultimo_uso = time.monotonic()

    # ================================================================= memória ("lembra que…", "esquece…")
    def _memoria(self, cmd: str, pedido: tuple[str, str]) -> None:
        acao, arg = pedido
        if acao == "lembrar":
            novo = self.memoria.lembrar(arg)
            log.info("Memória: %s %s", "aprendeu" if novo else "já sabia", arg)
            resposta = random.choice(["Anotado.", "Guardado.", "Pode deixar, anotado."]) if novo else "Isso eu já sabia."
        elif acao == "esquecer":
            apagados = self.memoria.esquecer(arg)
            log.info("Memória: esqueceu %s (pedido: %s)", apagados, arg)
            resposta = "Esquecido." if apagados else "Não achei isso na memória."
            if apagados:
                self.registro.emit("status", "Esqueci: " + " | ".join(apagados), "memória")
        elif acao == "esquecer_tudo":
            n = self.memoria.esquecer_tudo()
            log.info("Memória: apagou tudo (%d fatos; cópia em memoria.bak.json)", n)
            resposta = f"Memória apagada. Esqueci {n} {'coisa' if n == 1 else 'coisas'}." if n else "A memória já estava vazia."
        else:  # listar
            fatos = self.memoria.listar()
            if not fatos:
                resposta = "Ainda não sei nada sobre você. Diga: Jarvis, lembra que..."
            elif self.motores_disponiveis():
                # uma IA rápida conta em 2ª pessoa (a memória já vai no prompt dela)
                self.registro.emit("status", f"Memória ({len(fatos)}): " + " | ".join(fatos), "memória")
                self._perguntar(cmd + "\n\n(Responda com base só no que está em 'O que você sabe do usuário', falando "
                                "com ele em segunda pessoa, curto. Se forem muitas coisas, diga as principais.)",
                                forcar_categoria="rapido", pedido_original=cmd)
                return
            else:
                resposta = "Sei isto: " + " ".join(fatos)
        self.registro.emit("jarvis", resposta, "memória")
        self.conversa.adicionar(cmd, resposta, "memoria")
        self._falar(resposta, abrir_janela=True)

    # ================================================================= cérebro (uma coisa por vez)
    def _esvaziar_fila(self) -> None:
        try:
            while True:
                self._fila.get_nowait()
        except queue.Empty:
            pass

    def _definir_estado(self, e: str) -> None:
        if e != self._estado:
            self._estado = e
            self.estado.emit(e)

    def _estado_ocioso(self) -> str:
        if not self._ligado:
            return "desligado"
        if self._dormindo:
            return "dormindo"
        return "atento" if self._janela_aberta() else "ouvindo"

    def _loop(self) -> None:
        while True:
            try:
                item = self._fila.get(timeout=0.25)
            except queue.Empty:
                if not self._ocupado:
                    self._definir_estado(self._estado_ocioso())
                continue
            self._ocupado = True
            self._cancelar.clear()
            try:
                tipo = item[0]
                if tipo == "falar":
                    self._falar(item[1], abrir_janela=item[2])
                elif tipo == "perguntar":
                    self._perguntar(item[1], seguimento=item[2] if len(item) > 2 else False)
                elif tipo == "memoria":
                    self._memoria(item[1], item[2])
                elif tipo == "desligar":
                    self.desligar()
                elif tipo == "reiniciar":
                    self.pedir_reinicio.emit()
            except Exception as e:  # noqa: BLE001 — nunca deixa o cérebro morrer
                log.exception("Erro no cérebro")
                self._avisar(f"Erro: {e}")
            finally:
                self._ocupado = False
                if self._fila.empty():
                    self._definir_estado(self._estado_ocioso())

    def _falar(self, texto: str, abrir_janela: bool = True) -> None:
        if not texto:
            return
        self.ouvinte.definir_mudo(True)
        try:
            self.falante.falar(texto, self._cancelar)
        finally:
            self.ouvinte.definir_mudo(False)
        if abrir_janela and self._ligado and not self._cancelar.is_set():
            self._abrir_janela()

    def _falar_previa(self, texto: str) -> None:
        """Frase curta ("Pesquisando.") enquanto a IA lenta trabalha; depois volta a mostrar 'pensando'."""
        self._falar(texto, abrir_janela=False)
        if not self._cancelar.is_set():
            self._definir_estado("pensando")

    def _perguntar(self, cmd: str, seguimento: bool = False, forcar_categoria: str | None = None,
                   pedido_original: str | None = None) -> None:
        disp = self.motores_disponiveis()
        extras = [n for n in self.motores if n not in ("claude", "nemotron")]
        fila, texto, cat = rotear(cmd, self.modo, disp, extras, self.rotas, self.apelidos)
        if forcar_categoria and self.modo == "auto":
            preferidos = [n for n in {**ROTAS_PADRAO, **self.rotas}.get(forcar_categoria, []) if n in disp]
            fila = preferidos + [n for n in fila if n not in preferidos]
            cat = forcar_categoria
        if not fila:
            self._falar("Nenhuma IA disponível. Veja o painel.", abrir_janela=False)
            self._avisar("Nenhuma IA disponível: configure a chave do Nemotron ou instale/logue o Claude Code.")
            return
        log.info("Pedido (%s%s) → %s", cat, ", seguimento" if seguimento else "", " > ".join(fila))
        envio = texto + (nota_seguimento(self.nome) if seguimento else "")
        # papo rápido não ganha "Um momento." antes: a prévia é só para o que demora de verdade
        previa = previa_para(texto) if (self.previa and cat != "rapido") else ""
        resposta, motor, ms, fluxo = self._consultar(fila, envio, disp, previa)
        if resposta is None:
            return
        if resposta.strip().upper().startswith(SINAL_IGNORAR):
            if fluxo is not None:
                fluxo.abortar()
            log.info("Ignorado (%s achou que não era para mim): %s", motor.rotulo, texto)
            self.registro.emit("status", "Não era comigo: fiquei quieto", motor.rotulo)
            self._janela_ate = 0
            return
        log.info("Resposta %s (%.1f s): %s", motor.rotulo, ms / 1000, resposta[:300].replace("\n", " "))
        self.registro.emit("jarvis", resposta, f"{motor.rotulo} · {ms / 1000:.1f} s")
        _, escrito = fala_e_texto(resposta)
        if escrito:
            log.info("Texto completo no cartão (%d caracteres)", len(escrito))
            self.detalhe.emit(escrito)
        self.conversa.adicionar(pedido_original or texto, resposta, motor.nome)
        if motor.nome == "claude":
            self.prefs.set("claude_sessao", motor.sessao)
        if fluxo is not None and fluxo.terminar():
            # já falou enquanto a IA escrevia: só falta abrir a janela de seguimento
            if self._ligado and not self._cancelar.is_set():
                self._abrir_janela()
            return
        fala, cortado = limpar_para_fala(resposta, self.max_fala)
        if cortado:
            fala += " O resto está no painel."
        self._falar(fala or "Feito.", abrir_janela=True)

    def _consultar(self, fila: list[str], texto: str, disp: set[str], previa: str = ""):
        """Pergunta ao 1º motor da fila; se ele falhar, tenta o próximo.
        Devolve (resposta, motor, ms, fluxo) ou (None, ...). `fluxo` (FalaEmFluxo) = motor que fala enquanto gera;
        quem chama faz fluxo.terminar() para falar o fim e esperar a voz."""
        tentativas = list(fila)
        com_ferramentas = [n for n in MOTORES_COM_FERRAMENTAS if n in disp]
        for m in self.motores.values():
            if isinstance(m, MotorNemotron):
                m.encaminhar = self.modo == "auto" and bool(com_ferramentas)
        ultimo_erro = ""
        for i, n in enumerate(tentativas):
            motor = self.motores[n]
            self._definir_estado("pensando")
            self.legenda.emit("status", f"Pensando · {motor.rotulo}")
            fluxo = FalaEmFluxo(self, previa) if getattr(motor, "fluxo", False) else None
            previa_thread = None
            if fluxo is not None:
                previa = ""  # o fluxo fala a prévia só se a IA for usar ferramenta sem dizer nada antes
            elif motor.lento and previa:
                previa_thread = threading.Thread(target=self._falar_previa, args=(previa,), daemon=True)
                previa = ""  # uma prévia só, mesmo se cair para outra IA
                previa_thread.start()
            t0 = time.monotonic()
            try:
                if fluxo is not None:
                    resposta = motor.perguntar(texto, self._cancelar, ao_parcial=fluxo.parcial,
                                               ao_ferramenta=fluxo.ferramenta)
                else:
                    resposta = motor.perguntar(texto, self._cancelar)
            except Cancelado:
                log.info("Pedido cancelado")
                if fluxo is not None:
                    fluxo.abortar()
                return None, motor, 0, None
            except ErroMotor as e:
                ultimo_erro = str(e)
                log.warning("%s falhou: %s", motor.rotulo, e)
                self._avisar(f"{motor.rotulo} falhou: {e}")
                if fluxo is not None and fluxo.abortar():
                    # já tinha falado parte da resposta: outra IA começaria do zero e repetiria. Avisa e para.
                    self._falar("Perdi a conexão no meio da resposta.", abrir_janela=True)
                    return None, motor, 0, None
                continue
            finally:
                if previa_thread:
                    previa_thread.join()
            ms = (time.monotonic() - t0) * 1000
            if isinstance(motor, MotorNemotron) and resposta.strip().upper().startswith(SINAL_ENCAMINHAR):
                # precisa de internet/PC: passa para a próxima IA com ferramentas (as que já estão na fila vêm antes)
                restantes = [x for x in tentativas[i + 1:] if x in MOTORES_COM_FERRAMENTAS]
                if not restantes:
                    restantes = [x for x in com_ferramentas if x not in tentativas[: i + 1]]
                tentativas[i + 1:] = restantes
                destino = self.motores[restantes[0]].rotulo if restantes else "ninguém"
                log.info("%s encaminhou para %s: %s", motor.rotulo, destino, texto)
                self.registro.emit("status", f"{motor.rotulo} encaminhou para {destino}", "")
                continue
            if fluxo is None or not fluxo.comecou:
                self._definir_estado("pensando")
            return resposta, motor, ms, fluxo
        motivo = "O Claude está sem login." if "sem login" in ultimo_erro else (ultimo_erro.split(":")[0] if ultimo_erro else "")
        self._falar("Não consegui responder agora. " + motivo, abrir_janela=False)
        return None, None, 0, None
