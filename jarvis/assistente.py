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

import logging
import queue
import random
import threading
import time

from PySide6.QtCore import QObject, Signal

from .config import caminho, ler_env
from .motores import Cancelado, ErroMotor, MotorClaude, MotorCLI, MotorNemotron, SINAL_ENCAMINHAR
from .texto import comando_local, escolher_motor, extrair_comando, limpar_para_fala
from .voz import Falante, Ouvinte

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


class Assistente(QObject):
    estado = Signal(str)               # desligado | ouvindo | atento | pensando | falando
    nivel = Signal(float)              # 0..1 — volume da voz (falando) ou do microfone (atento)
    legenda = Signal(str, str)         # quem ("voce" | "jarvis" | "status"), texto
    registro = Signal(str, str, str)   # quem, texto, detalhe — histórico do painel
    aviso = Signal(str)                # mensagens de status/erro
    motor_mudou = Signal(str)          # auto | claude | nemotron | ...
    pedir_reinicio = Signal()

    def __init__(self, cfg: dict, prefs):
        super().__init__()
        self.cfg = cfg
        self.prefs = prefs
        self.nome = cfg.get("nome", "Jarvis")
        self.chaves = cfg.get("palavras_chave") or [self.nome.lower()]
        self.semelhanca = float(cfg.get("semelhanca_minima", 0.75))
        conv = cfg.get("conversa", {})
        self.janela_s = float(conv.get("janela_seguimento_s", 8))
        self.max_fala = int(conv.get("max_caracteres_fala", 600))
        self.reset_s = float(conv.get("nova_conversa_apos_min", 15)) * 60
        self.previa = bool(conv.get("previa_quando_demora", True))
        estilo = list(cfg.get("estilo_resposta") or [])
        pasta = caminho(cfg.get("claude", {}).get("pasta_trabalho", ".."))

        env = ler_env()
        self.motores = {
            "nemotron": MotorNemotron(cfg, env.get("NVIDIA_API_KEY", ""), self.nome, estilo, int(conv.get("historico_turnos", 10))),
            "claude": MotorClaude(cfg, pasta, self.nome, estilo),
        }
        for nome, c in (cfg.get("motores_extras") or {}).items():
            self.motores[nome] = MotorCLI(nome, c, pasta)

        self.modo = prefs.get("motor") or cfg.get("motor_padrao", "auto")
        self.ouvinte = Ouvinte(cfg.get("microfone", {}), cfg.get("idioma", "pt-BR"), self._ao_texto, self._nivel_mic, self._avisar,
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
        threading.Thread(target=self._aquecer, name="jarvis-aquecer", daemon=True).start()

    @staticmethod
    def _aquecer() -> None:
        """Importa a síntese de voz antes da 1ª fala (a 1ª chamada fria levava ~4 s)."""
        try:
            import edge_tts  # noqa: F401
            import miniaudio  # noqa: F401
        except Exception:
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
        disp = self.motores_disponiveis()
        self._avisar(f"Ligado. Microfone: {self.ouvinte.nome_microfone}. IAs: {', '.join(sorted(disp)) or 'nenhuma'}.")
        if "nemotron" not in disp:
            self._avisar(f"Nemotron desligado: {self.motores['nemotron'].motivo}.")
        if saudar and self.cfg.get("saudacao"):
            self.dizer(self.cfg["saudacao"], abrir_janela=False)

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

    def nova_conversa(self) -> None:
        for m in self.motores.values():
            m.nova_conversa()
        self._avisar("Conversa nova: histórico esquecido.")

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
        self.motores["nemotron"].chave = ler_env().get("NVIDIA_API_KEY", "")
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

    def _ao_texto(self, texto: str) -> None:
        """Frase transcrita do microfone (thread de transcrição)."""
        if not self._ligado:
            return
        achou, resto = extrair_comando(texto, self.chaves, self.semelhanca)
        if not achou and not self._janela_aberta():
            log.debug("Ignorado (sem palavra de ativação): %s", texto)
            return
        log.info("Ouviu: %s", texto)
        if achou and not resto:
            self._abrir_janela()
            self._definir_estado("atento")
            self.legenda.emit("voce", texto)
            self.dizer(random.choice(self.cfg.get("respostas_ativacao") or ["Sim?"]), abrir_janela=True)
            return
        self._tratar_comando(resto if achou else texto)

    def _tratar_comando(self, cmd: str) -> None:
        self._ultimo_uso_check()
        local = comando_local(cmd)
        self.legenda.emit("voce", cmd)
        self.registro.emit("voce", cmd, "")
        if local == "cancelar":
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
        elif local == "desligar":
            self.parar()
            self._fila.put(("falar", "Desligando. Me ligue pela bandeja.", False))
            self._fila.put(("desligar",))
        elif local == "reiniciar":
            self.parar()
            self._fila.put(("falar", "Reiniciando.", False))
            self._fila.put(("reiniciar",))
        else:
            self._fila.put(("perguntar", cmd))

    def _ultimo_uso_check(self) -> None:
        agora = time.monotonic()
        if agora - self._ultimo_uso > self.reset_s:
            for m in self.motores.values():
                m.nova_conversa()
            log.info("Conversa antiga expirou: começando uma nova")
        self._ultimo_uso = agora

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
                    self._perguntar(item[1])
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

    def _perguntar(self, cmd: str) -> None:
        disp = self.motores_disponiveis()
        extras = [n for n in self.motores if n not in ("claude", "nemotron")]
        nome, texto = escolher_motor(cmd, self.modo, disp, extras)
        if nome is None:
            self._falar("Nenhuma IA disponível. Veja o painel.", abrir_janela=False)
            self._avisar("Nenhuma IA disponível: configure a chave do Nemotron ou instale/logue o Claude Code.")
            return
        resposta, motor, ms = self._consultar(nome, texto, disp)
        if resposta is None:
            return
        log.info("Resposta %s (%.1f s): %s", motor.rotulo, ms / 1000, resposta[:300].replace("\n", " "))
        self.registro.emit("jarvis", resposta, f"{motor.rotulo} · {ms / 1000:.1f} s")
        fala, cortado = limpar_para_fala(resposta, self.max_fala)
        if cortado:
            fala += " O resto está no painel."
        self._falar(fala or "Feito.", abrir_janela=True)

    def _consultar(self, nome: str, texto: str, disp: set[str]):
        """Pergunta ao motor escolhido; se ele falhar, tenta o outro. Devolve (resposta, motor, ms) ou (None, ...)."""
        tentativas = [nome] + [n for n in ("nemotron", "claude") if n != nome and n in disp]
        nem = self.motores["nemotron"]
        nem.encaminhar = self.modo == "auto" and "claude" in disp
        ultimo_erro = ""
        for i, n in enumerate(tentativas):
            motor = self.motores[n]
            self._definir_estado("pensando")
            self.legenda.emit("status", f"Pensando · {motor.rotulo}")
            previa_thread = None
            if motor.lento and self.previa:
                previa_thread = threading.Thread(target=self._falar_previa, args=(previa_para(texto),), daemon=True)
                previa_thread.start()
            t0 = time.monotonic()
            try:
                resposta = motor.perguntar(texto, self._cancelar)
            except Cancelado:
                log.info("Pedido cancelado")
                return None, motor, 0
            except ErroMotor as e:
                ultimo_erro = str(e)
                log.warning("%s falhou: %s", motor.rotulo, e)
                self._avisar(f"{motor.rotulo} falhou: {e}")
                continue
            finally:
                if previa_thread:
                    previa_thread.join()
            ms = (time.monotonic() - t0) * 1000
            if n == "nemotron" and resposta.strip().upper().startswith(SINAL_ENCAMINHAR):
                if "claude" in disp and "claude" not in tentativas[i + 1:]:
                    tentativas.append("claude")
                log.info("Nemotron encaminhou para o Claude: %s", texto)
                self.registro.emit("status", "Nemotron encaminhou para o Claude", "")
                continue
            self._definir_estado("pensando")
            return resposta, motor, ms
        self._falar("Não consegui responder agora. " + (ultimo_erro.split(":")[0] if ultimo_erro else ""), abrir_janela=False)
        return None, None, 0
