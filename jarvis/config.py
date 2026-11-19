"""
Configuração do Jarvis.

- config.json (versionado): comportamento padrão — palavra de ativação, voz, motores de IA, estilo de resposta.
- .env (NÃO versionado): segredos. Hoje só NVIDIA_API_KEY (chave do Nemotron). Todas as credenciais também ficam no
  Cofre.md, na raiz da pasta Data, fora de qualquer repositório.
- preferencias.json (NÃO versionado): o que muda pela bandeja/painel (motor escolhido, popup, posição do popup).
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ARQ_CONFIG = RAIZ / "config.json"
ARQ_ENV = RAIZ / ".env"
ARQ_PREFS = RAIZ / "preferencias.json"
PASTA_LOGS = RAIZ / "logs"
PASTA_ASSETS = RAIZ / "assets"


def carregar_config() -> dict:
    with open(ARQ_CONFIG, "r", encoding="utf-8") as f:
        return json.load(f)


def caminho(p: str | os.PathLike) -> Path:
    """Caminho do config: relativo à pasta do projeto, com ~ e variáveis de ambiente expandidos."""
    q = Path(os.path.expandvars(os.path.expanduser(str(p))))
    return q if q.is_absolute() else (RAIZ / q).resolve()


def ler_env() -> dict[str, str]:
    """Lê o .env (KEY=VALUE, # comentário). Variáveis de ambiente do sistema têm prioridade."""
    valores: dict[str, str] = {}
    if ARQ_ENV.exists():
        for linha in ARQ_ENV.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            k, v = linha.split("=", 1)
            valores[k.strip()] = v.strip().strip('"').strip("'")
    for k in list(valores):
        if os.environ.get(k):
            valores[k] = os.environ[k]
    for k in ("NVIDIA_API_KEY",):
        if k not in valores and os.environ.get(k):
            valores[k] = os.environ[k]
    return valores


def salvar_env(chave: str, valor: str) -> None:
    """Grava/atualiza uma linha do .env sem mexer nas outras."""
    linhas = ARQ_ENV.read_text(encoding="utf-8").splitlines() if ARQ_ENV.exists() else []
    nova = f"{chave}={valor}"
    for i, linha in enumerate(linhas):
        if linha.strip().startswith(f"{chave}=") or linha.strip().startswith(f"# {chave}="):
            linhas[i] = nova
            break
    else:
        linhas.append(nova)
    ARQ_ENV.write_text("\n".join(linhas) + "\n", encoding="utf-8")


class Preferencias:
    """Dicionário persistido em preferencias.json (gravação atômica, segura entre threads)."""

    def __init__(self, arquivo: Path = ARQ_PREFS):
        self.arquivo = arquivo
        self._lock = threading.Lock()
        try:
            self._dados = json.loads(arquivo.read_text(encoding="utf-8")) if arquivo.exists() else {}
        except (OSError, json.JSONDecodeError):
            self._dados = {}

    def get(self, chave, padrao=None):
        with self._lock:
            return self._dados.get(chave, padrao)

    def set(self, chave, valor) -> None:
        with self._lock:
            self._dados[chave] = valor
            tmp = self.arquivo.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._dados, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.arquivo)
