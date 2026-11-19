"""Ícones desenhados em código (nada de arquivo de imagem para manter): bandeja, janela e atalho do Windows."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QRadialGradient

from .reator import CORES


def pixmap_reator(estado: str = "ouvindo", tam: int = 64) -> QPixmap:
    r, g, b = CORES.get(estado, CORES["ouvindo"])
    pm = QPixmap(tam, tam)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    c = QPointF(tam / 2, tam / 2)
    R = tam / 2 - 1
    fundo = QRadialGradient(c, R)
    fundo.setColorAt(0, QColor(8, 20, 32, 255))
    fundo.setColorAt(1, QColor(4, 10, 18, 255))
    p.setBrush(fundo)
    p.setPen(Qt.NoPen)
    p.drawEllipse(c, R, R)
    ret = QRectF(c.x() - R * 0.78, c.y() - R * 0.78, R * 1.56, R * 1.56)
    pen = QPen(QColor(r, g, b), max(2.0, tam * 0.09))
    pen.setCapStyle(Qt.FlatCap)
    p.setPen(pen)
    for k in range(3):
        p.drawArc(ret, int((90 + k * 120) * 16), int(80 * 16))
    nucleo = QRadialGradient(c, R * 0.42)
    nucleo.setColorAt(0, QColor(255, 255, 255))
    nucleo.setColorAt(0.5, QColor(r, g, b))
    nucleo.setColorAt(1, QColor(r, g, b, 0))
    p.setPen(Qt.NoPen)
    p.setBrush(nucleo)
    p.drawEllipse(c, R * 0.42, R * 0.42)
    p.end()
    return pm


def icone(estado: str = "ouvindo") -> QIcon:
    ic = QIcon()
    for t in (16, 20, 24, 32, 48, 64, 128, 256):
        ic.addPixmap(pixmap_reator(estado, t))
    return ic


def salvar_ico(destino: Path) -> Path | None:
    """Gera o .ico do atalho de inicialização. Devolve None se o Qt não souber gravar .ico."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    ok = pixmap_reator("ouvindo", 256).save(str(destino), "ICO")
    return destino if ok else None
