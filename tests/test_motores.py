"""Testes do motor Nemotron sem rede (o _post é trocado por uma resposta falsa)."""

import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jarvis.motores import ErroMotor, MotorNemotron, SINAL_ENCAMINHAR  # noqa: E402


def motor(resposta: str, chave: str = "nvapi-teste") -> MotorNemotron:
    m = MotorNemotron({}, chave, "Jarvis", ["Frases curtas."])
    m.enviados = []

    def falso(corpo):
        m.enviados.append(corpo)
        return {"choices": [{"message": {"content": resposta}}]}

    m._post = falso
    return m


class TestNemotron(unittest.TestCase):
    def test_sem_chave(self):
        m = MotorNemotron({}, "", "Jarvis", [])
        self.assertFalse(m.disponivel)
        with self.assertRaises(ErroMotor):
            m.perguntar("oi", threading.Event())

    def test_desliga_raciocinio_e_guarda_historico(self):
        m = motor("Paris.")
        self.assertEqual(m.perguntar("Capital da França?", threading.Event()), "Paris.")
        corpo = m.enviados[0]
        self.assertEqual(corpo["chat_template_kwargs"], {"enable_thinking": False})
        self.assertIn("ENCAMINHAR", corpo["messages"][0]["content"])
        self.assertEqual(len(m.historico), 2)
        m.perguntar("E a população?", threading.Event())
        self.assertEqual(len(m.enviados[1]["messages"]), 4)  # sistema + par anterior + pergunta nova

    def test_maiusculas_viram_normais(self):
        self.assertEqual(motor("TÓQUIO, CAPITAL DO JAPÃO.").perguntar("x", threading.Event()), "Tóquio, capital do japão.")

    def test_encaminhar_nao_entra_no_historico(self):
        m = motor(SINAL_ENCAMINHAR)
        self.assertEqual(m.perguntar("abre o navegador", threading.Event()), SINAL_ENCAMINHAR)
        self.assertEqual(m.historico, [])

    def test_tira_pensamento(self):
        self.assertEqual(motor("<think>hmm</think>Oi.").perguntar("x", threading.Event()), "Oi.")

    def test_sem_encaminhar_quando_desligado(self):
        m = motor("Ok.")
        m.encaminhar = False
        m.perguntar("x", threading.Event())
        self.assertNotIn("ENCAMINHAR", m.enviados[0]["messages"][0]["content"])


if __name__ == "__main__":
    unittest.main()
