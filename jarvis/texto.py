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
            antes_toks = list(orig[:i])
            while antes_toks and normalizar_token(antes_toks[-1]) in VOCATIVOS:  # "que horas são, ô Jarvis"
                antes_toks.pop()
            antes = " ".join(antes_toks).strip(" ,.;:!?-")
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


_TROCA_MOTOR = re.compile(r"^(?:usa|usar|use|modo|muda pro|muda para o|troca pro|troca para o|vai de)\s+(?:o\s+)?([a-z0-9]+)$")


def pedido_de_motor(texto: str) -> str | None:
    """'usa o Gemini' → 'gemini' (palavra falada; quem chama confere se é um motor). None = não é troca de motor."""
    n = re.sub(r"\s+", " ", _CORTESIA.sub(" ", " ".join(palavras(texto)))).strip()
    m = _TROCA_MOTOR.match(n)
    return m.group(1) if m else None


# --------------------------------------------------------------------------------------------- memória (ensinar)
# "Jarvis, lembra que eu acordo às 6" / "aprende que meu carro é um Civic" / "anota aí: a senha do wifi fica no Cofre"
_MEM_LEMBRAR = re.compile(
    r"^\s*(?:por favor,?\s+)?(?:se\s+)?"
    r"(?:lembr[ae]|aprend[ae]|anot[ae]|guard[ae]|grav[ae]|memoriz[ae]|registr[ae]|saiba|fica sabendo)(?:-se)?"
    r"(?:\s+(?:a[ií]|pra mim|para mim|isso|disso|bem|sempre))*"
    r"\s*(?:(?:de\s+)?que\s+|:\s*)(?P<fato>.+)$",
    re.I,
)
# "esquece que eu gosto de café" / "esquece o que eu falei sobre o carro" / "apaga da memória o café"
_MEM_ESQUECER = re.compile(
    r"^\s*(?:por favor,?\s+)?(?:"
    r"(?:esque[cç][ae]|esquecer)(?:-se)?(?:\s+(?:a[ií]|da (?:sua )?mem[oó]ria))?"
    r"|(?:apag[ae]|remov[ae]|tir[ae]|limp[ae])\s+da (?:sua )?mem[oó]ria"
    r")\s+(?:(?:de\s+)?que\s+|:\s*|(?:o\s+)?que eu (?:te )?(?:disse|falei|ensinei)\s+(?:sobre|de|do|da|que)\s+)?(?P<alvo>.+)$",
    re.I,
)
_MEM_ESQUECER_TUDO = {
    "esquece tudo que voce sabe de mim", "esquece tudo que voce sabe sobre mim", "esquece tudo sobre mim",
    "apaga a memoria", "apaga a sua memoria", "apaga toda a memoria", "apaga toda a sua memoria", "limpa a memoria",
    "limpa a sua memoria", "zera a memoria", "zera a sua memoria", "esquece tudo que eu te ensinei",
    "esquece tudo que te ensinei", "esquece tudo que eu te falei",
}
_MEM_LISTAR = (
    re.compile(r"^(?:me\s+(?:fala|diz|conta)\s+|e\s+)?(?:o\s+)?(?:que|quais|qual)\b.*\b(?:sabe|lembra|aprendeu|guardou|anotou|"
               r"sabes|conhece)\b.*\b(?:mim|meu respeito|minha vida|de eu)\b"),
    re.compile(r"\b(?:o que|que)\b.*\b(?:tem|ha|esta|guardou)\b.*\bna (?:sua )?memoria\b"),
    re.compile(r"^(?:le|lista|listar|mostra|fala)\s+(?:a\s+)?(?:sua\s+)?memoria$"),
    re.compile(r"^(?:o que|que)\s+(?:voce\s+)?(?:aprendeu|anotou|guardou)(?:\s+ate agora)?$"),
)


