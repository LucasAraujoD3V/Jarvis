"""Testes da transcrição (sem microfone, sem rede, sem baixar modelo): normalização, filtro de alucinação e reserva."""

import os
import queue
import sys
import threading
import unittest
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jarvis.stt import Transcritor, e_alucinacao, juntar_segmentos, normalizar  # noqa: E402


class TestNormalizacao(unittest.TestCase):
    def test_voz_fraca_sobe_para_o_pico_alvo(self):
        # microfone do PC fixo: fala chegando com pico ~34–97 de 32768 (log de 20/11)
        fraco = (np.sin(np.linspace(0, 200, 16000)) * 97).astype(np.int16)
        n = normalizar(fraco, pico_alvo=0.6)
        self.assertEqual(n.dtype, np.float32)
        self.assertAlmostEqual(float(np.max(np.abs(n))), 0.6, places=2)

    def test_silencio_digital_nao_explode(self):
        self.assertEqual(float(np.max(np.abs(normalizar(np.zeros(1600, dtype=np.int16))))), 0.0)

    def test_ganho_tem_limite(self):
        quase_nada = np.ones(1600, dtype=np.int16)  # pico 1/32768
        self.assertLess(float(np.max(normalizar(quase_nada, ganho_max=400))), 0.02)


class TestAlucinacao(unittest.TestCase):
    def test_frases_inventadas(self):
        for t in ("Legendas pela comunidade Amara.org", "Obrigado por assistir!", "...", " "):
            self.assertTrue(e_alucinacao(t), t)
        self.assertFalse(e_alucinacao("Jarvis, que horas são?"))

    def test_segmentos(self):
        segs = [SimpleNamespace(text=" Jarvis, abre o Chrome.", no_speech_prob=0.1, avg_logprob=-0.3),
                SimpleNamespace(text=" Obrigado por assistir.", no_speech_prob=0.2, avg_logprob=-0.4),
                SimpleNamespace(text=" hmm", no_speech_prob=0.9, avg_logprob=-1.5)]
        self.assertEqual(juntar_segmentos(segs), "Jarvis, abre o Chrome.")


class WhisperFalso:
    def __init__(self, resposta):
        self.resposta = resposta
        self.chamadas = 0

    def transcrever(self, audio):
        self.chamadas += 1
        return self.resposta


class TestReserva(unittest.TestCase):
    def transcritor(self, principal, google, whisper):
        avisos = []
        t = Transcritor({"reserva": True}, "pt-BR", "Jarvis", avisos.append)
        t.principal = principal
        t.whisper = WhisperFalso(whisper)
        t._por_google = lambda audio: google() if callable(google) else google
        return t, avisos

    def test_google_nao_entendeu_whisper_salva(self):
        t, _ = self.transcritor("google", "", "Jarvis, que horas são?")
        self.assertEqual(t.transcrever(np.zeros(1600, dtype=np.int16)), "Jarvis, que horas são?")

    def test_google_entendeu_whisper_nem_roda(self):
        t, _ = self.transcritor("google", "Jarvis, oi", "outra coisa")
        self.assertEqual(t.transcrever(np.zeros(1600, dtype=np.int16)), "Jarvis, oi")
        self.assertEqual(t.whisper.chamadas, 0)

    def test_sem_internet_whisper_responde_sem_aviso(self):
        def sem_rede():
            raise ConnectionError("sem internet")

        t, avisos = self.transcritor("google", sem_rede, "Jarvis, liga a luz")
        self.assertEqual(t.transcrever(np.zeros(1600, dtype=np.int16)), "Jarvis, liga a luz")
        self.assertEqual(avisos, [])

    def test_whisper_principal_na_gpu(self):
        t, _ = self.transcritor("whisper", "google", "Jarvis, abre o Kairos")
        self.assertEqual(t.transcrever(np.zeros(1600, dtype=np.int16)), "Jarvis, abre o Kairos")

    def test_ninguem_entendeu(self):
        t, _ = self.transcritor("google", "", "")
        self.assertEqual(t.transcrever(np.zeros(1600, dtype=np.int16)), "")


class Prefs(dict):
    def set(self, k, v):
        self[k] = v


class TestGpuPorComputador(unittest.TestCase):
    """O Jarvis roda em mais de um PC: o teste da NVIDIA de um não pode valer para o outro."""

    def setUp(self):
        import jarvis.stt as stt

        self.stt = stt
        self.sondas = []
        self._original = stt.sondar_cuda
        stt.sondar_cuda = lambda modelo, raiz, timeout_s=600: (self.sondas.append(modelo) or (False, "sem placa NVIDIA"))

    def tearDown(self):
        self.stt.sondar_cuda = self._original

    def test_resultado_de_outro_pc_nao_vale_aqui(self):
        prefs = Prefs(whisper_cuda={"PC-FIXO": {"ok": True, "ct2": self.stt.versao_ct2(), "modelo": "small"}})
        t = Transcritor({}, "pt-BR", "Jarvis", lambda m: None, prefs=prefs)
        self.assertFalse(t._gpu_ok())  # este PC é testado de novo, não herda o "tem NVIDIA" do outro
        self.assertEqual(len(self.sondas), 1)
        self.assertIn("PC-FIXO", prefs["whisper_cuda"])
        self.assertIn(self.stt.maquina(), prefs["whisper_cuda"])
        self.assertFalse(t._gpu_ok())  # segunda vez usa o salvo, sem testar de novo
        self.assertEqual(len(self.sondas), 1)

    def test_formato_antigo_e_ignorado(self):
        prefs = Prefs(whisper_cuda={"ok": True, "ct2": "x", "modelo": "small"})
        t = Transcritor({}, "pt-BR", "Jarvis", lambda m: None, prefs=prefs)
        t._gpu_ok()
        self.assertEqual(len(self.sondas), 1)
        self.assertNotIn("ok", prefs["whisper_cuda"])


