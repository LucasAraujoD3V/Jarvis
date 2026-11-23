"""
Cartão de texto ao lado do rosto: a versão ESCRITA e completa da resposta (a falada é curta e didática, como no modo
de voz do ChatGPT). Só aparece quando a IA mandou detalhes (depois da marca [[TEXTO]]). Só texto e um "×".
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout, QWidget


class Cartao(QWidget):
    LARGURA = 460
    ALTURA = 470

    def __init__(self, nome: str = "Jarvis"):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(self.LARGURA, self.ALTURA)
        self.setWindowTitle(f"{nome} — resposta")
        fundo = QFrame(self)
        fundo.setObjectName("fundo")
        fundo.setGeometry(0, 0, self.LARGURA, self.ALTURA)
        fundo.setStyleSheet(
            "QFrame#fundo{background:rgba(7,16,24,235);border:1px solid rgba(127,220,255,80);border-radius:14px;}"
            "QLabel{color:#7fdcff;font-size:12px;letter-spacing:2px;}"
            "QTextBrowser{background:transparent;border:none;color:#dcf4ff;font-size:14px;}"
            "QPushButton{color:#7fdcff;background:transparent;border:none;font-size:18px;}"
            "QPushButton:hover{color:#ffffff;}")
        topo = QHBoxLayout()
        titulo = QLabel(nome.upper())
        fechar = QPushButton("×")
        fechar.setFixedSize(28, 28)
        fechar.setCursor(Qt.PointingHandCursor)
        fechar.clicked.connect(self.hide)
        topo.addWidget(titulo)
        topo.addStretch(1)
        topo.addWidget(fechar)
        self.texto = QTextBrowser()
        self.texto.setOpenExternalLinks(True)
        caixa = QVBoxLayout(fundo)
        caixa.setContentsMargins(18, 10, 12, 16)
        caixa.addLayout(topo)
        caixa.addWidget(self.texto)

    def mostrar(self, markdown: str, perto_de: QWidget | None = None) -> None:
        self.texto.setMarkdown(markdown)
        tela = QGuiApplication.primaryScreen().availableGeometry()
        if perto_de is not None and perto_de.isVisible():
            x = perto_de.x() - self.width() - 12
            y = perto_de.y() + perto_de.height() - self.height()
        else:
            x = tela.right() - self.width() - 18 - 372
            y = tela.bottom() - self.height() - 12
        self.move(max(tela.left() + 8, x), max(tela.top() + 8, min(y, tela.bottom() - self.height())))
        self.show()
        self.raise_()
