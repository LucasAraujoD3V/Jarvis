"""Memória persistente (fatos ensinados) e conversa compartilhada entre IAs — tudo em disco.
Rodar: python -m unittest discover -s tests -v
"""

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
from jarvis.motores import MotorClaude, MotorCLI, MotorNemotron, nota_seguimento, regras_voz  # noqa: E402
from jarvis.texto import comando_local, comando_memoria  # noqa: E402


class TestComandoMemoria(unittest.TestCase):
    def test_lembrar(self):
        self.assertEqual(comando_memoria("lembra que eu gosto de café sem açúcar"), ("lembrar", "eu gosto de café sem açúcar"))
        self.assertEqual(comando_memoria("Aprende que meu carro é um Civic"), ("lembrar", "meu carro é um Civic"))
        self.assertEqual(comando_memoria("anota aí: a reunião de segunda é às 9"), ("lembrar", "a reunião de segunda é às 9"))
        self.assertEqual(comando_memoria("lembre-se de que eu moro em Goiânia"), ("lembrar", "eu moro em Goiânia"))

    def test_esquecer(self):
        self.assertEqual(comando_memoria("esquece que eu gosto de café"), ("esquecer", "eu gosto de café"))
        self.assertEqual(comando_memoria("esquece o que eu falei sobre o carro"), ("esquecer", "o carro"))
        self.assertEqual(comando_memoria("apaga da memória o café"), ("esquecer", "o café"))
        self.assertEqual(comando_memoria("esquece tudo que você sabe de mim"), ("esquecer_tudo", ""))

    def test_listar(self):
        for f in ("o que você sabe de mim?", "O que você sabe sobre mim", "o que você lembra de mim",
                  "o que tem na sua memória", "me fala o que você sabe de mim", "o que você aprendeu"):
            self.assertEqual(comando_memoria(f), ("listar", ""), f)

    def test_nao_e_memoria(self):
        for f in ("que horas são", "abre o navegador", "apaga o arquivo teste.txt", "lembra que horas são?",
                  "me lembra de ligar pro João às 5", "esquece isso", "esquece tudo", "você sabe que dia é hoje"):
            self.assertIsNone(comando_memoria(f), f)
        # os comandos curtos continuam sendo locais (cancelar / conversa nova)
        self.assertEqual(comando_local("esquece isso"), "cancelar")
        self.assertEqual(comando_local("esquece tudo"), "nova_conversa")


class TestMemoria(unittest.TestCase):
    def setUp(self):
        self.pasta = Path(tempfile.mkdtemp())
        self.arq = self.pasta / "memoria.json"

    def test_persiste_entre_instancias(self):  # = reiniciar o Jarvis
        m = Memoria(self.arq)
        self.assertTrue(m.lembrar("eu gosto de café sem açúcar"))
        self.assertTrue(m.lembrar("meu carro é um Civic"))
        m2 = Memoria(self.arq)
        self.assertEqual(m2.listar(), ["Eu gosto de café sem açúcar.", "Meu carro é um Civic."])
        self.assertIn("café sem açúcar", m2.para_prompt())
        self.assertIn("'eu' = o usuário", m2.para_prompt())

    def test_nao_duplica(self):
        m = Memoria(self.arq)
        self.assertTrue(m.lembrar("eu gosto de café"))
        self.assertFalse(m.lembrar("Eu gosto de café."))
        self.assertEqual(len(m.listar()), 1)

    def test_esquecer_pelo_assunto(self):
        m = Memoria(self.arq)
        m.lembrar("eu gosto de café sem açúcar")
        m.lembrar("meu carro é um Civic prata")
        self.assertEqual(m.esquecer("o carro"), ["Meu carro é um Civic prata."])
        self.assertEqual(m.esquecer("futebol"), [])
        self.assertEqual(Memoria(self.arq).listar(), ["Eu gosto de café sem açúcar."])

    def test_esquecer_tudo_guarda_copia(self):
        m = Memoria(self.arq)
        m.lembrar("eu gosto de café")
        self.assertEqual(m.esquecer_tudo(), 1)
        self.assertEqual(m.listar(), [])
        bak = json.loads((self.pasta / "memoria.bak.json").read_text(encoding="utf-8"))
        self.assertEqual(len(bak["fatos"]), 1)

    def test_vazia_nao_vai_pro_prompt(self):
        self.assertEqual(Memoria(self.arq).para_prompt(), "")


