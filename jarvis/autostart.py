"""
Iniciar com o Windows: atalho "Jarvis.lnk" na pasta Inicializar do usuário
(%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup). Nada de registro, nada de serviço com admin:
apagar o atalho desfaz tudo.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PASTA_INICIAR = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
ATALHO = PASTA_INICIAR / "Jarvis.lnk"


def pythonw() -> Path:
    exe = Path(sys.executable)
    candidato = exe.with_name("pythonw.exe")
    return candidato if candidato.exists() else exe


def ativo() -> bool:
    return ATALHO.exists()


def ativar(script: Path, icone: Path | None = None) -> Path:
    import win32com.client

    PASTA_INICIAR.mkdir(parents=True, exist_ok=True)
    shell = win32com.client.Dispatch("WScript.Shell")
    sc = shell.CreateShortCut(str(ATALHO))
    sc.TargetPath = str(pythonw())
    sc.Arguments = f'"{script}" --boot'
    sc.WorkingDirectory = str(script.parent)
    sc.Description = "Jarvis — assistente de voz (Claude + Nemotron)"
    if icone and icone.exists():
        sc.IconLocation = str(icone)
    sc.save()
    return ATALHO


def desativar() -> None:
    try:
        ATALHO.unlink()
    except FileNotFoundError:
        pass