def comando_memoria(texto: str) -> tuple[str, str] | None:
    """
    Pedidos para ENSINAR o Jarvis. Devolve (acao, argumento) ou None:
      ("lembrar", fato) · ("esquecer", trecho) · ("esquecer_tudo", "") · ("listar", "")
    "esquece isso" e "esquece tudo" (sozinhos) são comandos locais de cancelar/conversa nova, não memória.
    """
    t = (texto or "").strip()
    n = " ".join(palavras(t))
    if not n:
        return None
    n_sem_cortesia = re.sub(r"\s+", " ", re.sub(r"\b(por favor|ai|ja)\b", " ", n)).strip()
    if n_sem_cortesia in _MEM_ESQUECER_TUDO:
        return ("esquecer_tudo", "")
    if any(r.search(n) for r in _MEM_LISTAR):
        return ("listar", "")
    m = _MEM_ESQUECER.match(t)
    if m:
        alvo = m.group("alvo").strip(" ,.;:!?")
        if " ".join(palavras(alvo)) not in ("isso", "tudo", "", "disso"):
            return ("esquecer", alvo)
    m = _MEM_LEMBRAR.match(t)
    if m:
        fato = m.group("fato").strip(" ,;:")
        if fato and not fato.rstrip().endswith("?") and len(palavras(fato)) >= 2:
            return ("lembrar", fato)
    return None


# --------------------------------------------------------------------------------------------- roteamento de IA
# O modo automático separa o pedido em 4 tipos e cada tipo tem uma fila de IAs (a 1ª disponível responde; se falhar,
# tenta a próxima). As filas ficam em config.json → roteamento (ROTAS_PADRAO é o padrão):
#   pc          mexer no computador (abrir, criar, arquivo, git, projeto...) → Claude Code (tem ferramentas de verdade)
#   internet    pesquisa, notícia, clima, cotação → Gemini CLI (busca do Google embutida; poupa o limite do Claude)
#   inteligente raciocínio pesado (comparar, analisar, calcular, planejar, pedido longo) → o modelo mais inteligente grátis
#   rapido      papo, comentário, curiosidade, conta simples → o modelo mais rápido (Nemotron lightning, ~0,5 s)
RADICAIS_PC = (
    "abr", "cri", "arquiv", "past", "projet", "codig", "program", "script", "rod", "execut", "instal", "commit",
    "edit", "corrig", "consert", "deploy", "servidor", "planilh", "document", "email", "mand", "envi", "baix",
    "organiz", "apag", "delet", "renome", "mov", "termin", "comand", "vps", "git", "github", "kairos", "mesa",
    "trader", "brain", "cofre", "lembr", "agend", "calendar", "naveg",
)
RADICAIS_INTERNET = (
    "pesquis", "busc", "procur", "noticia", "cotac", "site", "pagin", "clima", "chov", "temperatur", "previs",
    "dolar", "bitcoin", "placar",
)
FRASES_PC = (
    "no meu computador", "no pc", "le o", "leia o", "le a", "leia a", "da uma olhada", "verifica", "verifique",
    "checa", "cheque", "status do", "status da", "git", "vps", "pc",
)
FRASES_INTERNET = (
    "na internet", "na web", "hoje no mundo", "o tempo hoje", "como esta o tempo", "quanto esta", "quanto ta",
    "quem ganhou", "resultado do jogo",
)
RADICAIS_DIFICIL = (
    "compar", "analis", "calcul", "resolv", "demonstr", "estrateg", "planej", "otimiz", "raciocin", "deduz",
    "equac", "integral", "derivad", "probabil", "estatist", "argument", "vantag", "desvantag", "aprofund",
    "detalhad", "minucios", "complex", "dilema", "enigma", "charada", "logica",
)
FRASES_DIFICIL = (
    "pensa bem", "pense bem", "com calma", "passo a passo", "qual a diferenca", "qual e a diferenca",
    "pros e contras", "o que voce acha melhor", "me ajuda a decidir",
)
PALAVRAS_PEDIDO_LONGO = 30  # pedido falado com mais de 30 palavras costuma ser complexo
ROTAS_PADRAO: dict[str, list[str]] = {
    "rapido": ["nemotron", "gemini", "claude"],
    "inteligente": ["nvidia_pro", "gemini", "claude", "nemotron"],
    "internet": ["gemini", "claude"],
    "pc": ["claude", "codex", "gemini"],
}
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


def _casa(texto: str, radicais: tuple[str, ...], frases: tuple[str, ...]) -> bool:
    toks = palavras(texto)
    junto = " " + " ".join(toks) + " "
    if any(f" {f} " in junto for f in frases):
        return True
    return any(len(t) >= _MIN_TAM_RADICAL and t.startswith(r) for t in toks for r in radicais)


def parece_pc(texto: str) -> bool:
    return _casa(texto, RADICAIS_PC, FRASES_PC)


