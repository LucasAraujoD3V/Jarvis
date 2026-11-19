# Jarvis — Brain

Assistente de voz pessoal do Windows, estilo "Jarvis". Liga sozinho com o computador, fica na bandeja (perto do relógio),
escuta o microfone, responde com voz e mostra um popup animado (anéis girando, núcleo que pulsa no ritmo da fala).

Junta três peças que já existiam na pasta `Data`:
- **Leitor de voz** (`Data\Leitor de voz`): a ideia de escutar, transcrever e falar com Edge TTS. Lá o texto era colado na
  janela do Claude com `pyautogui`; aqui virou **serviço**: o Jarvis chama a IA direto, sem depender de janela aberta.
- **Nemotron da Mesa-Traders** (`Mesa-Traders\src\brain.js`): mesma API da NVIDIA (formato OpenAI), agora com um modelo
  rápido e uma **conta própria** (pendente — ver Configuração).
- **Claude Code logado no PC** (`claude -p`): usa a conta já logada, sem chave de API.

## Como usar

- **Falar:** "Jarvis, <pedido>". Ex.: "Jarvis, que horas são?", "Jarvis, pergunta pro Claude o status da Mesa Traders".
  - Só "Jarvis" → ele responde "Sim?" e escuta o próximo pedido sem precisar do nome.
  - Depois de cada resposta há uma **janela de 8 s** para continuar a conversa sem repetir o nome.
  - O nome pode vir no fim: "que horas são, Jarvis?".
- **Comandos de voz locais** (frase exata, sem IA): "cancela"/"para de falar", "obrigado" (encerra), "nova conversa",
  "usa o Claude" / "usa o Nemotron" / "modo automático", "desliga", "reinicia".
- **Bandeja:** clique no ícone = painel. Menu: Ligar, Desligar, Reiniciar, Calar, Conversa nova, Motor de IA,
  Mostrar popup, Iniciar com o Windows, Sair.
- **Popup:** clique = cala/cancela; arrastar = muda de lugar (fica salvo); botão direito = esconde.
- **Painel:** estado, microfone, IAs prontas, histórico da conversa, caixa para digitar pedidos, campo para colar a chave.
- **CMD / outros programas** (com o Jarvis rodando):
  - `python jarvis.pyw --pedir "que horas são"` — faz um pedido como se fosse falado.
  - `python jarvis.pyw --falar "Backup concluído"` — só fala o texto (bom para avisos de scripts, VPS, Mesa-Traders).
  - `python jarvis.pyw` de novo → só abre o painel da instância que já está rodando.
- **Com console (debug):** `iniciar.bat` (= `python jarvis.pyw --console`). **Demo do popup:** `python jarvis.pyw --demo`.

## Arquitetura

```
jarvis.pyw            entrada (pythonw sem console; redireciona stdout/stderr nulos)
jarvis/app.py         liga tudo: Qt, instância única (QLocalServer "JarvisAssistente-<usuario>"), mensagens --pedir/--falar,
                      reinício (sobe processo novo com --reiniciado e encerra), autostart na 1ª execução, --demo
jarvis/assistente.py  núcleo: estados, palavra de ativação, janela de seguimento, comandos locais, fila do "cérebro",
                      escolha do motor, fallback entre motores, prévia falada quando a IA é lenta, sinais Qt para a interface
jarvis/motores.py     MotorNemotron (HTTP NVIDIA), MotorClaude (subprocesso `claude -p`), MotorCLI (codex/gemini/...)
jarvis/voz.py         Ouvinte (microfone 30 ms → detector de fala com limiar adaptativo → Google STT pt-BR)
                      Falante (Edge TTS em memória → miniaudio → sounddevice, mede volume p/ animação; fallback SAPI)
jarvis/texto.py       regras puras e testadas: palavra de ativação, comandos locais, roteamento, limpeza de markdown
jarvis/reator.py      desenho do reator (QPainter): marcações, arcos, anel tracejado, cometa, barras radiais, núcleo
jarvis/hud.py         popup translúcido, sempre por cima, sem roubar foco, fade in/out, legenda
jarvis/painel.py      janela de controle (fechar = esconder)
jarvis/bandeja.py     ícone e menu da bandeja (ícone muda de cor com o estado)
jarvis/icones.py      ícones desenhados em código; gera assets/jarvis.ico para o atalho
jarvis/autostart.py   atalho Jarvis.lnk em %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
jarvis/config.py      config.json, .env, preferencias.json
```

### Estados (cor do popup/ícone)
`desligado` (cinza) · `ouvindo` = aguardando o nome (ciano, popup escondido) · `atento` = ouvindo o pedido (ciano, núcleo
segue o microfone) · `pensando` (âmbar, giro rápido + cometa) · `falando` (ciano claro, barras no ritmo da voz).

