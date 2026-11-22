"""Testes das regras de texto (palavra de ativação, comandos locais, roteamento de IA, limpeza para fala).
Rodar: python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jarvis.texto import (  # noqa: E402
    DivisorFala, categoria, comando_local, data_falada, dividir_frases, escolher_motor, extrair_comando, hora_falada,
    limpar_para_fala, parece_acao, pedido_de_motor, rotear,
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

    def test_auto_conversa_vai_pro_claude(self):
        # desde 22/11/2026 o Claude responde tudo; o Nemotron é reserva
        self.assertEqual(escolher_motor("quanto é 12 vezes 8", "auto", TODOS)[0], "claude")
        self.assertEqual(escolher_motor("me explica o que é inflação", "auto", TODOS)[0], "claude")

    def test_sem_claude_conversa_cai_no_nemotron(self):
        self.assertEqual(escolher_motor("me explica o que é inflação", "auto", {"nemotron"})[0], "nemotron")

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
    """Claude primeiro em tudo; a reserva depende do tipo: papo → Nemotron; difícil → Kimi; internet → Gemini; PC → Codex."""

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
        self.assertEqual(self.fila("agora sim hein")[0][:2], ["claude", "nemotron"])
        self.assertEqual(self.fila("compara Python e Rust")[0][:2], ["claude", "nvidia_pro"])
        self.assertEqual(self.fila("como está o tempo hoje")[0][:2], ["claude", "gemini"])
        self.assertEqual(self.fila("abre o projeto Kairos")[0][:2], ["claude", "codex"])

    def test_fila_tem_reserva_sem_repetir(self):
        fila = self.fila("agora sim hein")[0]
        self.assertEqual(len(fila), len(set(fila)))
        self.assertEqual(set(fila), TUDO)

    def test_sem_claude_papo_cai_no_nemotron_e_sem_nvidia_no_gemini(self):
        self.assertEqual(self.fila("agora sim hein", TUDO - {"claude"})[0][0], "nemotron")
        self.assertEqual(self.fila("agora sim hein", {"gemini", "codex"})[0][0], "gemini")

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


class TestHoraLocal(unittest.TestCase):
    def test_comandos(self):
        self.assertEqual(comando_local("que horas são?"), "hora")
        self.assertEqual(comando_local("Que horas são agora?"), "hora")
        self.assertEqual(comando_local("que dia é hoje"), "data")
        self.assertIsNone(comando_local("que horas abre o mercado"))  # pergunta de verdade vai para a IA

    def test_falas(self):
        self.assertEqual(hora_falada(14, 5), "São 14:05.")
        self.assertEqual(hora_falada(1, 30), "É 01:30.")
        self.assertEqual(hora_falada(12, 0), "É meio-dia.")
        self.assertEqual(data_falada(5, 22, 11), "Hoje é sábado, 22 de novembro.")


def alimentar_aos_poucos(d: DivisorFala, texto: str, passo: int = 3) -> list[str]:
    saida = []
    for i in range(0, len(texto), passo):
        saida += d.alimentar(texto[i:i + passo])
    return saida + d.terminar()


class TestDivisorFala(unittest.TestCase):
    def test_primeira_frase_sai_antes_do_fim(self):
        d = DivisorFala()
        self.assertEqual(d.alimentar("Abrindo o Chrome. Já"), ["Abrindo o Chrome."])
        self.assertEqual(d.alimentar(" está aberto"), [])
        self.assertEqual(d.terminar(), ["Já está aberto."])

    def test_streaming_reconstroi_tudo_sem_markdown(self):
        texto = "**Pronto**, Lucas. Abri o projeto.\nTem dois testes falhando: o de login e o de cadastro."
        partes = alimentar_aos_poucos(DivisorFala(), texto)
        self.assertEqual(" ".join(partes), "Pronto, Lucas. Abri o projeto. Tem dois testes falhando: o de login e o de cadastro.")

    def test_pula_bloco_de_codigo(self):
        partes = alimentar_aos_poucos(DivisorFala(), "Rodei isto:\n```\ngit status\nnpm test\n```\nTudo passou.")
        self.assertEqual(" ".join(partes), "Rodei isto: Tudo passou.")

    def test_corta_no_limite(self):
        d = DivisorFala(max_chars=40)
        partes = alimentar_aos_poucos(d, "Primeira frase curta. Segunda frase também curta. Terceira que não cabe mais.")
        self.assertTrue(d.cortado)
        self.assertLessEqual(len(" ".join(partes)), 60)
        self.assertEqual(partes[0], "Primeira frase curta.")

    def test_numero_solto_nao_vira_trecho(self):
        self.assertEqual(alimentar_aos_poucos(DivisorFala(), "Duas coisas. 1. Login quebrado."),
                         ["Duas coisas.", "Login quebrado."])


if __name__ == "__main__":
    unittest.main()
