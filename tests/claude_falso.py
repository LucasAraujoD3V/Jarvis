"""
Imita o `claude -p --input-format stream-json --output-format stream-json --include-partial-messages` (formato visto no
Claude Code 2.1 em 22/11/2026) para testar o MotorClaude sem rede e sem gastar a assinatura.

Comportamento pelo conteúdo da mensagem:
  "LOGIN"      → result com is_error (sessão OAuth expirada)
  "MORRE"      → o processo sai no meio da resposta
  "FERRAMENTA" → usa uma ferramenta antes de responder
  "DEMORA"     → espera 30 s (para testar cancelamento)
  outro        → responde "Oi, Lucas. Tudo certo por aqui." em pedaços
--resume sessao-inexistente → sai na hora com erro (sessão que não existe mais)
Cada execução acrescenta uma linha JSON com os argumentos em $CLAUDE_FALSO_LOG.
"""

import json
import os
import sys
import time
import uuid

sys.stdout.reconfigure(encoding="utf-8")
sys.stdin.reconfigure(encoding="utf-8")
args = sys.argv[1:]
if os.environ.get("CLAUDE_FALSO_LOG"):
    with open(os.environ["CLAUDE_FALSO_LOG"], "a", encoding="utf-8") as f:
        f.write(json.dumps(args, ensure_ascii=False) + "\n")

sessao = args[args.index("--resume") + 1] if "--resume" in args else str(uuid.uuid4())
if sessao == "sessao-inexistente":
    print(f"No conversation found with session ID: {sessao}", file=sys.stderr)
    sys.exit(1)


def ev(obj):
    obj.setdefault("session_id", sessao)
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def stream(evento):
    ev({"type": "stream_event", "event": evento, "parent_tool_use_id": None})


def falar(texto):
    stream({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
    for i in range(0, len(texto), 4):
        stream({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": texto[i:i + 4]}})


iniciou = False
for linha in sys.stdin:
    if not linha.strip():
        continue
    msg = json.loads(linha)
    conteudo = msg["message"]["content"]
    if not iniciou:
        ev({"type": "system", "subtype": "init", "model": "falso"})
        iniciou = True
    if "LIMITE" in conteudo and not os.environ.get("CLAUDE_CONFIG_DIR"):
        ev({"type": "result", "subtype": "success", "is_error": True, "result": "Claude AI usage limit reached|4102444800"})
        continue
    if "LIMITE" in conteudo:
        resposta = "Respondido pela conta 2."
        falar(resposta)
        ev({"type": "result", "subtype": "success", "is_error": False, "result": resposta})
        continue
    if "LOGIN" in conteudo:
        ev({"type": "assistant", "message": {"content": [{"type": "text", "text": "Failed to authenticate"}]}})
        ev({"type": "result", "subtype": "success", "is_error": True,
            "result": "Failed to authenticate: OAuth session expired and could not be refreshed"})
        continue
    if "MORRE" in conteudo:
        falar("Começando a")
        sys.exit(2)
    if "DEMORA" in conteudo:
        time.sleep(30)
    resposta = "Oi, Lucas. Tudo certo por aqui."
    if "NAO_E_COMIGO" in conteudo:
        resposta = "IGNORAR"
    if "FERRAMENTA" in conteudo:
        stream({"type": "content_block_start", "index": 0,
                "content_block": {"type": "tool_use", "name": "Bash", "id": "t1", "input": {}}})
        # resposta do subagente/ferramenta não pode vazar para a fala
        ev({"type": "stream_event", "parent_tool_use_id": "t1",
            "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "SEGREDO"}}})
        resposta = "Pronto, abri o Chrome."
    falar(resposta)
    ev({"type": "result", "subtype": "success", "is_error": False, "result": resposta})
