"""
Painel do Jarvis: a "interface para ver". Abre pela bandeja (clique no ícone) e fechar só esconde.

Mostra o reator animado, o estado, o microfone, quais IAs estão prontas, o histórico da conversa
e permite: ligar/desligar/reiniciar, calar, conversa nova, trocar o motor, digitar um comando e
colar a chave do Nemotron (vai para o .env, nunca para o git).
"""

from __future__ import annotations

import html
import time

from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextBrowser, QVBoxLayout, QWidget,
)

from .icones import icone
from .reator import Reator

ESTILO = """
QWidget { background: #07111b; color: #d7f3ff; font-family: 'Segoe UI'; font-size: 10pt; }
QLabel#titulo { font-size: 18pt; font-weight: 600; letter-spacing: 6px; color: #39e0ff; }
QLabel#estado { font-size: 11pt; letter-spacing: 2px; color: #8fdcf0; }
QLabel.info { color: #7fa8b8; }
QPushButton { background: #0d2233; border: 1px solid #1b4a63; border-radius: 6px; padding: 6px 12px; color: #c9f1ff; }
QPushButton:hover { background: #12324a; border-color: #39e0ff; }
QPushButton:disabled { color: #4d6673; border-color: #16303f; }
QPushButton#principal { border-color: #39e0ff; color: #39e0ff; font-weight: 600; }
QPushButton#principal:disabled { color: #4d6673; border-color: #16303f; font-weight: 400; }
QComboBox, QLineEdit { background: #0a1a27; border: 1px solid #1b4a63; border-radius: 6px; padding: 5px 8px; color: #d7f3ff; }
QComboBox QAbstractItemView { background: #0a1a27; selection-background-color: #12324a; }
QTextBrowser { background: #050d15; border: 1px solid #12324a; border-radius: 8px; padding: 6px; }
QCheckBox { color: #9cc7d6; }
"""


class ReatorWidget(QWidget):
    def __init__(self, tamanho: int = 150, parent=None):
        super().__init__(parent)
        self.setFixedSize(tamanho, tamanho)
        self.reator = Reator()
        self._ultimo = time.monotonic()
        self._t = QTimer(self)
        self._t.setInterval(20)
        self._t.timeout.connect(self._tique)

    def showEvent(self, e):
        self._ultimo = time.monotonic()
        self._t.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self._t.stop()
        super().hideEvent(e)

    def _tique(self):
        agora = time.monotonic()
        self.reator.passo(agora - self._ultimo)
        self._ultimo = agora
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        r = self.width() / 2
        self.reator.pintar(p, QPointF(r, r), r / 1.2, fundo=False)


