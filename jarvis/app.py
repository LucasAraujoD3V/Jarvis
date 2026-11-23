"""
Liga tudo: bandeja + popup + painel + assistente. Uma instância só por usuário
(abrir de novo só traz o painel). Reiniciar = sobe um processo novo e encerra este.

Uso:
  pythonw jarvis.pyw            normal (sem console)
  python  jarvis.pyw --console  com log no terminal
  pythonw jarvis.pyw --boot     chamado pelo atalho de inicialização do Windows
  python  jarvis.pyw --demo     só o popup em demonstração (sem microfone nem IA)
  python  jarvis.pyw --pedir "que horas são"   manda um pedido ao Jarvis que já está rodando (CMD/scripts)
  python  jarvis.pyw --falar "Backup concluído" faz o Jarvis rodando falar um texto
  python  jarvis.pyw --ouvir "Jarvis, que horas são"  teste: como se o microfone tivesse ouvido (nome + janela)
"""

from __future__ import annotations

import argparse
import ctypes
import getpass
import json
import logging
import logging.handlers
import os
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QProcess, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from . import autostart
from .config import PASTA_ASSETS, PASTA_LOGS, RAIZ, Preferencias, carregar_config, salvar_env

log = logging.getLogger("jarvis")
NOME_SOCKET = f"JarvisAssistente-{getpass.getuser()}"
SCRIPT = RAIZ / "jarvis.pyw"


