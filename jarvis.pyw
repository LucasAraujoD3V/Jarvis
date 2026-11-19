"""Jarvis — assistente de voz (Claude Code + Nemotron) na bandeja do Windows. Ponto de entrada."""

import os
import sys

# pythonw não tem console: sys.stdout/stderr viram None e algumas bibliotecas quebram ao escrever neles
if sys.stdout is None or sys.stderr is None:
    _nulo = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = sys.stdout or _nulo
    sys.stderr = sys.stderr or _nulo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from jarvis.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