class Painel(QWidget):
    pedir_ligar = Signal()
    pedir_desligar = Signal()
    pedir_reiniciar = Signal()
    pedir_parar = Signal()
    pedir_nova_conversa = Signal()
    pedir_motor = Signal(str)
    pedir_texto = Signal(str)
    pedir_chave = Signal(str)
    mudou_popup = Signal(bool)
    mudou_autostart = Signal(bool)

    def __init__(self, nome: str, motores: list[tuple[str, str]]):
        super().__init__()
        self.setWindowTitle(f"{nome} — Painel")
        self.setWindowIcon(icone())
        self.setStyleSheet(ESTILO)
        self.resize(640, 720)

        self.reator = ReatorWidget(150)
        self.lbl_titulo = QLabel(nome.upper())
        self.lbl_titulo.setObjectName("titulo")
        self.lbl_estado = QLabel("—")
        self.lbl_estado.setObjectName("estado")
        self.lbl_mic = QLabel("Microfone: —")
        self.lbl_ias = QLabel("IAs: —")
        self.lbl_aviso = QLabel("")
        for l in (self.lbl_mic, self.lbl_ias, self.lbl_aviso):
            l.setProperty("class", "info")
            l.setWordWrap(True)
        self.lbl_dica = QLabel(f"Diga “{nome}” + pedido. Ex.: “{nome}, que horas são?” · “{nome}, pergunta pro Claude o status da Mesa Traders”.")
        self.lbl_dica.setWordWrap(True)
        self.lbl_dica.setProperty("class", "info")

        topo_txt = QVBoxLayout()
        topo_txt.addWidget(self.lbl_titulo)
        topo_txt.addWidget(self.lbl_estado)
        topo_txt.addSpacing(6)
        topo_txt.addWidget(self.lbl_mic)
        topo_txt.addWidget(self.lbl_ias)
        topo_txt.addWidget(self.lbl_aviso)
        topo_txt.addStretch(1)
        topo = QHBoxLayout()
        topo.addWidget(self.reator)
        topo.addLayout(topo_txt, 1)

        self.bt_ligar = QPushButton("Ligar")
        self.bt_ligar.setObjectName("principal")
        self.bt_desligar = QPushButton("Desligar")
        self.bt_reiniciar = QPushButton("Reiniciar")
        self.bt_parar = QPushButton("Calar")
        self.bt_nova = QPushButton("Conversa nova")
        self.bt_ligar.clicked.connect(self.pedir_ligar)
        self.bt_desligar.clicked.connect(self.pedir_desligar)
        self.bt_reiniciar.clicked.connect(self.pedir_reiniciar)
        self.bt_parar.clicked.connect(self.pedir_parar)
        self.bt_nova.clicked.connect(self.pedir_nova_conversa)
        botoes = QHBoxLayout()
        for b in (self.bt_ligar, self.bt_desligar, self.bt_reiniciar, self.bt_parar, self.bt_nova):
            botoes.addWidget(b)

        self.cb_motor = QComboBox()
        for chave, rotulo in motores:
            self.cb_motor.addItem(rotulo, chave)
        self.cb_motor.currentIndexChanged.connect(lambda _: self.pedir_motor.emit(self.cb_motor.currentData()))
        self.ck_popup = QCheckBox("Mostrar popup")
        self.ck_auto = QCheckBox("Iniciar com o Windows")
        self.ck_popup.toggled.connect(self.mudou_popup)
        self.ck_auto.toggled.connect(self.mudou_autostart)
        linha_motor = QHBoxLayout()
        linha_motor.addWidget(QLabel("Motor:"))
        linha_motor.addWidget(self.cb_motor, 1)
        linha_motor.addWidget(self.ck_popup)
        linha_motor.addWidget(self.ck_auto)

        self.historico = QTextBrowser()
        self.historico.setOpenExternalLinks(False)

        self.ed_texto = QLineEdit()
        self.ed_texto.setPlaceholderText("Digite um pedido e Enter (não precisa dizer o nome)…")
        self.ed_texto.returnPressed.connect(self._enviar)
        bt_enviar = QPushButton("Enviar")
        bt_enviar.clicked.connect(self._enviar)
        linha_txt = QHBoxLayout()
        linha_txt.addWidget(self.ed_texto, 1)
        linha_txt.addWidget(bt_enviar)

        self.ed_chave = QLineEdit()
        self.ed_chave.setEchoMode(QLineEdit.Password)
        self.ed_chave.setPlaceholderText("Chave do Nemotron (nvapi-…) — fica só no .env deste PC")
        bt_chave = QPushButton("Salvar chave")
        bt_chave.clicked.connect(self._salvar_chave)
        self.linha_chave = QWidget()
        lc = QHBoxLayout(self.linha_chave)
        lc.setContentsMargins(0, 0, 0, 0)
        lc.addWidget(self.ed_chave, 1)
        lc.addWidget(bt_chave)

        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(16, 14, 16, 14)
        raiz.setSpacing(10)
        raiz.addLayout(topo)
        raiz.addLayout(botoes)
        raiz.addLayout(linha_motor)
        raiz.addWidget(self.lbl_dica)
        raiz.addWidget(self.historico, 1)
        raiz.addLayout(linha_txt)
        raiz.addWidget(self.linha_chave)

    # ------------------------------------------------------------------ atualizações
    def definir_estado(self, e: str) -> None:
        nomes = {"desligado": "DESLIGADO", "ouvindo": "AGUARDANDO O NOME", "atento": "OUVINDO",
                 "pensando": "PROCESSANDO", "falando": "FALANDO"}
        self.lbl_estado.setText(nomes.get(e, e.upper()))
        self.reator.reator.definir_estado(e)
        ligado = e != "desligado"
        self.bt_ligar.setEnabled(not ligado)
        self.bt_desligar.setEnabled(ligado)

    def definir_nivel(self, v: float) -> None:
        self.reator.reator.definir_nivel(v)

    def definir_motor(self, chave: str) -> None:
        i = self.cb_motor.findData(chave)
        if i >= 0 and i != self.cb_motor.currentIndex():
            self.cb_motor.blockSignals(True)
            self.cb_motor.setCurrentIndex(i)
            self.cb_motor.blockSignals(False)

    def definir_checks(self, popup: bool, autostart: bool) -> None:
        for ck, v in ((self.ck_popup, popup), (self.ck_auto, autostart)):
            ck.blockSignals(True)
            ck.setChecked(v)
            ck.blockSignals(False)

    def definir_info(self, mic: str, ias: str, precisa_chave: bool) -> None:
        self.lbl_mic.setText(f"Microfone: {mic or '—'}")
        self.lbl_ias.setText(ias)
        self.linha_chave.setVisible(precisa_chave)

    def aviso(self, msg: str) -> None:
        self.lbl_aviso.setText(msg)
        self._linha("status", msg, "")

    def registrar(self, quem: str, texto: str, detalhe: str) -> None:
        self._linha(quem, texto, detalhe)

    def _linha(self, quem: str, texto: str, detalhe: str) -> None:
        hora = time.strftime("%H:%M:%S")
        t = html.escape(texto).replace("\n", "<br>")
        if quem == "voce":
            bloco = f"<p style='margin:6px 0'><span style='color:#5f8a9c'>{hora}</span> <b style='color:#9be7ff'>Você:</b> {t}</p>"
        elif quem == "jarvis":
            det = f" <span style='color:#5f8a9c'>({html.escape(detalhe)})</span>" if detalhe else ""
            bloco = f"<p style='margin:6px 0 12px 0'><span style='color:#5f8a9c'>{hora}</span> <b style='color:#39e0ff'>Jarvis</b>{det}<br>{t}</p>"
        else:
            bloco = f"<p style='margin:3px 0; color:#56798a; font-size:9pt'>{hora} · {t}</p>"
        self.historico.append(bloco)
        sb = self.historico.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _enviar(self) -> None:
        t = self.ed_texto.text().strip()
        if t:
            self.ed_texto.clear()
            self.pedir_texto.emit(t)

    def _salvar_chave(self) -> None:
        t = self.ed_chave.text().strip()
        if t:
            self.ed_chave.clear()
            self.pedir_chave.emit(t)

    def closeEvent(self, ev) -> None:  # fechar = esconder (o Jarvis continua na bandeja)
        ev.ignore()
        self.hide()