def parece_internet(texto: str) -> bool:
    return _casa(texto, RADICAIS_INTERNET, FRASES_INTERNET)


def parece_acao(texto: str) -> bool:
    """Precisa de ferramenta (mexer no PC ou buscar na internet)?"""
    return parece_pc(texto) or parece_internet(texto)


def parece_dificil(texto: str) -> bool:
    return len(palavras(texto)) > PALAVRAS_PEDIDO_LONGO or _casa(texto, RADICAIS_DIFICIL, FRASES_DIFICIL)


# Substantivos que aparecem em pergunta teórica ("compara Python e Rust para um servidor web") e, sozinhos, não provam
# que é para mexer no PC/internet. Verbos de ação ("abre", "cria", "pesquisa") e assuntos do dia ("clima") provam.
_FRACOS = ("servidor", "program", "script", "document", "comand", "termin", "planilh", "email", "codig", "site", "pagin")


def categoria(texto: str) -> str:
    """pc | internet | inteligente | rapido (ver ROTAS_PADRAO)."""
    forte_pc = _casa(texto, tuple(r for r in RADICAIS_PC if r not in _FRACOS), FRASES_PC)
    forte_net = _casa(texto, tuple(r for r in RADICAIS_INTERNET if r not in _FRACOS), FRASES_INTERNET)
    if forte_pc:
        return "pc"
    if forte_net:
        return "internet"
    if parece_dificil(texto):
        return "inteligente"
    if parece_pc(texto):
        return "pc"
    if parece_internet(texto):
        return "internet"
    return "rapido"


def _prefixos_de(apelido: str) -> list[list[str]]:
    a = normalizar_token(apelido)
    return [["pergunta", "pro", a], ["pergunta", "para", "o", a], ["pergunta", "ao", a], ["pede", "pro", a], ["no", a], [a]]


def rotear(texto: str, modo: str, disponiveis: set[str], extras: list[str] | None = None,
           rotas: dict[str, list[str]] | None = None, apelidos: dict[str, str] | None = None) -> tuple[list[str], str, str]:
    """
    Devolve (fila_de_motores, texto_sem_o_prefixo, categoria). Fila vazia = nenhuma IA disponível.
    Ordem: 1) pedido explícito na frase ("pergunta pro Claude ...", "pergunta pro Kimi ..."); 2) modo fixo;
    3) automático: fila da categoria (rotas); 4) o resto do que estiver disponível, como reserva.
    `apelidos`: nome falado → motor (ex.: {"kimi": "nvidia_pro"}).
    """
    rotas = {**ROTAS_PADRAO, **(rotas or {})}
    prefixos = {k: list(v) for k, v in PREFIXOS_MOTOR.items()}
    for nome in extras or []:
        prefixos.setdefault(nome, []).extend(_prefixos_de(nome))
    for falado, nome in (apelidos or {}).items():
        prefixos.setdefault(nome, []).extend(_prefixos_de(falado))
    # prefixo mais longo primeiro: "pergunta pro gemini" antes de "gemini"
    for nome in prefixos:
        prefixos[nome].sort(key=len, reverse=True)

    cat = categoria(texto)
    reserva = [n for n in (*rotas.get("rapido", []), *rotas.get("pc", []), "claude", "nemotron", *(extras or []))
               if n in disponiveis]

    def com_reserva(primeiros: list[str]) -> list[str]:
        fila: list[str] = []
        for n in (*primeiros, *reserva):
            if n in disponiveis and n not in fila:
                fila.append(n)
        return fila

    for nome, lista in prefixos.items():
        for p in lista:
            resto = _tirar_prefixo(texto, p)
            if resto is not None and nome in disponiveis:
                return com_reserva([nome]), (resto or texto), cat

    if modo and modo != "auto" and modo in disponiveis:
        return com_reserva([modo]), texto, cat
    return com_reserva(rotas.get(cat, [])), texto, cat


def escolher_motor(texto: str, modo: str, disponiveis: set[str], extras: list[str] | None = None,
                   rotas: dict[str, list[str]] | None = None, apelidos: dict[str, str] | None = None) -> tuple[str | None, str]:
    """Atalho de `rotear`: só o 1º motor da fila (None = nenhuma IA disponível)."""
    fila, texto, _ = rotear(texto, modo, disponiveis, extras, rotas, apelidos)
    return (fila[0] if fila else None), texto


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
