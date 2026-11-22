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


class TestAparaSilencio(unittest.TestCase):
    def test_tira_silencio_das_pontas(self):
        import numpy as np

        from jarvis.voz import aparar_silencio

        voz = (np.sin(np.linspace(0, 300, 12000)) * 8000).astype(np.int16)  # 0,5 s de "voz" a 24 kHz
        audio = np.concatenate([np.zeros(12000, np.int16), voz, np.zeros(12000, np.int16)])
        cortado = aparar_silencio(audio, 24000)
        self.assertLess(len(cortado), len(voz) + 2 * 24000 * 0.1)
        self.assertGreaterEqual(len(cortado), len(voz) - 100)
        self.assertEqual(len(aparar_silencio(np.zeros(100, np.int16), 24000)), 100)  # tudo silêncio: não mexe


if __name__ == "__main__":
    unittest.main()