def configurar_log(console: bool) -> None:
    PASTA_LOGS.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    raiz = logging.getLogger()
    raiz.setLevel(logging.INFO)
    arq = logging.handlers.RotatingFileHandler(PASTA_LOGS / "jarvis.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    arq.setFormatter(fmt)
    raiz.addHandler(arq)
    if console and sys.stderr:
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(fmt)
        raiz.addHandler(h)

    def excecao(tipo, valor, tb):
        log.critical("Erro não tratado", exc_info=(tipo, valor, tb))

    def excecao_thread(a):  # sem isto, erro em thread some (pythonw não tem stderr)
        log.critical("Erro na thread %s", a.thread.name if a.thread else "?", exc_info=(a.exc_type, a.exc_value, a.exc_traceback))

    sys.excepthook = excecao
    threading.excepthook = excecao_thread


def enviar_mensagem(acao: str, texto: str = "") -> bool:
    """Manda uma mensagem para o Jarvis que já está rodando. False = não tem Jarvis rodando."""
    s = QLocalSocket()
    s.connectToServer(NOME_SOCKET)
    if not s.waitForConnected(500):
        return False
    try:  # deixa o Jarvis rodando trazer o painel para a frente (quem chamou é o processo em primeiro plano)
        ctypes.windll.user32.AllowSetForegroundWindow(-1)
    except Exception:
        pass
    s.write(json.dumps({"acao": acao, "texto": texto}, ensure_ascii=False).encode("utf-8"))
    s.flush()
    s.waitForBytesWritten(1000)
    s.disconnectFromServer()
    return True


def ja_rodando(aguardar_s: float = 0.0) -> bool:
    """Se já existe um Jarvis, pede para ele mostrar o painel e devolve True.
    Com aguardar_s (reinício), espera o antigo terminar antes de desistir."""
    fim = time.monotonic() + aguardar_s
    while True:
        s = QLocalSocket()
        s.connectToServer(NOME_SOCKET)
        if not s.waitForConnected(300):
            return False
        s.disconnectFromServer()
        if time.monotonic() >= fim:
            return enviar_mensagem("mostrar")
        time.sleep(0.3)


def demo() -> int:
    """Popup em loop pelos estados — para ver a animação sem microfone/IA."""
    from .hud import HUD

    app = QApplication(sys.argv)
    hud = HUD("Jarvis")
    hud.mostrar()
    passos = [
        ("atento", "voce", "Jarvis, como está o tempo hoje?", 0.25),
        ("pensando", "status", "Pensando · Claude", 0.0),
        ("falando", "jarvis", "Hoje faz 27 graus, céu limpo. Chuva só à noite.", 1.0),
        ("ouvindo", "jarvis", "", 0.0),
    ]
    estado = {"i": 0, "t": 0.0}

    def tique():
        estado["t"] += 0.05
        i = estado["i"]
        e, quem, txt, nv = passos[i]
        if estado["t"] < 0.06:
            hud.definir_estado(e)
            if txt:
                hud.definir_legenda(quem, txt)
        if e == "falando":
            import math
            hud.definir_nivel(0.35 + 0.6 * abs(math.sin(estado["t"] * 7)) * (0.6 + 0.4 * math.sin(estado["t"] * 2.3)))
        else:
            hud.definir_nivel(nv)
        if estado["t"] > 3.0:
            estado["t"] = 0.0
            estado["i"] = (i + 1) % len(passos)

    t = QTimer()
    t.timeout.connect(tique)
    t.start(50)
    return app.exec()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--boot", action="store_true")
    ap.add_argument("--console", action="store_true")
    ap.add_argument("--reiniciado", action="store_true")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--sem-saudacao", action="store_true")
    ap.add_argument("--pedir", metavar="TEXTO", help="manda um pedido para o Jarvis que já está rodando")
    ap.add_argument("--falar", metavar="TEXTO", help="faz o Jarvis que já está rodando falar este texto")
    ap.add_argument("--ouvir", metavar="TEXTO", help="teste: entrega TEXTO como se o microfone tivesse ouvido "
                                                      "(passa pela palavra de ativação e pela janela de seguimento)")
    args = ap.parse_args()

    if args.pedir or args.falar or args.ouvir:  # modo comando: não abre interface, só conversa com a instância rodando
        from PySide6.QtCore import QCoreApplication

        _app = QCoreApplication(sys.argv)
        acao = "pedir" if args.pedir else "falar" if args.falar else "ouvir"
        ok = enviar_mensagem(acao, args.pedir or args.falar or args.ouvir)
        if not ok and sys.stderr:
            print("Jarvis não está rodando.", file=sys.stderr)
        return 0 if ok else 1

    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Jarvis.Assistente")
    except Exception:
        pass
    configurar_log(args.console)
    if args.demo:
        return demo()

    app = QApplication(sys.argv)
    app.setApplicationName("Jarvis")
    app.setQuitOnLastWindowClosed(False)

    if ja_rodando(aguardar_s=6.0 if args.reiniciado else 0.0):
        log.info("Já existe um Jarvis rodando: mostrei o painel dele e saí.")
        return 0

    servidor = QLocalServer()
    QLocalServer.removeServer(NOME_SOCKET)
    servidor.listen(NOME_SOCKET)

    from .assistente import Assistente
    from .bandeja import Bandeja
    from .hud import HUD
    from .icones import icone, salvar_ico
    from .painel import Painel

    cfg = carregar_config()
    prefs = Preferencias()
    nome = cfg.get("nome", "Jarvis")
    app.setWindowIcon(icone())

    assistente = Assistente(cfg, prefs)
    motores = [("auto", "Automático (escolhe a IA pelo tipo de pedido)")] + [(k, m.rotulo) for k, m in assistente.motores.items()]
    hud = HUD(nome)
    painel = Painel(nome, motores)
    bandeja = Bandeja(nome, motores)

    pos = prefs.get("hud_pos")
    if isinstance(pos, list) and len(pos) == 2:
        from PySide6.QtCore import QPoint
        hud.posicao_salva = QPoint(int(pos[0]), int(pos[1]))
    hud.movido.connect(lambda x, y: prefs.set("hud_pos", [x, y]))

    popup = {"on": bool(prefs.get("popup", cfg.get("interface", {}).get("popup", True)))}
    esconder_s = float(cfg.get("interface", {}).get("esconder_apos_s", 2.5))

    # ------------------------------------------------------------ autostart (na 1ª vez segue o config.json)
    if prefs.get("autostart_definido") is None and cfg.get("iniciar_com_windows", True):
        try:
            ico = salvar_ico(PASTA_ASSETS / "jarvis.ico")
            autostart.ativar(SCRIPT, ico)
            log.info("Atalho de inicialização criado: %s", autostart.ATALHO)
        except Exception:
            log.exception("Não consegui criar o atalho de inicialização")
        prefs.set("autostart_definido", True)

    def mudar_autostart(on: bool) -> None:
        try:
            if on:
                autostart.ativar(SCRIPT, salvar_ico(PASTA_ASSETS / "jarvis.ico"))
            else:
                autostart.desativar()
            assistente.aviso.emit("Vai iniciar com o Windows." if on else "Não inicia mais com o Windows.")
        except Exception as e:  # noqa: BLE001
            log.exception("Autostart")
            assistente.aviso.emit(f"Erro no autostart: {e}")
        sincronizar_checks()

    def mudar_popup(on: bool) -> None:
        popup["on"] = on
        prefs.set("popup", on)
        if not on:
            hud.esconder()
        sincronizar_checks()

    def sincronizar_checks() -> None:
        a = autostart.ativo()
        painel.definir_checks(popup["on"], a)
        bandeja.definir_checks(popup["on"], a)

    def atualizar_info() -> None:
        disp = assistente.motores_disponiveis()
        partes = []
        for k, m in assistente.motores.items():
            if k in ("claude", "nemotron") or m.disponivel:
                partes.append(f"{m.rotulo}: {'pronto' if m.disponivel else m.motivo}")
        painel.definir_info(assistente.ouvinte.nome_microfone, "IAs — " + " · ".join(partes), "nemotron" not in disp)

    # ------------------------------------------------------------ estado → interface
    minimizado = {"on": False}  # o usuário clicou em "–": fica escondido até ele chamar "Jarvis"

    def ao_estado(e: str) -> None:
        visual = "desligado" if e == "dormindo" else e
        hud.definir_estado(visual)
        painel.definir_estado(visual)
        bandeja.definir_estado(visual)
        if e in ("dormindo", "desligado"):  # só some dormindo ("boa noite, Jarvis") ou desligado
            hud.esconder()
            cartao.hide()
        elif e in ("atento", "pensando", "falando"):
            minimizado["on"] = False
            if popup["on"]:
                hud.mostrar()
        elif popup["on"] and not minimizado["on"]:
            hud.mostrar()
        atualizar_info()

    def ao_nivel(v: float) -> None:
        hud.definir_nivel(v)
        painel.definir_nivel(v)

    def ao_motor(m: str) -> None:
        painel.definir_motor(m)
        bandeja.definir_motor(m)

    assistente.estado.connect(ao_estado)
    assistente.nivel.connect(ao_nivel)
    assistente.legenda.connect(hud.definir_legenda)
    from .cartao import Cartao

    cartao = Cartao(nome)
    assistente.detalhe.connect(lambda t: cartao.mostrar(t, hud))
    assistente.legenda.connect(lambda quem, _t: cartao.hide() if quem == "voce" else None)  # pergunta nova: some
    hud._btn_min.clicked.connect(cartao.hide)
    hud._btn_min.clicked.connect(lambda: minimizado.update(on=True))
    assistente.registro.connect(painel.registrar)
    assistente.aviso.connect(painel.aviso)
    assistente.motor_mudou.connect(ao_motor)

    # ------------------------------------------------------------ comandos da interface → assistente
    def reiniciar() -> None:
        log.info("Reiniciando…")
        assistente.desligar()
        servidor.close()
        args_novos = [str(SCRIPT), "--reiniciado"] + (["--console"] if args.console else [])
        QProcess.startDetached(str(autostart.pythonw()), args_novos, str(RAIZ))
        bandeja.hide()
        QTimer.singleShot(150, app.quit)

    def sair() -> None:
        assistente.desligar()
        bandeja.hide()
        hud.hide()
        QTimer.singleShot(150, app.quit)

    def mostrar_painel() -> None:
        atualizar_info()
        # se o Windows escondeu a janela por fora (atalho/processo iniciado como "oculto"), o Qt ainda acha que ela
        # está visível e show() não faz nada: esconde e mostra de novo para sincronizar
        try:
            if painel.isVisible() and not ctypes.windll.user32.IsWindowVisible(int(painel.winId())):
                painel.hide()
        except Exception:
            pass
        painel.showNormal()
        painel.raise_()
        painel.activateWindow()

    def salvar_chave(chave: str) -> None:
        if not chave.startswith("nvapi-"):
            painel.aviso("Chave estranha: as do Nemotron começam com nvapi-. Salvei mesmo assim.")
        salvar_env("NVIDIA_API_KEY", chave)
        assistente.recarregar_chave_nemotron()
        atualizar_info()

    for origem in (painel, bandeja):
        origem.pedir_ligar.connect(lambda: assistente.ligar(saudar=True))
        origem.pedir_desligar.connect(assistente.desligar)
        origem.pedir_reiniciar.connect(reiniciar)
        origem.pedir_parar.connect(assistente.parar)
        origem.pedir_nova_conversa.connect(assistente.nova_conversa)
        origem.pedir_motor.connect(assistente.definir_motor)
        origem.mudou_popup.connect(mudar_popup)
        origem.mudou_autostart.connect(mudar_autostart)
    painel.pedir_texto.connect(assistente.enviar_texto)
    painel.pedir_chave.connect(salvar_chave)
    bandeja.pedir_painel.connect(hud.mostrar)  # clique no ícone = mostra o rosto
    bandeja.pedir_sair.connect(sair)
    hud.clicado.connect(assistente.parar)
    assistente.pedir_reinicio.connect(reiniciar)

    def nova_conexao() -> None:
        s = servidor.nextPendingConnection()
        if not s:
            return
        dados = b""
        while s.waitForReadyRead(400):
            dados += bytes(s.readAll())
        dados += bytes(s.readAll())
        s.disconnectFromServer()
        if not dados:  # só uma sonda de "tem alguém aí?" (ja_rodando)
            return
        try:
            msg = json.loads(dados.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            msg = {"acao": "mostrar"}
        acao, texto = msg.get("acao"), str(msg.get("texto") or "")
        log.info("Mensagem local: %s %s", acao, texto[:80])
        if acao == "pedir" and texto:
            assistente.enviar_texto(texto)
        elif acao == "falar" and texto:
            assistente.dizer(texto)
        elif acao == "ouvir" and texto:
            log.info("Ouvido simulado (--ouvir): %s", texto)
            assistente._ao_texto(texto)
        else:
            mostrar_painel()

    servidor.newConnection.connect(nova_conexao)

    # ------------------------------------------------------------ começa
    ao_motor(assistente.modo)
    ao_estado("desligado")
    sincronizar_checks()
    bandeja.show()
    saudar = not args.sem_saudacao and not args.reiniciado
    # no boot o Windows ainda está subindo áudio/rede: espera um pouco antes de abrir o microfone
    QTimer.singleShot(8000 if args.boot else 300, lambda: assistente.ligar(saudar=saudar))
    aviso_inicio = prefs.get("aviso_inicio")  # ex.: reiniciou sozinho porque achou um microfone com som
    if aviso_inicio:
        prefs.set("aviso_inicio", None)
        QTimer.singleShot(9500 if args.boot else 1500, lambda: assistente.dizer(aviso_inicio))
    # só o rosto: nada de painel ao abrir (o painel antigo fica escondido; a tela é o rosto animado do HUD)
    log.info("Jarvis iniciado (boot=%s, reiniciado=%s)", args.boot, args.reiniciado)
    codigo = app.exec()
    assistente.encerrar()  # fecha o processo do Claude que fica aberto entre os pedidos
    # Sai na marra: em 20/11 um Jarvis ficou vivo depois de "reiniciar" (preso no encerramento do áudio nativo) e
    # ficaram DOIS ouvindo e falando. Tudo que importa (preferências, log) já foi gravado.
    log.info("Jarvis encerrado")
    logging.shutdown()
    os._exit(codigo)