class TestFalaEmFila(unittest.TestCase):
    """Falante.falar_fila com síntese e reprodução trocadas por falsas (sem som, sem rede)."""

    def falante(self, ditos):
        try:
            from jarvis.voz import Falante
        except ImportError as e:  # sounddevice ausente
            self.skipTest(str(e))
        f = Falante({}, lambda v: None, ditos.append)
        f.sintetizar = lambda texto: (np.zeros(10, dtype=np.int16), 24000)
        f._tocar = lambda audio, taxa, cancelar: None
        return f

    def test_fala_na_ordem_conforme_chega(self):
        ditos = []
        f = self.falante(ditos)
        q = queue.Queue()

        def produtor():
            for t in ("Abrindo o Chrome.", "Pronto.", "Mais alguma coisa?"):
                q.put(t)
                threading.Event().wait(0.05)
            q.put(None)

        threading.Thread(target=produtor).start()
        self.assertTrue(f.falar_fila(q, threading.Event()))
        self.assertEqual(ditos, ["Abrindo o Chrome.", "Pronto.", "Mais alguma coisa?"])

    def test_cancelar_para(self):
        ditos = []
        f = self.falante(ditos)
        q = queue.Queue()
        cancelar = threading.Event()
        cancelar.set()
        q.put("Não deveria falar.")
        self.assertFalse(f.falar_fila(q, cancelar))
        self.assertEqual(ditos, [])


class TestFalaEmFluxo(unittest.TestCase):
    """A fala que acompanha o Claude em streaming (precisa do PySide6 para importar o assistente)."""

    def fluxo(self, previa=""):
        try:
            from jarvis.assistente import FalaEmFluxo
        except ImportError as e:
            self.skipTest(str(e))
        ditos, mudo = [], []
        falante = SimpleNamespace(falar_fila=lambda fila, cancelar: [ditos.append(t) for t in iter(fila.get, None)])
        ouvinte = SimpleNamespace(definir_mudo=mudo.append)
        a = SimpleNamespace(max_fala=600, falante=falante, ouvinte=ouvinte, _cancelar=threading.Event())
        return FalaEmFluxo(a, previa), ditos, mudo

    def test_fala_enquanto_chega_e_desmuta_no_fim(self):
        f, ditos, mudo = self.fluxo()
        for p in ("Oi, Lu", "cas. Tudo ", "certo."):
            f.parcial(p)
        self.assertTrue(f.terminar())
        self.assertEqual(ditos, ["Oi, Lucas.", "Tudo certo."])
        self.assertEqual(mudo, [True, False])

    def test_ignorar_nao_fala_nada(self):
        f, ditos, mudo = self.fluxo()
        f.parcial("IGNORAR")
        self.assertFalse(f.terminar())
        self.assertEqual(ditos, [])
        self.assertTrue(f.ignorar)
        self.assertEqual(mudo, [])

    def test_frase_antes_da_ferramenta_sai_na_hora(self):
        # bug real (22/11): "Vou procurar a pasta." só saía quando chegava texto novo, depois da ferramenta
        f, ditos, _ = self.fluxo(previa="Abrindo.")
        f.parcial("Vou procurar a pasta.")  # sem espaço depois: a frase ainda não "fechou"
        f.ferramenta("Bash")
        self.assertEqual(list(f.fila.queue), ["Vou procurar a pasta."])  # já foi para a voz, sem prévia
        f.parcial("\nSão 15 arquivos.")
        f.terminar()
        self.assertEqual(ditos, ["Vou procurar a pasta.", "São 15 arquivos."])

    def test_narracao_entre_ferramentas_nao_gasta_a_fala_final(self):
        f, ditos, _ = self.fluxo()
        f.parcial("Vou procurar.")
        f.ferramenta("Bash")
        f.parcial("\nA busca ficou lenta, foi pra background. " * 20)  # narração longa no meio
        f.ferramenta("Bash")  # menos de 10 s desde a última fala: narração não é falada
        f.parcial("\nSão 15 arquivos.")
        f.terminar()
        self.assertEqual(ditos, ["Vou procurar.", "São 15 arquivos."])

    def test_narracao_vira_aviso_depois_de_muito_silencio(self):
        f, ditos, _ = self.fluxo()
        f.parcial("Vou procurar.")
        f.ferramenta("Bash")
        f._ultima_fala -= 30  # 30 s mudo
        f.parcial("\nAinda procurando, a busca está lenta. Vou tentar outro caminho.")
        f.ferramenta("Bash")
        f.parcial("\nAchei.")
        f.terminar()
        self.assertEqual(ditos, ["Vou procurar.", "Ainda procurando, a busca está lenta.", "Achei."])

    def test_ferramenta_sem_texto_fala_previa_uma_vez(self):
        f, ditos, _ = self.fluxo(previa="Abrindo.")
        f.ferramenta("Bash")
        f.ferramenta("Bash")
        f.parcial("Pronto, abri o Chrome.")
        f.terminar()
        self.assertEqual(ditos, ["Abrindo.", "Pronto, abri o Chrome."])


if __name__ == "__main__":
    unittest.main()
