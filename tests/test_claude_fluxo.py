"""Testes do MotorClaude em processo fixo (stream-json), com um Claude falso (tests/claude_falso.py)."""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jarvis.memoria import Conversa, Memoria  # noqa: E402
from jarvis.motores import Cancelado, ErroMotor, MotorClaude  # noqa: E402

FALSO = str(Path(__file__).with_name("claude_falso.py"))


class TestClaudeFluxo(unittest.TestCase):
    def setUp(self):
        self.pasta = Path(tempfile.mkdtemp())
        self.log = self.pasta / "args.log"
        os.environ["CLAUDE_FALSO_LOG"] = str(self.log)
        self.m = MotorClaude({}, self.pasta, "Jarvis", ["Você trabalha para Lucas."])
        self.m.prefixo = [sys.executable, FALSO]
        self.m.memoria = Memoria(self.pasta / "memoria.json")
        self.m.conversa = Conversa(self.pasta / "conversa.json")

    def tearDown(self):
        self.m.encerrar()
        os.environ.pop("CLAUDE_FALSO_LOG", None)

    def execucoes(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [json.loads(l) for l in self.log.read_text(encoding="utf-8").splitlines() if l.strip()]

    def test_streaming_e_processo_fica_aberto(self):
        pedacos = []
        r = self.m.perguntar("oi", threading.Event(), ao_parcial=pedacos.append)
        self.assertEqual(r, "Oi, Lucas. Tudo certo por aqui.")
        self.assertGreater(len(pedacos), 3)  # chegou em pedaços, não de uma vez
        self.assertEqual("".join(pedacos), r)
        sessao = self.m.sessao
        self.assertTrue(sessao)
        self.assertEqual(self.m.perguntar("e aí", threading.Event()), "Oi, Lucas. Tudo certo por aqui.")
        self.assertEqual(len(self.execucoes()), 1)  # o mesmo processo respondeu as duas
        self.assertEqual(self.m.sessao, sessao)

    def test_prompt_tem_persona_regras_e_memoria(self):
        self.m.memoria.lembrar("meu café favorito é expresso")
        self.m.perguntar("oi", threading.Event())
        args = self.execucoes()[0]
        sistema = args[args.index("--append-system-prompt") + 1]
        self.assertIn("Você trabalha para Lucas.", sistema)
        self.assertIn("Start-Process", sistema)
        self.assertIn("expresso", sistema)
        self.assertNotIn("3 a 6 palavras", sistema)
        for flag in ("--input-format", "--include-partial-messages", "--strict-mcp-config", "--safe-mode"):
            self.assertIn(flag, args)
        self.assertIn(f"Sua pasta atual é {self.pasta}", sistema)
        self.assertIn("O que tem na pasta atual agora", sistema)
        self.assertIn("memoria.json", sistema)  # lista o nível de cima da pasta de trabalho
        self.assertIn("ANTES de qualquer busca", sistema)
        self.assertIn("não liste fontes", sistema)

    def test_safe_mode_so_se_a_versao_conhece(self):
        m = MotorClaude({}, self.pasta, "Jarvis", [])
        m.exe = "claude"
        m._ajuda = "  --model <model>\n  --resume\n"  # versão antiga, sem --safe-mode
        self.assertNotIn("--safe-mode", m._comando(retomar=False))
        m._ajuda = "  --safe-mode   Start with all customizations disabled\n"
        self.assertIn("--safe-mode", m._comando(retomar=False))
        m.modo_limpo = False
        self.assertNotIn("--safe-mode", m._comando(retomar=False))

    def test_memoria_nova_vai_na_mensagem_sem_reiniciar(self):
        self.m.perguntar("oi", threading.Event())
        self.m.memoria.lembrar("eu acordo às 6")
        msg = self.m._mensagem("bom dia")
        self.assertIn("acordo às 6", msg)
        self.assertNotIn("acordo às 6", self.m._mensagem("de novo"))  # só uma vez

    def test_ferramenta_avisa_e_nao_vaza_texto_de_subagente(self):
        ferramentas, pedacos = [], []
        r = self.m.perguntar("FERRAMENTA abre o chrome", threading.Event(), ao_parcial=pedacos.append,
                             ao_ferramenta=ferramentas.append)
        self.assertEqual(ferramentas, ["Bash"])
        self.assertEqual(r, "Pronto, abri o Chrome.")
        self.assertNotIn("SEGREDO", "".join(pedacos))

    def test_limite_troca_de_conta_e_avisa(self):
        self.m.contas = ["", str(self.pasta / "conta2")]
        pedacos = []
        r = self.m.perguntar("LIMITE oi", threading.Event(), ao_parcial=pedacos.append)
        self.assertEqual(r, "Respondido pela conta 2.")
        self.assertEqual(self.m.conta, 1)
        self.assertIn("passei para a outra", "".join(pedacos))
        self.assertTrue(self.m.disponivel)

    def test_mensagem_de_limite_de_sessao_2026(self):
        from jarvis.motores import _ERRO_LIMITE, fim_do_limite
        import time as _t

        msg = "You've hit your session limit · resets 4:10am (America/Sao_Paulo)"
        self.assertTrue(_ERRO_LIMITE.search(msg))
        volta = fim_do_limite(msg)
        self.assertTrue(_t.time() < volta <= _t.time() + 24 * 3600)

    def test_limite_sem_outra_conta(self):
        with self.assertRaises(ErroMotor) as ctx:
            self.m.perguntar("LIMITE oi", threading.Event())
        self.assertIn("limite", str(ctx.exception))
        self.assertFalse(self.m.disponivel)

    def test_sem_login_sai_da_fila(self):
        with self.assertRaises(ErroMotor) as ctx:
            self.m.perguntar("LOGIN", threading.Event())
        self.assertIn("sem login", str(ctx.exception))
        self.assertFalse(self.m.disponivel)
        self.assertIn("/login", self.m.motivo)

    def test_processo_morre_no_meio_nao_repete(self):
        # morreu no meio: como já tinha mandado texto, não repete (o Jarvis já estaria falando)
        with self.assertRaises(ErroMotor):
            self.m.perguntar("MORRE", threading.Event(), ao_parcial=lambda t: None)
        self.assertEqual(self.m.perguntar("oi", threading.Event()), "Oi, Lucas. Tudo certo por aqui.")

    def test_sessao_antiga_sumiu_comeca_outra(self):
        self.m.sessao = "sessao-inexistente"
        self.assertEqual(self.m.perguntar("oi", threading.Event()), "Oi, Lucas. Tudo certo por aqui.")
        self.assertNotEqual(self.m.sessao, "sessao-inexistente")
        self.assertEqual(len(self.execucoes()), 2)

    def test_retoma_sessao_salva(self):
        self.m.sessao = "abc-123"
        self.m.perguntar("oi", threading.Event())
        args = self.execucoes()[0]
        self.assertEqual(args[args.index("--resume") + 1], "abc-123")

    def test_cancelar_mata_o_processo(self):
        cancelar = threading.Event()
        threading.Timer(0.8, cancelar.set).start()
        t0 = time.monotonic()
        with self.assertRaises(Cancelado):
            self.m.perguntar("DEMORA", cancelar)
        self.assertLess(time.monotonic() - t0, 10)
        self.assertFalse(self.m.vivo())
        # o próximo pedido sobe outro processo retomando a mesma sessão
        self.assertEqual(self.m.perguntar("oi", threading.Event()), "Oi, Lucas. Tudo certo por aqui.")
        ultimo = self.execucoes()[-1]
        self.assertIn("--resume", ultimo)

    def test_aquecer_abre_antes_do_pedido(self):
        self.m.aquecer()
        self.assertTrue(self.m.vivo())
        self.m.perguntar("oi", threading.Event())
        self.assertEqual(len(self.execucoes()), 1)


if __name__ == "__main__":
    unittest.main()
