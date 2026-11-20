"""Testes das regras de texto (palavra de ativação, comandos locais, roteamento de IA, limpeza para fala).
Rodar: python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jarvis.texto import (  # noqa: E402
    categoria, comando_local, dividir_frases, escolher_motor, extrair_comando, limpar_para_fala, parece_acao,
    pedido_de_motor, rotear,
)

CHAVES = ["jarvis", "jarbas", "javis"]
TODOS = {"claude", "nemotron"}


class TestAtivacao(unittest.TestCase):
    def test_nome_no_inicio(self):
        self.assertEqual(extrair_comando("Jarvis, que horas são?", CHAVES), (True, "que horas são"))

    def test_nome_com_vocativo(self):
        self.assertEqual(extrair_comando("ok Jarvis abre o navegador", CHAVES), (True, "abre o navegador"))

    def test_nome_no_fim(self):
        self.assertEqual(extrair_comando("que horas são, Jarvis?", CHAVES), (True, "que horas são"))
        self.assertEqual(extrair_comando("Que horas são o Jarvis", CHAVES), (True, "Que horas são"))  # transcrição real

    def test_so_o_nome(self):
        self.assertEqual(extrair_comando("Jarvis", CHAVES), (True, ""))
        self.assertEqual(extrair_comando("ei Jarvis!", CHAVES), (True, ""))

    def test_variacoes_da_transcricao(self):
        for falado in ("Járvis me ajuda", "jarvi me ajuda", "Jarbas me ajuda", "Javis me ajuda"):
            self.assertEqual(extrair_comando(falado, CHAVES), (True, "me ajuda"), falado)

    def test_sem_nome(self):
        self.assertEqual(extrair_comando("vamos almoçar agora", CHAVES), (False, ""))
        self.assertFalse(extrair_comando("o jardim está bonito", CHAVES)[0])


class TestComandosLocais(unittest.TestCase):
    def test_basicos(self):
        self.assertEqual(comando_local("cancela"), "cancelar")
        self.assertEqual(comando_local("Para de falar!"), "cancelar")
        self.assertEqual(comando_local("usa o Claude"), "motor:claude")
        self.assertEqual(comando_local("modo automático"), "motor:auto")
        self.assertEqual(comando_local("nova conversa, por favor"), "nova_conversa")
        self.assertEqual(comando_local("desliga"), "desligar")
        self.assertEqual(comando_local("obrigado"), "encerrar")

    def test_pedido_de_verdade_nao_e_local(self):
        self.assertIsNone(comando_local("desliga o computador"))
        self.assertIsNone(comando_local("para onde vai o voo"))
        self.assertIsNone(comando_local("usa o Claude para criar um site"))


class TestRoteamento(unittest.TestCase):
    def test_prefixo_explicito(self):
        self.assertEqual(escolher_motor("pergunta pro Claude qual o status da VPS", "auto", TODOS),
                         ("claude", "qual o status da VPS"))
        self.assertEqual(escolher_motor("Nemotron, conta uma piada", "auto", TODOS), ("nemotron", "conta uma piada"))

    def test_auto_conversa_vai_pro_nemotron(self):
        self.assertEqual(escolher_motor("quanto é 12 vezes 8", "auto", TODOS)[0], "nemotron")
        self.assertEqual(escolher_motor("me explica o que é inflação", "auto", TODOS)[0], "nemotron")

    def test_auto_acao_vai_pro_claude(self):
        for t in ("abre o projeto Mesa Traders", "pesquisa o preço do bitcoin", "cria um arquivo de notas",
                  "como está o tempo hoje", "verifica o git do Kairos"):
            self.assertEqual(escolher_motor(t, "auto", TODOS)[0], "claude", t)

    def test_modo_fixo(self):
        self.assertEqual(escolher_motor("quanto é 2 mais 2", "claude", TODOS)[0], "claude")

    def test_sem_nemotron_cai_no_claude(self):
        self.assertEqual(escolher_motor("conta uma piada", "auto", {"claude"})[0], "claude")

    def test_nenhum(self):
        self.assertEqual(escolher_motor("oi", "auto", set())[0], None)

    def test_extras(self):
        self.assertEqual(escolher_motor("pergunta pro codex o que faz esse script", "auto", TODOS | {"codex"}, ["codex"]),
                         ("codex", "o que faz esse script"))

    def test_parece_acao(self):
        self.assertFalse(parece_acao("qual a capital da Austrália"))
        self.assertTrue(parece_acao("roda os testes no PC"))


TUDO = {"claude", "nemotron", "nvidia_pro", "gemini", "codex"}
EXTRAS = ["nvidia_pro", "gemini", "codex"]


class TestRoteamentoNovo(unittest.TestCase):
    """Papo → rápido; difícil → o mais inteligente; internet → Gemini; PC → Claude."""

    def fila(self, texto, disp=TUDO, **kw):
        return rotear(texto, "auto", disp, EXTRAS, **kw)

    def test_categorias(self):
        self.assertEqual(categoria("agora sim hein"), "rapido")
        self.assertEqual(categoria("qual a capital do Japão"), "rapido")
        self.assertEqual(categoria("compara Python e Rust para um site de vendas"), "inteligente")
        self.assertEqual(categoria("pensa bem: qual a melhor estratégia pra quitar duas dívidas"), "inteligente")
        self.assertEqual(categoria("como está o tempo hoje"), "internet")
        self.assertEqual(categoria("pesquisa o preço do bitcoin"), "internet")
        self.assertEqual(categoria("abre o projeto Mesa Traders"), "pc")
        self.assertEqual(categoria("cria um arquivo de notas"), "pc")

    def test_filas(self):
        self.assertEqual(self.fila("agora sim hein")[0][0], "nemotron")
        self.assertEqual(self.fila("compara Python e Rust")[0][0], "nvidia_pro")
        self.assertEqual(self.fila("como está o tempo hoje")[0][0], "gemini")
        self.assertEqual(self.fila("abre o projeto Kairos")[0][0], "claude")

    def test_fila_tem_reserva_sem_repetir(self):
        fila = self.fila("agora sim hein")[0]
        self.assertEqual(len(fila), len(set(fila)))
        self.assertEqual(set(fila), TUDO)

    def test_sem_chave_nvidia_papo_cai_no_gemini(self):
        self.assertEqual(self.fila("agora sim hein", {"claude", "gemini", "codex"})[0][0], "gemini")

    def test_apelidos(self):
        fila, texto, _ = rotear("pergunta pro Kimi quanto é 17 vezes 23", "auto", TUDO, EXTRAS, apelidos={"kimi": "nvidia_pro"})
        self.assertEqual((fila[0], texto), ("nvidia_pro", "quanto é 17 vezes 23"))
        fila, texto, _ = rotear("Gemini, qual a previsão pra amanhã", "auto", TUDO, EXTRAS)
        self.assertEqual((fila[0], texto), ("gemini", "qual a previsão pra amanhã"))

    def test_rotas_do_config(self):
        self.assertEqual(self.fila("agora sim hein", rotas={"rapido": ["gemini"]})[0][0], "gemini")

    def test_troca_de_motor_falada(self):
        self.assertEqual(pedido_de_motor("usa o Gemini"), "gemini")
        self.assertEqual(pedido_de_motor("troca pro Kimi, por favor"), "kimi")
        self.assertIsNone(pedido_de_motor("usa o Claude para criar um site"))


class TestFala(unittest.TestCase):
    def test_markdown_some(self):
        md = "## Resultado\n\n- **Item um** feito\n- `config.json` ajustado\n\n```python\nprint(1)\n```\nVeja [aqui](http://x.com) 🚀"
        fala, cortado = limpar_para_fala(md, 600)
        self.assertNotIn("#", fala)
        self.assertNotIn("*", fala)
        self.assertNotIn("print", fala)
        self.assertNotIn("http", fala)
        self.assertNotIn("🚀", fala)
        self.assertIn("Item um feito", fala)
        self.assertFalse(cortado)

    def test_corta_no_fim_da_frase(self):
        texto = "Primeira frase curta. " * 50
        fala, cortado = limpar_para_fala(texto, 100)
        self.assertTrue(cortado)
        self.assertLessEqual(len(fala), 100)
        self.assertTrue(fala.endswith("."))

    def test_dividir(self):
        partes = dividir_frases("Um. Dois. Três. " + "palavra " * 60, 120)
        self.assertTrue(all(len(p) <= 120 for p in partes))
        self.assertEqual(partes[0], "Um. Dois. Três.")


if __name__ == "__main__":
    unittest.main()