class TestConversa(unittest.TestCase):
    def setUp(self):
        self.arq = Path(tempfile.mkdtemp()) / "conversa.json"

    def test_persiste_e_limita(self):
        c = Conversa(self.arq, max_turnos=3)
        for i in range(5):
            c.adicionar(f"pergunta {i}", f"resposta {i}", "nemotron")
        c2 = Conversa(self.arq, max_turnos=3)
        self.assertEqual([t["pedido"] for t in c2.turnos], ["pergunta 2", "pergunta 3", "pergunta 4"])
        self.assertEqual(c2.mensagens(1), [{"role": "user", "content": "pergunta 4"}, {"role": "assistant", "content": "resposta 4"}])

    def test_expira(self):
        c = Conversa(self.arq, expira_s=60)
        self.assertFalse(c.expirou())  # vazia não expira
        c.adicionar("oi", "Olá.", "nemotron")
        self.assertFalse(c.expirou())
        self.assertTrue(c.expirou(agora=time.time() + 120))
        self.assertFalse(Conversa(self.arq, expira_s=0).expirou(agora=time.time() + 10**6))  # 0 = nunca

    def test_recente_depois_do_claude(self):
        c = Conversa(self.arq)
        c.adicionar("a", "A.", "nemotron")
        c.adicionar("b", "B.", "claude")
        c.adicionar("c", "C.", "gemini")
        self.assertEqual([t["pedido"] for t in c.recente(6, depois_do_motor="claude")], ["c"])
        self.assertEqual([t["pedido"] for t in c.recente(6)], ["a", "b", "c"])


class TestContextoNasIAs(unittest.TestCase):
    """A memória tem de chegar no prompt de TODAS as IAs."""

    def setUp(self):
        pasta = Path(tempfile.mkdtemp())
        self.mem = Memoria(pasta / "memoria.json")
        self.mem.lembrar("meu nome é Lucas")
        self.conv = Conversa(pasta / "conversa.json")
        self.conv.adicionar("quanto é 2 mais 2", "Quatro.", "nemotron")

    def test_nemotron(self):
        m = MotorNemotron({}, "nvapi-teste", "Jarvis", [])
        m.memoria, m.conversa = self.mem, self.conv
        enviados = []
        m._post = lambda corpo: enviados.append(corpo) or {"choices": [{"message": {"content": "Oi, Lucas."}}]}
        m.perguntar("oi", threading.Event())
        msgs = enviados[0]["messages"]
        self.assertIn("Meu nome é Lucas", msgs[0]["content"])
        self.assertEqual(msgs[1:3], self.conv.mensagens())  # conversa compartilhada vira histórico do chat
        self.assertEqual(msgs[-1], {"role": "user", "content": "oi"})

    def test_claude(self):
        m = MotorClaude({}, Path("."), "Jarvis", [])
        m.exe = "claude"
        m.memoria, m.conversa = self.mem, self.conv
        args = m._comando("oi", retomar=True)
        sistema = args[args.index("--append-system-prompt") + 1]
        self.assertIn("Meu nome é Lucas", sistema)
        self.assertIn("quanto é 2 mais 2", sistema)  # sem sessão: recebe a conversa recente
        m.sessao = "abc"
        args = m._comando("oi", retomar=True)
        self.assertEqual(args[args.index("--resume") + 1], "abc")

    def test_cli_pelo_stdin(self):
        # "CLI" falsa: um python que devolve o que recebeu pela entrada padrão
        eco = [sys.executable, "-c", "import sys; sys.stdout.reconfigure(encoding='utf-8'); "
               "sys.stdin.reconfigure(encoding='utf-8'); print(sys.stdin.read())"]
        m = MotorCLI("eco", {"ativo": True, "comando": eco}, Path("."), "Jarvis", [])
        m.memoria, m.conversa = self.mem, self.conv
        saida = m.perguntar("que horas são", threading.Event())
        self.assertIn("Meu nome é Lucas", saida)
        self.assertIn("quanto é 2 mais 2", saida)
        self.assertIn("Pedido do usuário: que horas são", saida)

    def test_cli_saida_json(self):
        js = [sys.executable, "-c", "import sys, json; sys.stdin.read(); print('aviso qualquer'); "
              "print(json.dumps({'response': 'Olá!', 'stats': {}}))"]
        m = MotorCLI("js", {"ativo": True, "comando": js, "saida_json": "response"}, Path("."))
        self.assertEqual(m.perguntar("oi", threading.Event()), "Olá!")


class TestConversaNatural(unittest.TestCase):
    def test_prompt_trata_comentario(self):
        r = regras_voz("Jarvis", [])
        self.assertIn("agora sim hein", r)
        self.assertIn("Nunca responda 'não entendi o pedido'", r)

    def test_nota_de_seguimento_permite_ignorar(self):
        self.assertIn("IGNORAR", nota_seguimento("Jarvis"))


if __name__ == "__main__":
    unittest.main()
