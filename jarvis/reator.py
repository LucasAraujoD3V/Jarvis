"""
O "reator" do Jarvis: anéis girando, arcos, marcações e um núcleo que pulsa com a voz.
Só desenho (QPainter) — usado pelo popup e pelo painel, em qualquer tamanho.

Cada estado tem cor e velocidade:
  ouvindo  ciano calmo        atento  ciano, núcleo segue o microfone
  pensando âmbar, giro rápido falando ciano claro, barras radiais no ritmo da voz
  desligado cinza, quase parado
"""

from __future__ import annotations

import math
import random

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QRadialGradient

CORES = {
    "ouvindo": (0, 210, 255),
    "atento": (0, 225, 255),
    "pensando": (255, 176, 46),
    "falando": (70, 235, 255),
    "desligado": (110, 125, 140),
    "erro": (255, 70, 100),
}
# graus por segundo de cada anel (positivo = horário)
VELOCIDADES = {
    "ouvindo": (8, -30, 18, 60),
    "atento": (12, -45, 26, 90),
    "pensando": (40, -220, 120, 320),
    "falando": (16, -70, 36, 140),
    "desligado": (2, -4, 2, 6),
}
N_BARRAS = 72


class Reator:
    def __init__(self):
        self.estado = "ouvindo"
        self.t = 0.0
        self.angulos = [0.0, 0.0, 0.0, 0.0]
        self.vel = list(VELOCIDADES["ouvindo"])
        self.cor = list(CORES["ouvindo"])
        self.nivel = 0.0
        self.alvo_nivel = 0.0
        self.barras = [0.0] * N_BARRAS
        self._fase = [random.random() * math.tau for _ in range(N_BARRAS)]

    def definir_estado(self, e: str) -> None:
        self.estado = e if e in CORES else "ouvindo"

    def definir_nivel(self, v: float) -> None:
        self.alvo_nivel = max(0.0, min(1.0, float(v)))

    def passo(self, dt: float) -> None:
        dt = min(dt, 0.1)
        self.t += dt
        k = min(1.0, dt * 4)
        alvo_v = VELOCIDADES.get(self.estado, VELOCIDADES["ouvindo"])
        alvo_c = CORES.get(self.estado, CORES["ouvindo"])
        for i in range(4):
            self.vel[i] += (alvo_v[i] - self.vel[i]) * k
            self.angulos[i] = (self.angulos[i] + self.vel[i] * dt) % 360
        for i in range(3):
            self.cor[i] += (alvo_c[i] - self.cor[i]) * k
        # sobe rápido, desce devagar (parece voz de verdade)
        sub = 18 if self.alvo_nivel > self.nivel else 6
        self.nivel += (self.alvo_nivel - self.nivel) * min(1.0, dt * sub)
        for i in range(N_BARRAS):
            onda = 0.55 + 0.45 * math.sin(self.t * (5 + (i % 7)) + self._fase[i])
            vizinho = 0.5 + 0.5 * math.sin(i * 0.7 + self.t * 3)
            alvo = self.nivel * (0.35 + 0.65 * onda * vizinho)
            self.barras[i] += (alvo - self.barras[i]) * min(1.0, dt * 14)

    # ------------------------------------------------------------------ desenho
    def _c(self, a: int, mais_claro: float = 0.0) -> QColor:
        r, g, b = (min(255, int(c + (255 - c) * mais_claro)) for c in self.cor)
        return QColor(r, g, b, max(0, min(255, a)))

    def _arco(self, p: QPainter, c: QPointF, r: float, ini: float, ext: float, largura: float, alfa: int, brilho=True) -> None:
        ret = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
        if brilho:
            pen = QPen(self._c(int(alfa * 0.25)), largura * 3.2)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawArc(ret, int(-ini * 16), int(-ext * 16))
        pen = QPen(self._c(alfa, 0.25), largura)
        pen.setCapStyle(Qt.FlatCap)
        p.setPen(pen)
        p.drawArc(ret, int(-ini * 16), int(-ext * 16))

    def pintar(self, p: QPainter, c: QPointF, R: float, fundo: bool = True) -> None:
        p.setRenderHint(QPainter.Antialiasing, True)
        a1, a2, a3, a4 = self.angulos
        nv = self.nivel
        desligado = self.estado == "desligado"

        if fundo:  # disco escuro translúcido + brilho: legível sobre qualquer papel de parede
            g = QRadialGradient(c, R * 1.18)
            g.setColorAt(0.0, QColor(4, 14, 24, 255))
            g.setColorAt(0.84, QColor(4, 14, 24, 250))
            g.setColorAt(1.0, QColor(4, 14, 24, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(g)
            p.drawEllipse(c, R * 1.18, R * 1.18)
        brilho = QRadialGradient(c, R * 1.1)
        brilho.setColorAt(0.0, self._c(int(70 + 90 * nv)))
        brilho.setColorAt(0.45, self._c(int(18 + 40 * nv)))
        brilho.setColorAt(1.0, self._c(0))
        p.setPen(Qt.NoPen)
        p.setBrush(brilho)
        p.drawEllipse(c, R * 1.1, R * 1.1)
        p.setBrush(Qt.NoBrush)

        # 1) marcações externas girando devagar
        p.save()
        p.translate(c)
        p.rotate(a1)
        for i in range(72):
            longa = i % 6 == 0
            p.setPen(QPen(self._c(200 if longa else 95), 2.0 if longa else 1.0))
            r0 = R * (0.93 if longa else 0.955)
            p.drawLine(QPointF(0, -r0), QPointF(0, -R * 0.99))
            p.rotate(5)
        p.restore()

        # 2) três arcos grossos (anti-horário)
        for k in range(3):
            self._arco(p, c, R * 0.86, a2 + k * 120, 68, R * 0.035, 230)
        # arcos finos de contraponto
        for k in range(2):
            self._arco(p, c, R * 0.80, -a2 * 0.6 + k * 180 + 40, 50, R * 0.012, 160, brilho=False)

        # 3) anel tracejado
        for k in range(24):
            self._arco(p, c, R * 0.72, a3 + k * 15, 9, R * 0.022, 150, brilho=False)

        # 4) círculo fino + dois arcos rápidos
        p.setPen(QPen(self._c(90), 1.2))
        p.drawEllipse(c, R * 0.62, R * 0.62)
        for k in range(2):
            self._arco(p, c, R * 0.62, a4 + k * 180, 110, R * 0.018, 220)

        # 5) "cometa" quando está pensando
        if self.estado == "pensando":
            for j in range(14):
                self._arco(p, c, R * 0.53, a4 * 1.6 - j * 6, 6, R * 0.02, int(240 * (1 - j / 14)), brilho=j == 0)

        # 6) barras radiais no ritmo da voz/microfone
        if nv > 0.01 and not desligado:
            p.save()
            p.translate(c)
            p.rotate(-a1 * 2)
            base = R * 0.44
            for i, b in enumerate(self.barras):
                comp = R * 0.16 * b
                if comp > 0.6:
                    p.setPen(QPen(self._c(int(120 + 135 * b), 0.3), max(1.4, R * 0.012)))
                    p.drawLine(QPointF(0, -base), QPointF(0, -base - comp))
                p.rotate(360 / N_BARRAS)
            p.restore()

        # 7) núcleo pulsando
        respira = 0.5 + 0.5 * math.sin(self.t * (1.4 if not desligado else 0.5))
        rn = R * (0.27 + 0.03 * respira + 0.11 * nv)
        nucleo = QRadialGradient(c, rn)
        nucleo.setColorAt(0.0, QColor(255, 255, 255, 250 if not desligado else 120))
        nucleo.setColorAt(0.35, self._c(235 if not desligado else 110, 0.35))
        nucleo.setColorAt(0.75, self._c(110 if not desligado else 50))
        nucleo.setColorAt(1.0, self._c(0))
        p.setPen(Qt.NoPen)
        p.setBrush(nucleo)
        p.drawEllipse(c, rn, rn)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(self._c(170, 0.2), 1.6))
        p.drawEllipse(c, R * 0.36, R * 0.36)
        # triângulo interno (assinatura do reator)
        p.save()
        p.translate(c)
        p.rotate(-a3 * 0.5)
        p.setPen(QPen(self._c(120, 0.5), 1.2))
        rt = R * 0.2
        pts = [QPointF(rt * math.cos(math.radians(90 + k * 120)), -rt * math.sin(math.radians(90 + k * 120))) for k in range(3)]
        for k in range(3):
            p.drawLine(pts[k], pts[(k + 1) % 3])
        p.restore()
