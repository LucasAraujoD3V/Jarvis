"""Ícone na bandeja do Windows (perto do relógio) com o menu de controle do Jarvis."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from .icones import icone

NOMES = {"desligado": "desligado", "ouvindo": "aguardando o nome", "atento": "ouvindo",
         "pensando": "processando", "falando": "falando"}


class Bandeja(QSystemTrayIcon):
    pedir_painel = Signal()
    pedir_ligar = Signal()
    pedir_desligar = Signal()
    pedir_reiniciar = Signal()
    pedir_parar = Signal()
    pedir_nova_conversa = Signal()
    pedir_motor = Signal(str)
    mudou_popup = Signal(bool)
    mudou_autostart = Signal(bool)
    pedir_sair = Signal()

    def __init__(self, nome: str, motores: list[tuple[str, str]]):
        super().__init__(icone("ouvindo"))
        self.nome = nome
        self._icones = {e: icone(e) for e in ("ouvindo", "atento", "pensando", "falando", "desligado")}
        m = QMenu()
        self.acao_status = QAction(f"{nome}: —", m)
        self.acao_status.setEnabled(False)
        m.addAction(self.acao_status)
        m.addAction("Abrir painel", self.pedir_painel.emit)
        m.addSeparator()
        self.acao_ligar = m.addAction("Ligar", self.pedir_ligar.emit)
        self.acao_desligar = m.addAction("Desligar", self.pedir_desligar.emit)
        m.addAction("Reiniciar", self.pedir_reiniciar.emit)
        m.addAction("Calar / cancelar", self.pedir_parar.emit)
        m.addAction("Conversa nova", self.pedir_nova_conversa.emit)
        m.addSeparator()
        sub = m.addMenu("Motor de IA")
        self.grupo_motor = QActionGroup(sub)
        self.acoes_motor: dict[str, QAction] = {}
        for chave, rotulo in motores:
            a = sub.addAction(rotulo)
            a.setCheckable(True)
            a.setData(chave)
            self.grupo_motor.addAction(a)
            a.triggered.connect(lambda _=False, c=chave: self.pedir_motor.emit(c))
            self.acoes_motor[chave] = a
        self.acao_popup = m.addAction("Mostrar popup")
        self.acao_popup.setCheckable(True)
        self.acao_popup.toggled.connect(self.mudou_popup)
        self.acao_auto = m.addAction("Iniciar com o Windows")
        self.acao_auto.setCheckable(True)
        self.acao_auto.toggled.connect(self.mudou_autostart)
        m.addSeparator()
        m.addAction("Sair", self.pedir_sair.emit)
        self._menu = m
        self.setContextMenu(m)
        self.activated.connect(self._ativado)
        self.setToolTip(nome)

    def _ativado(self, motivo) -> None:
        if motivo in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.pedir_painel.emit()

    def definir_estado(self, e: str) -> None:
        self.setIcon(self._icones.get(e, self._icones["ouvindo"]))
        txt = f"{self.nome}: {NOMES.get(e, e)}"
        self.setToolTip(txt)
        self.acao_status.setText(txt)
        self.acao_ligar.setEnabled(e == "desligado")
        self.acao_desligar.setEnabled(e != "desligado")

    def definir_motor(self, chave: str) -> None:
        a = self.acoes_motor.get(chave)
        if a and not a.isChecked():
            a.setChecked(True)

    def definir_checks(self, popup: bool, autostart: bool) -> None:
        for a, v in ((self.acao_popup, popup), (self.acao_auto, autostart)):
            a.blockSignals(True)
            a.setChecked(v)
            a.blockSignals(False)
