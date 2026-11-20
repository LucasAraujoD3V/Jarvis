"""Escolha automática de microfone (sem hardware)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jarvis.voz import escolher_microfone, mesmo_microfone  # noqa: E402

# medições reais de 20/11/2026 (tudo mudo) + um fone Bluetooth conectado (simulado)
SONDAS = [
    {"nome": "Microfone (High Definition Audi", "api": "MME", "rms": 0.5},
    {"nome": "Microphone (Camo)", "api": "MME", "rms": 0.5},
    {"nome": "Headset (AL-1115 Hands-Free)", "api": "MME", "rms": 48.0},
]


class TestMicrofone(unittest.TestCase):
    def test_nome_cortado_do_mme(self):
        self.assertTrue(mesmo_microfone("Microfone (High Definition Audi", "Microfone (High Definition Audio Device)"))
        self.assertFalse(mesmo_microfone("Microphone (Camo)", "Microfone (High Definition Audio Device)"))
        self.assertFalse(mesmo_microfone("", "x"))

    def test_escolhe_o_que_tem_som(self):
        self.assertEqual(escolher_microfone(SONDAS, "Microfone (High Definition Audi")["nome"], "Headset (AL-1115 Hands-Free)")

    def test_nada_com_som(self):
        self.assertIsNone(escolher_microfone(SONDAS[:2], "Microfone (High Definition Audi"))

    def test_nao_troca_para_o_mesmo(self):
        self.assertIsNone(escolher_microfone(SONDAS, "Headset (AL-1115 Hands-Free)"))

    def test_ignora_erro_de_medida(self):
        self.assertIsNone(escolher_microfone([{"nome": "X", "api": "MME", "rms": -1.0}], "Y"))


if __name__ == "__main__":
    unittest.main()
