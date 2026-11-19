"""
Popup flutuante do Jarvis (estilo HUD): aparece no canto inferior direito quando ele ouve o nome, pensa ou fala,
e some sozinho depois. Fica por cima de tudo, sem roubar o foco do que você está usando.

- Clique esquerdo: cala o Jarvis / cancela o pedido.
- Arrastar: muda o popup de lugar (a posição fica salva).
- Clique direito: esconde agora.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QEasingCurve, QPoint, QPointF, QPropertyAnimation, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from .reator import Reator

ROTULOS = {
    "ouvindo": "AGUARDANDO",
    "atento": "OUVINDO",
    "pensando": "PROCESSANDO",
    "falando": "FALANDO",
    "desligado": "DESLIGADO",
}


class HUD(QWidget):
    clicado = Signal()
    movido = Signal(int, int)

    LARGURA = 360
    ALTURA = 470
    RAIO = 128

    def __init__(self, nome: str = "JARVIS"):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(self.LARGURA, self.ALTURA)
        self.setWindowTitle(f"{nome} — popup")
        self.nome = nome.upper()
        self.reator = Reator()
        self.estado = "ouvindo"
        self.texto_voce = ""
        self.texto_jarvis = ""
        self.texto_status = ""
        self.posicao_salva: QPoint | None = None
        self._arrasto: QPoint | None = None
        self._arrastou = False
        self._ultimo = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tique)
        self._anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._anim.setDuration(260)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.finished.connect(self._fim_anim)
        self._escondendo = False
        self._timer_esconder = QTimer(self)
        self._timer_esconder.setSingleShot(True)
        self._timer_esconder.timeout.connect(self.esconder)

    # ------------------------------------------------------------------ API
    def definir_estado(self, e: str) -> None:
        self.estado = e
        self.reator.definir_estado(e)
        if e in ("ouvindo", "desligado"):
            self.texto_status = ""
        self.update()

    def definir_nivel(self, v: float) -> None:
        self.reator.definir_nivel(v)

    def definir_legenda(self, quem: str, texto: str) -> None:
        if quem == "voce":
            self.texto_voce = texto
            self.texto_jarvis = ""
            self.texto_status = ""
        elif quem == "jarvis":
            self.texto_jarvis = texto
            self.texto_status = ""
        else:
            self.texto_status = texto
        self.update()

    def mostrar(self) -> None:
        self._timer_esconder.stop()
        if self.isVisible() and not self._escondendo:
            return
        if not self.isVisible():
            self._posicionar()
            self.setWindowOpacity(0.0)
            self._ultimo = time.monotonic()
            self._timer.start()
            self.show()
            self.raise_()
        self._escondendo = False
        self._anim.stop()
        self._anim.setStartValue(self.windowOpacity())
        self._anim.setEndValue(1.0)
        self._anim.start()

    def esconder_em(self, segundos: float) -> None:
        if self.isVisible():
            self._timer_esconder.start(int(segundos * 1000))

    def esconder(self) -> None:
        self._timer_esconder.stop()
        if not self.isVisible():
            return
        self._escondendo = True
        self._anim.stop()
        self._anim.setStartValue(self.windowOpacity())
        self._anim.setEndValue(0.0)
        self._anim.start()

    def _fim_anim(self) -> None:
        if self._escondendo:
            self.hide()
            self._timer.stop()
            self._escondendo = False
            self.texto_voce = self.texto_jarvis = self.texto_status = ""

    def _posicionar(self) -> None:
        tela = QGuiApplication.primaryScreen().availableGeometry()
        if self.posicao_salva is not None:
            p = self.posicao_salva
            if tela.contains(QPoint(p.x() + 20, p.y() + 20)):
                self.move(p)
                return
        self.move(tela.right() - self.LARGURA - 18, tela.bottom() - self.ALTURA - 12)

    def _tique(self) -> None:
        agora = time.monotonic()
        self.reator.passo(agora - self._ultimo)
        self._ultimo = agora
        self.update()

    # ------------------------------------------------------------------ mouse
    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.LeftButton:
            self._arrasto = ev.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._arrastou = False
        elif ev.button() == Qt.RightButton:
            self.esconder()

    def mouseMoveEvent(self, ev) -> None:
        if self._arrasto is not None and ev.buttons() & Qt.LeftButton:
            novo = ev.globalPosition().toPoint() - self._arrasto
            if (novo - self.pos()).manhattanLength() > 3:
                self._arrastou = True
                self.move(novo)

    def mouseReleaseEvent(self, ev) -> None:
        if ev.button() == Qt.LeftButton and self._arrasto is not None:
            if self._arrastou:
                self.posicao_salva = self.pos()
                self.movido.emit(self.x(), self.y())
            else:
                self.clicado.emit()
            self._arrasto = None

    # ------------------------------------------------------------------ desenho
    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        w = self.width()
        cx, cy = w / 2, 20 + self.RAIO * 1.18
        self.reator.pintar(p, QPointF(cx, cy), self.RAIO)

        # nome + estado
        cor = QColor(*[int(c) for c in self.reator.cor])
        y = cy + self.RAIO * 1.18 + 4
        f = QFont("Segoe UI", 10, QFont.DemiBold)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 4)
        p.setFont(f)
        p.setPen(QColor(cor.red(), cor.green(), cor.blue(), 230))
        rotulo = f"{self.nome}  ·  {ROTULOS.get(self.estado, self.estado.upper())}"
        p.drawText(QRectF(0, y, w, 20), Qt.AlignHCenter | Qt.AlignVCenter, rotulo)

        # legenda (o que você disse + o que ele está falando)
        linhas: list[tuple[str, QColor, QFont]] = []
        fv = QFont("Segoe UI", 9)
        fj = QFont("Segoe UI", 11, QFont.Medium)
        if self.texto_voce:
            linhas.append((f"Você: {self.texto_voce}", QColor(150, 205, 225, 210), fv))
        if self.texto_jarvis:
            linhas.append((self.texto_jarvis, QColor(240, 250, 255, 250), fj))
        elif self.texto_status:
            linhas.append((self.texto_status, QColor(cor.red(), cor.green(), cor.blue(), 220), fv))
        if not linhas:
            return
        caixa = QRectF(14, y + 26, w - 28, self.height() - (y + 26) - 8)
        alturas = []
        for txt, _, fonte in linhas:
            fm = QFontMetrics(fonte)
            r = fm.boundingRect(0, 0, int(caixa.width() - 24), 400, Qt.TextWordWrap, txt)
            alturas.append(min(r.height(), fm.lineSpacing() * (2 if fonte is fv else 4)))
        total = sum(alturas) + 8 * (len(linhas) - 1) + 20
        fundo = QRectF(caixa.x(), caixa.y(), caixa.width(), min(total, caixa.height()))
        caminho = QPainterPath()
        caminho.addRoundedRect(fundo, 12, 12)
        p.fillPath(caminho, QColor(4, 14, 24, 235))
        p.setPen(QPen(QColor(cor.red(), cor.green(), cor.blue(), 90), 1))
        p.drawPath(caminho)
        yy = fundo.y() + 10
        for (txt, c, fonte), h in zip(linhas, alturas):
            p.setFont(fonte)
            p.setPen(c)
            p.drawText(QRectF(fundo.x() + 12, yy, fundo.width() - 24, h), Qt.TextWordWrap | Qt.AlignHCenter | Qt.AlignTop, txt)
            yy += h + 8