### Fluxo de um pedido
1. Microfone contínuo; fala detectada por energia (limiar = 20º percentil do ruído × 2,6, mínimo 180) → frase termina com 0,9 s de silêncio.
2. Google Speech (pt-BR, grátis) transcreve. Sem "Jarvis" e fora da janela de seguimento → ignora.
3. `comando_local` (frase exata) ou IA. Microfone fica **mudo enquanto ele fala** (não ouve a própria voz).
4. Roteamento (`escolher_motor`): "pergunta pro Claude/Nemotron ..." manda direto; modo fixo; **automático**:
   pedido de ação/internet/PC (abre, cria, arquivo, pasta, projeto, pesquisa, clima, cotação, git, Mesa, Kairos...) → Claude;
   o resto → Nemotron. Se o Nemotron perceber que precisa de internet/PC, responde `ENCAMINHAR` e o Jarvis repassa ao Claude.
   Motor falhou → tenta o outro.
5. Claude é lento: o Jarvis fala uma prévia ("Pesquisando.", "Certo. Um momento.") enquanto espera.
6. Resposta → tira markdown/emojis/links → corta em ~600 caracteres no fim de frase ("O resto está no painel.") → fala em trechos
   (sintetiza o próximo enquanto toca o atual).

### Motores
| Motor | Como | Latência medida (18/11/2026) | Observações |
|---|---|---|---|
| Nemotron | `POST https://integrate.api.nvidia.com/v1/chat/completions`, modelo `nvidia/nemotron-3.5-lightning-30b-a3b`, `chat_template_kwargs.enable_thinking=false` | ~0,5 s | Sem internet/PC. Histórico de 10 turnos. Normaliza respostas EM MAIÚSCULAS. |
| Claude | `claude -p <texto> --output-format json --permission-mode auto --model sonnet --strict-mcp-config --append-system-prompt <regras de voz>` na pasta `Data` | 5–6 s simples; 15–25 s com ferramenta/web | Conta logada. `--resume <session_id>` mantém a conversa. Cancelar mata a árvore de processos (`taskkill /T`). `--strict-mcp-config` corta o contexto de ~52k para ~14k tokens. |
| Extras | `motores_extras` no config (codex, gemini) | — | Desativados; ativam se o comando existir no PATH e `ativo: true`. |

- Modo de permissão do Claude = `auto` (classificador bloqueia ação arriscada; o prompt também manda não fazer nada irreversível sem confirmar).
- Conversa nova automática depois de 15 min sem uso.
- Estilo das respostas = `estilo_resposta` do config.json (as 4 regras de economia: frases curtas, sem enrolação, sem narrar, sem artigos).

## Configuração

- **Todas as credenciais estão no `Cofre.md` na raiz da pasta `Data`, fora do versionamento e nunca postado no git.**
  `.env.example` mostra as variáveis que o projeto precisa (só `NVIDIA_API_KEY`).
- `config.json` (versionado): nome, palavras de ativação (variações que o reconhecimento costuma errar: jarbas, javis...),
  voz (`pt-BR-AntonioNeural`, +8%, -4Hz), microfone, janela de seguimento, motores, estilo, popup.
- `.env` (gitignorado): `NVIDIA_API_KEY` — conta **nova** do Nemotron, só do Jarvis (separada da Mesa-Traders). **Pendente.**
  Sem ela o modo automático manda tudo para o Claude (funciona, mas é mais lento e gasta o limite do plano).
- `preferencias.json` (gitignorado): motor escolhido, popup ligado, posição do popup, se o autostart já foi definido.
- `logs/jarvis.log` (gitignorado, 1 MB × 4): tudo que ouviu (com nome), respostas, erros.
- Python 3.13 em `C:\Users\Lucas\AppData\Local\Programs\Python\Python313`. Dependências: `pip install -r requirements.txt`.
- Autostart: `Jarvis.lnk` na pasta Inicializar → `pythonw.exe "...\Jarvis\jarvis.pyw" --boot` (espera 8 s antes de abrir o
  microfone; no boot não abre o painel, só bandeja + saudação). A pasta do projeto está fixada no iCloud (`attrib +P`)
  para os arquivos estarem sempre locais na hora do boot.

## Limitações conhecidas
- Precisa de internet para transcrever (Google) e para a voz neural (Edge). Sem internet a voz cai na do Windows (SAPI),
  mas a transcrição para.
- Não dá para interromper por voz enquanto ele fala (microfone mudo para não ouvir a si mesmo): clique no popup ou "Calar".
- O nome do microfone vem cortado em 31 caracteres (limite do driver MME).
- No Windows 11 o ícone novo da bandeja nasce escondido em "^": arraste para a barra uma vez.

## Testes
- `python -m unittest discover -s tests -v` — 25 testes (ativação, comandos locais, roteamento, limpeza de fala, motor Nemotron sem rede).
- Teste real feito em 18/11/2026: Nemotron 0,6 s ("capital do Japão"); Claude com ferramenta 15,7 s (contou arquivos da pasta);
  encaminhamento Nemotron → Claude com busca web 22 s (jogo do Flamengo); reinício via `--pedir "reinicia"`; popup em todos os estados.
