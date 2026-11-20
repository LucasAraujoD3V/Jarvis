"""
Compara modelos da API grátis da NVIDIA (build.nvidia.com) em INTELIGÊNCIA e LATÊNCIA, para escolher o "nvidia_pro"
(perguntas difíceis) e o rápido (papo) do Jarvis.

  python ferramentas/comparar_modelos.py                 # modelos padrão
  python ferramentas/comparar_modelos.py modelo1 modelo2 # só estes

Regras: 3 perguntas de raciocínio com resposta conferível; no máximo 60 s por modelo (o que passar disso é cortado);
modelos rodam em paralelo (o limite grátis é ~40 pedidos/min; aqui são ~21). Usa a NVIDIA_API_KEY do .env do Jarvis.
"""

from __future__ import annotations

import json
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jarvis.config import ler_env  # noqa: E402

URL = "https://integrate.api.nvidia.com/v1/chat/completions"
ORCAMENTO_S = 60.0
MODELOS = [
    "moonshotai/kimi-k3.1-pro",
    "mistralai/mistral-large-4-1t-a52b",
    "nvidia/nemotron-3-ultra-550b-a55b",
    "z-ai/glm-5.3-flash",
    "minimaxai/minimax-m3.1-flash",
    "nvidia/nemotron-3.5-super-vl-120b-a12b",
    "nvidia/nemotron-3.5-lightning-30b-a3b",
]
# (pergunta, regex da resposta certa)
PERGUNTAS = [
    ("Um taco e uma bola custam R$ 1,10 juntos. O taco custa R$ 1,00 a mais que a bola. Quanto custa a bola? "
     "Responda só o valor.", r"0[,.]05|5 centavos|cinco centavos"),
    ("Se 5 máquinas fazem 5 peças em 5 minutos, quanto tempo 100 máquinas levam para fazer 100 peças? "
     "Responda só o tempo.", r"\b5\s*min|cinco minutos"),
    ("Quantos números inteiros de 1 a 100 são divisíveis por 3 ou por 5? Responda só o número.", r"\b47\b|quarenta e sete"),
]
SISTEMA = "Responda em português do Brasil, curto, sem markdown."


def perguntar(chave: str, modelo: str, pergunta: str, timeout: float) -> tuple[str, float]:
    corpo = {"model": modelo, "messages": [{"role": "system", "content": SISTEMA}, {"role": "user", "content": pergunta}],
             "temperature": 0.2, "max_tokens": 2048, "stream": False}
    if "lightning" in modelo:  # é assim que o Jarvis usa o rápido: sem "pensar"
        corpo["chat_template_kwargs"] = {"enable_thinking": False}
    req = urllib.request.Request(URL, data=json.dumps(corpo).encode(), headers={
        "Authorization": f"Bearer {chave}", "Content-Type": "application/json", "Accept": "application/json"})
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        j = json.load(r)
    texto = j["choices"][0]["message"].get("content") or ""
    return re.sub(r"<think>[\s\S]*?</think>", "", texto).strip(), time.monotonic() - t0


def avaliar(chave: str, modelo: str, saida: dict) -> None:
    inicio = time.monotonic()
    res = {"acertos": 0, "tempos": [], "erros": [], "respostas": []}
    for pergunta, certo in PERGUNTAS:
        resta = ORCAMENTO_S - (time.monotonic() - inicio)
        if resta < 2:
            res["erros"].append("cortado (passou de 60 s)")
            break
        try:
            texto, dt = perguntar(chave, modelo, pergunta, timeout=resta)
        except urllib.error.HTTPError as e:
            res["erros"].append(f"HTTP {e.code}")
            continue
        except Exception as e:  # noqa: BLE001 — timeout, rede
            res["erros"].append(type(e).__name__)
            continue
        ok = bool(re.search(certo, texto, re.I))
        res["acertos"] += ok
        res["tempos"].append(dt)
        res["respostas"].append(("✔" if ok else "✘") + " " + texto.replace("\n", " ")[:70])
    saida[modelo] = res


def main() -> int:
    chave = ler_env().get("NVIDIA_API_KEY", "")
    if not chave:
        print("Sem NVIDIA_API_KEY no .env do Jarvis (cole a chave no painel).")
        return 1
    modelos = sys.argv[1:] or MODELOS
    saida: dict = {}
    threads = [threading.Thread(target=avaliar, args=(chave, m, saida)) for m in modelos]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    print(f"{'modelo':45} acertos  média   máx   obs")
    for m in sorted(modelos, key=lambda m: (-saida[m]["acertos"], sum(saida[m]["tempos"] or [99]) / max(1, len(saida[m]["tempos"])))):
        r = saida[m]
        t = r["tempos"]
        media = sum(t) / len(t) if t else float("nan")
        print(f"{m:45} {r['acertos']}/{len(PERGUNTAS)}    {media:5.1f}s {max(t) if t else float('nan'):5.1f}s  {', '.join(r['erros'])}")
        for resp in r["respostas"]:
            print(f"    {resp}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
