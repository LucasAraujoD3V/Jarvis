"""
Regras de texto puras (sem áudio, sem rede, sem Qt) — fáceis de testar.

- extrair_comando: acha a palavra de ativação ("Jarvis") e devolve o comando falado junto.
- comando_local: frases curtas que o próprio assistente resolve (cancelar, trocar motor, desligar...).
- escolher_motor: decide qual IA responde (Claude, Nemotron ou outra) e tira o "pergunta pro X" da frase.
- limpar_para_fala / dividir_frases: transformam a resposta (às vezes em markdown) em texto bom de ouvir.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher


def sem_acentos(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def normalizar_token(tok: str) -> str:
    return re.sub(r"[^a-z0-9]", "", sem_acentos(tok))


def palavras(texto: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", sem_acentos(texto))


def parece(palavra: str, chave: str, limiar: float = 0.75) -> bool:
    """A palavra ouvida 'parece' a palavra-chave? (o reconhecimento erra: 'javis', 'jarvi', 'járvis'...)"""
    if palavra == chave:
        return True
    if len(palavra) < 4 or len(chave) < 4:
        return False
    return SequenceMatcher(None, palavra, chave).ratio() >= limiar


# Interjeições que costumam vir antes do nome e não são comando ("ok Jarvis", "ei Jarvis").
VOCATIVOS = {"ok", "okay", "ei", "oi", "hey", "ola", "alo", "e", "ai", "fala", "o"}


def extrair_comando(texto: str, chaves: list[str], limiar: float = 0.75) -> tuple[bool, str]:
    """
    Procura a palavra de ativação em qualquer posição.
    Devolve (achou, comando). O comando é o que vem DEPOIS do nome; se o nome veio no fim
    ("que horas são, Jarvis?"), o comando é o que veio ANTES.
    """
    chaves_n = [normalizar_token(c) for c in chaves if normalizar_token(c)]
    orig = (texto or "").split()
    for i, tok in enumerate(orig):
        n = normalizar_token(tok)
        if not n:
            continue
        if any(parece(n, c, limiar) for c in chaves_n):
            depois = " ".join(orig[i + 1:]).strip(" ,.;:!?-")
            antes = " ".join(orig[:i]).strip(" ,.;:!?-")
            if depois:
                return True, depois
            if antes and " ".join(palavras(antes)) not in VOCATIVOS:
                return True, antes
            return True, ""
    return False, ""


# --------------------------------------------------------------------------------------------- comandos locais
# Casamento EXATO da frase inteira (já sem acentos e sem "por favor"), para nunca confundir com um pedido de verdade:
# "desliga" desliga o assistente; "desliga o computador" vai para a IA.
COMANDOS_LOCAIS: dict[str, set[str]] = {
    "cancelar": {
        "cancela", "cancelar", "cancele", "para", "pare", "parar", "chega", "silencio", "fica quieto",
        "cala a boca", "esquece isso", "deixa pra la", "para de falar", "pare de falar", "parar de falar",
        "deixa", "nada", "nao precisa",
    },
    "encerrar": {"obrigado", "obrigada", "valeu", "so isso", "e isso", "nada nao", "tchau", "beleza obrigado", "ok obrigado"},
    "nova_conversa": {
        "nova conversa", "esquece tudo", "limpa a conversa", "limpar conversa", "limpar a conversa",
        "comeca de novo", "zera a conversa", "outro assunto",
    },
    "motor:claude": {"usa o claude", "usar o claude", "usa claude", "usar claude", "modo claude", "muda pro claude", "troca pro claude", "muda para o claude", "troca para o claude"},
    "motor:nemotron": {"usa o nemotron", "usar o nemotron", "usa nemotron", "usar nemotron", "modo nemotron", "muda pro nemotron", "troca pro nemotron", "muda para o nemotron", "troca para o nemotron"},
    "motor:auto": {"modo automatico", "usa o automatico", "motor automatico", "volta pro automatico", "modo auto"},
    "desligar": {"desliga", "desligar", "desligue", "se desliga", "vai dormir", "dormir", "desativar", "desativa", "desliga o microfone"},
    "reiniciar": {"reinicia", "reiniciar", "reinicie", "se reinicia", "reinicia o assistente"},
}

_CORTESIA = re.compile(r"\b(por favor|porfavor|agora|ai|ja|entao)\b")


def comando_local(texto: str) -> str | None:
    n = " ".join(palavras(texto))
    n = re.sub(r"\s+", " ", _CORTESIA.sub(" ", n)).strip()
    if not n:
        return None
    for acao, frases in COMANDOS_LOCAIS.items():
        if n in frases:
            return acao
    return None


# --------------------------------------------------------------------------------------------- roteamento de IA
# Pedidos que precisam de internet, de dados atuais ou de MEXER no computador vão para o Claude (tem ferramentas).
# Conversa, conta, explicação e curiosidade vão para o Nemotron (responde em ~1 s).
RADICAIS_ACAO = (
    "abr", "cri", "arquiv", "past", "projet", "codig", "program", "script", "rod", "execut", "instal", "commit",
    "edit", "corrig", "consert", "deploy", "servidor", "pesquis", "busc", "procur", "noticia", "cotac",
    "planilh", "document", "email", "mand", "envi", "baix", "organiz", "apag", "delet", "renome", "mov",
    "termin", "comand", "naveg", "site", "pagin", "vps", "git", "github", "clima", "chov", "temperatur",
    "previs", "dolar", "bitcoin", "kairos", "mesa", "trader", "brain", "cofre", "lembr", "agend", "calendar",
)
FRASES_ACAO = (
    "no meu computador", "no pc", "na internet", "na web", "hoje no mundo", "o tempo hoje", "como esta o tempo",
    "quanto esta", "quanto ta", "le o", "leia o", "le a", "leia a", "da uma olhada", "verifica", "verifique",
    "checa", "cheque", "status do", "status da", "git", "vps", "pc",
)
# radicais curtos demais casariam palavras comuns: só valem como palavra inteira começando assim e com tamanho mínimo
_MIN_TAM_RADICAL = 4

PREFIXOS_MOTOR: dict[str, list[list[str]]] = {
    "claude": [
        ["pergunta", "pro", "claude"], ["pergunta", "para", "o", "claude"], ["pergunta", "ao", "claude"],
        ["pede", "pro", "claude"], ["pede", "para", "o", "claude"], ["pede", "ao", "claude"],
        ["no", "claude"], ["claude"], ["cloud"],
    ],
    "nemotron": [
        ["pergunta", "pro", "nemotron"], ["pergunta", "para", "o", "nemotron"], ["pergunta", "ao", "nemotron"],
        ["pede", "pro", "nemotron"], ["no", "nemotron"], ["nemotron"],
    ],
}


def _tirar_prefixo(texto: str, prefixo: list[str]) -> str | None:
    """Se o texto começa com o prefixo (comparando sem acento/pontuação), devolve o resto ORIGINAL; senão None."""
    orig = texto.split()
    norm = [(i, normalizar_token(t)) for i, t in enumerate(orig)]
    norm = [(i, n) for i, n in norm if n]
    if len(norm) < len(prefixo):
        return None
    if [n for _, n in norm[: len(prefixo)]] != prefixo:
        return None
    ultimo = norm[len(prefixo) - 1][0]
    return " ".join(orig[ultimo + 1:]).strip(" ,.;:!?-")


def parece_acao(texto: str) -> bool:
    toks = palavras(texto)
    junto = " " + " ".join(toks) + " "
    if any(f" {f} " in junto for f in FRASES_ACAO):
        return True
    for t in toks:
        if len(t) < _MIN_TAM_RADICAL:
            continue
        if any(t.startswith(r) for r in RADICAIS_ACAO):
            return True
    return False


def escolher_motor(texto: str, modo: str, disponiveis: set[str], extras: list[str] | None = None) -> tuple[str | None, str]:
    """
    Devolve (nome_do_motor, texto_sem_o_prefixo). Nome None = nenhuma IA disponível.
    Ordem: 1) pedido explícito na frase ("pergunta pro Claude ..."); 2) modo fixo (claude/nemotron/...);
    3) automático: ação/internet → Claude, resto → Nemotron; 4) o que estiver disponível.
    """
    prefixos = dict(PREFIXOS_MOTOR)
    for nome in extras or []:
        prefixos.setdefault(nome, [["pergunta", "pro", nome], ["pergunta", "para", "o", nome], ["no", nome], [nome]])

    for nome, lista in prefixos.items():
        for p in lista:
            resto = _tirar_prefixo(texto, p)
            if resto is not None and nome in disponiveis:
                return nome, (resto or texto)

    if modo and modo != "auto" and modo in disponiveis:
        return modo, texto

    preferido = "claude" if parece_acao(texto) else "nemotron"
    if preferido in disponiveis:
        return preferido, texto
    for nome in ("claude", "nemotron", *(extras or [])):
        if nome in disponiveis:
            return nome, texto
    return None, texto


# --------------------------------------------------------------------------------------------- texto para fala
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍]")


def limpar_markdown(t: str) -> str:
    t = t or ""
    t = re.sub(r"<think>[\s\S]*?</think>", " ", t)
    t = re.sub(r"```[\s\S]*?```", " ", t)
    t = re.sub(r"`([^`\n]+)`", r"\1", t)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"^\s*#{1,6}\s*", "", t, flags=re.M)
    t = re.sub(r"\*{1,3}([^*\n]+)\*{1,3}", r"\1", t)
    t = re.sub(r"^\s*\|.*\|\s*$", " ", t, flags=re.M)
    t = re.sub(r"^\s*[-*•>]+\s+", "", t, flags=re.M)
    t = re.sub(r"^\s*\d+[.)]\s+", "", t, flags=re.M)
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"[-=_*]{3,}", " ", t)
    t = _EMOJI.sub("", t)
    # quebra de linha sem pontuação vira ponto, para a voz respirar
    linhas = [ln.strip() for ln in t.splitlines() if ln.strip()]
    t = " ".join(ln if re.search(r"[.!?:;,]$", ln) else ln + "." for ln in linhas)
    t = re.sub(r"\s+([.,;:!?])", r"\1", t)
    t = re.sub(r"([.!?]){2,}", r"\1", t)
    t = re.sub(r"\s{2,}", " ", t)
    return t.strip()


def dividir_frases(texto: str, max_trecho: int = 220) -> list[str]:
    """Quebra em trechos curtos (frases agrupadas) para começar a falar antes de sintetizar tudo."""
    frases = [f.strip() for f in re.split(r"(?<=[.!?;:])\s+", texto or "") if f.strip()]
    trechos: list[str] = []
    atual = ""
    for f in frases:
        while len(f) > max_trecho:  # frase gigante: corta na vírgula/espaço mais próximo
            corte = max(f.rfind(",", 0, max_trecho), f.rfind(" ", 0, max_trecho))
            corte = corte if corte > 40 else max_trecho
            if atual:
                trechos.append(atual)
                atual = ""
            trechos.append(f[:corte].strip(" ,"))
            f = f[corte:].strip(" ,")
        if atual and len(atual) + 1 + len(f) > max_trecho:
            trechos.append(atual)
            atual = f
        else:
            atual = f"{atual} {f}".strip()
    if atual:
        trechos.append(atual)
    return trechos


def limpar_para_fala(texto: str, max_chars: int = 600) -> tuple[str, bool]:
    """Devolve (texto_para_falar, cortado?). Corta no fim de frase antes do limite."""
    t = limpar_markdown(texto)
    if len(t) <= max_chars:
        return t, False
    frases = [f.strip() for f in re.split(r"(?<=[.!?])\s+", t) if f.strip()]
    saida = ""
    for f in frases:
        if len(saida) + len(f) + 1 > max_chars:
            break
        saida = f"{saida} {f}".strip()
    if not saida:
        saida = t[:max_chars].rsplit(" ", 1)[0] + "."
    return saida, True
