"""Jarvis — assistente de voz (Claude Code + Nemotron) na bandeja do Windows. Ponto de entrada."""

import os
import sys

# pythonw não tem console: sys.stdout/stderr viram None e algumas bibliotecas quebram ao escrever neles
if sys.stdout is None or sys.stderr is None:
    _nulo = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = sys.stdout or _nulo
    sys.stderr = sys.stderr or _nulo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Queda nativa (DLL) no pythonw não deixa rastro nenhum: o faulthandler grava a pilha de todas as threads aqui.
# (22/11/2026: no notebook, Python 3.14, uma queda 0xc0000005 em python314.dll fechou o Jarvis sem nada no log.)
try:
    import faulthandler

    os.makedirs(os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs"), exist_ok=True)
    _falhas = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "falhas.log"), "a", encoding="utf-8")
    faulthandler.enable(file=_falhas, all_threads=True)
except Exception:  # noqa: BLE001
    pass

from jarvis.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
