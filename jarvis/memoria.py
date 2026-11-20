"""
Memória do Jarvis — tudo em disco, para sobreviver a reinícios.

- Memoria: fatos que o usuário ENSINOU ("Jarvis, lembra que…", "aprende que…", "esquece…").
  Arquivo memoria.json (gitignorado: é pessoal). Vai no prompt de TODAS as IAs.
- Conversa: as últimas trocas (usuário ↔ Jarvis), venham da IA que vierem. Arquivo conversa.json (gitignorado).
  É o que dá continuidade: a IA rápida sabe o que o Claude acabou de responder e vice-versa, e depois de
  reiniciar o Jarvis a conversa continua de onde parou (até ficar velha: config conversa.nova_conversa_apos_min).
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import threading
import time
from difflib import SequenceMatcher
from pathlib import Path

from .texto import palavras

# palavras que não ajudam a achar qual fato esquecer ("esquece que EU gosto DE café" → "gosto", "cafe")
_VAZIAS = {
    "a", "o", "as", "os", "um", "uma", "de", "da", "do", "das", "dos", "e", "que", "eu", "me", "meu", "minha",
    "meus", "minhas", "mim", "na", "no", "nas", "nos", "em", "pra", "para", "por", "com", "se", "isso", "aquilo",
    "sobre", "voce", "te", "ja", "sou", "tenho", "esta", "estou", "ser", "foi", "falei", "disse",
}


def _gravar_json(arquivo: Path, dados) -> None:
    tmp = arquivo.with_suffix(".tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, arquivo)


def _ler_json(arquivo: Path, padrao):
    try:
        return json.loads(arquivo.read_text(encoding="utf-8")) if arquivo.exists() else padrao
    except (OSError, json.JSONDecodeError):
        return padrao


def _chaves(texto: str) -> set[str]:
    return {p for p in palavras(texto) if p not in _VAZIAS and len(p) > 1}


def _normal(texto: str) -> str:
    return " ".join(palavras(texto))


# ===================================================================================================== fatos
class Memoria:
    """Lista de fatos ensinados pelo usuário. Cada fato: {"texto": "...", "quando": "AAAA-MM-DD HH:MM"}."""

    def __init__(self, arquivo: Path, max_fatos: int = 300, max_chars_prompt: int = 5000):
        self.arquivo = Path(arquivo)
        self.max_fatos = max_fatos
        self.max_chars_prompt = max_chars_prompt
        self._lock = threading.Lock()
        dados = _ler_json(self.arquivo, {})
        self.fatos: list[dict] = [f for f in (dados.get("fatos") or []) if isinstance(f, dict) and f.get("texto")]

    def _salvar(self) -> None:
        _gravar_json(self.arquivo, {"fatos": self.fatos})

    @staticmethod
    def _limpar(fato: str) -> str:
        fato = re.sub(r"\s+", " ", (fato or "").strip(" \t\n,;:-"))
        if fato and fato[-1] not in ".!?":
            fato += "."
        return fato[:1].upper() + fato[1:]

    def lembrar(self, fato: str) -> bool:
        """Guarda o fato. False = já sabia (igual ou quase igual a um que já está guardado)."""
        fato = self._limpar(fato)
        if not _normal(fato):
            return False
        agora = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        with self._lock:
            n = _normal(fato)
            for f in self.fatos:
                if SequenceMatcher(None, _normal(f["texto"]), n).ratio() >= 0.92:
                    f["quando"] = agora
                    self._salvar()
                    return False
            self.fatos.append({"texto": fato, "quando": agora})
            self.fatos = self.fatos[-self.max_fatos:]
            self._salvar()
            return True

    def _pontuar(self, alvo: str, fato: str) -> float:
        ca, cf = _chaves(alvo), _chaves(fato)
        cobertura = len(ca & cf) / len(ca) if ca else 0.0
        return max(cobertura, SequenceMatcher(None, _normal(alvo), _normal(fato)).ratio())

    def esquecer(self, alvo: str) -> list[str]:
        """Apaga o fato que mais combina com `alvo` (e os empatados com ele). Devolve os textos apagados."""
        if not _normal(alvo):
            return []
        with self._lock:
            notas = [(self._pontuar(alvo, f["texto"]), i) for i, f in enumerate(self.fatos)]
            if not notas:
                return []
            melhor = max(n for n, _ in notas)
            if melhor < 0.6:
                return []
            apagar = {i for n, i in notas if n >= melhor - 1e-9}
            apagados = [self.fatos[i]["texto"] for i in sorted(apagar)]
            self.fatos = [f for i, f in enumerate(self.fatos) if i not in apagar]
            self._salvar()
            return apagados

    def esquecer_tudo(self) -> int:
        """Apaga todos os fatos (guarda uma cópia em memoria.bak.json, por segurança). Devolve quantos eram."""
        with self._lock:
            n = len(self.fatos)
            if n:
                _gravar_json(self.arquivo.with_name(self.arquivo.stem + ".bak.json"), {"fatos": self.fatos})
            self.fatos = []
            self._salvar()
            return n

    def listar(self) -> list[str]:
        with self._lock:
            return [f["texto"] for f in self.fatos]

    def para_prompt(self) -> str:
        """Bloco que vai no prompt das IAs (os fatos mais recentes primeiro, até max_chars_prompt)."""
        with self._lock:
            fatos = list(self.fatos)
        if not fatos:
            return ""
        linhas: list[str] = []
        total = 0
        for f in reversed(fatos):
            linha = f"- {f['texto']} (anotado em {f.get('quando', '?')[:10]})"
            if total + len(linha) > self.max_chars_prompt:
                break
            linhas.append(linha)
            total += len(linha) + 1
        return (
            "O que você sabe do usuário (foi ele quem te ensinou; nas frases, 'eu' = o usuário). "
            "Use quando ajudar, sem recitar à toa:\n" + "\n".join(reversed(linhas))
        )


# ===================================================================================================== conversa
class Conversa:
    """Últimas trocas usuário ↔ Jarvis, compartilhadas entre todas as IAs e gravadas em disco."""

    def __init__(self, arquivo: Path, max_turnos: int = 10, expira_s: float = 0):
        self.arquivo = Path(arquivo)
        self.max_turnos = max_turnos
        self.expira_s = expira_s  # 0 = nunca expira sozinha
        self._lock = threading.Lock()
        dados = _ler_json(self.arquivo, {})
        self.turnos: list[dict] = [t for t in (dados.get("turnos") or []) if isinstance(t, dict)]
        self.atualizado: float = float(dados.get("atualizado") or 0)

    def _salvar(self) -> None:
        _gravar_json(self.arquivo, {"atualizado": self.atualizado, "turnos": self.turnos})

    def expirou(self, agora: float | None = None) -> bool:
        """A conversa ficou velha demais (sem uso há mais de `expira_s`)? Conversa vazia nunca 'expira'."""
        if not self.expira_s or not self.atualizado:
            return False
        return (agora or time.time()) - self.atualizado > self.expira_s

    def adicionar(self, pedido: str, resposta: str, motor: str) -> None:
        with self._lock:
            self.turnos.append({"pedido": pedido, "resposta": resposta, "motor": motor,
                                "quando": _dt.datetime.now().strftime("%Y-%m-%d %H:%M")})
            self.turnos = self.turnos[-self.max_turnos:]
            self.atualizado = time.time()
            self._salvar()

    def limpar(self) -> None:
        with self._lock:
            self.turnos = []
            self.atualizado = 0
            self._salvar()

    def mensagens(self, turnos: int | None = None) -> list[dict]:
        """No formato de chat (OpenAI): [{"role": "user"...}, {"role": "assistant"...}, ...]."""
        with self._lock:
            sel = self.turnos[-(turnos or self.max_turnos):]
        msgs: list[dict] = []
        for t in sel:
            msgs += [{"role": "user", "content": t["pedido"]}, {"role": "assistant", "content": t["resposta"]}]
        return msgs

    def recente(self, turnos: int = 6, depois_do_motor: str | None = None) -> list[dict]:
        """Últimos turnos. Com `depois_do_motor`, só os que vieram DEPOIS da última resposta desse motor
        (o Claude já tem os dele na própria sessão)."""
        with self._lock:
            sel = list(self.turnos)
        if depois_do_motor:
            for i in range(len(sel) - 1, -1, -1):
                if sel[i].get("motor") == depois_do_motor:
                    sel = sel[i + 1:]
                    break
        return sel[-turnos:]

    @staticmethod
    def como_texto(turnos: list[dict]) -> str:
        if not turnos:
            return ""
        linhas = []
        for t in turnos:
            linhas.append(f"Usuário: {t['pedido']}")
            linhas.append(f"Jarvis ({t.get('motor', '?')}): {t['resposta']}")
        return "Conversa recente (para contexto):\n" + "\n".join(linhas)
