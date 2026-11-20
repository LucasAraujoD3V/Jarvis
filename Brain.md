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
  "usa o Claude" / "usa o Gemini" / "usa o Codex" / "usa o Kimi" / "usa o Nemotron" / "modo automático", "desliga", "reinicia".
- **Ensinar (memória persistente)** — resolvido pelo próprio Jarvis, sem IA:
  - "Jarvis, **lembra que** eu acordo às 6" / "**aprende que** meu carro é um Civic" / "**anota aí:** …" → "Anotado."
  - "Jarvis, **esquece que** eu gosto de café" / "esquece o que eu falei sobre o carro" / "apaga da memória o café"
    → apaga o fato que mais combina. "esquece tudo que você sabe de mim" / "apaga a memória" → apaga tudo (cópia em `memoria.bak.json`).
  - "Jarvis, **o que você sabe de mim?**" → uma IA rápida resume a memória em 2ª pessoa (sem IA: lê a lista).
  - "esquece isso" = cancelar; "esquece tudo" = conversa nova (não apaga a memória).
- **Escolher a IA na frase:** "pergunta pro Claude …", "pergunta pro Gemini …", "pergunta pro Kimi …", "Codex, …".
- **Comentários** ("agora sim hein", "boa", "show") recebem resposta curta e natural. Frase captada **sem o nome** na
  janela de 8 s que claramente era para outra pessoa ("Ô Maria, pega o carregador") → a IA responde `IGNORAR` e ele fica quieto.
- **Bandeja:** clique no ícone = painel. Menu: Ligar, Desligar, Reiniciar, Calar, Conversa nova, Motor de IA,
  Mostrar popup, Iniciar com o Windows, Sair.
- **Popup:** clique = cala/cancela; arrastar = muda de lugar (fica salvo); botão direito = esconde.
- **Painel:** estado, microfone, IAs prontas, histórico da conversa, caixa para digitar pedidos, campo para colar a chave.
- **CMD / outros programas** (com o Jarvis rodando):
  - `python jarvis.pyw --pedir "que horas são"` — faz um pedido como se fosse falado.
  - `python jarvis.pyw --falar "Backup concluído"` — só fala o texto (bom para avisos de scripts, VPS, Mesa-Traders).
  - `python jarvis.pyw --ouvir "Jarvis, que horas são"` — **teste**: entrega o texto como se o microfone tivesse ouvido
    (passa pela palavra de ativação e pela janela de seguimento). Ex.: teste de comentário/IGNORAR sem microfone.
  - `python jarvis.pyw` de novo → só abre o painel da instância que já está rodando.
- **Com console (debug):** `iniciar.bat` (= `python jarvis.pyw --console`). **Demo do popup:** `python jarvis.pyw --demo`.

## Arquitetura

```
jarvis.pyw            entrada (pythonw sem console; redireciona stdout/stderr nulos)
jarvis/app.py         liga tudo: Qt, instância única (QLocalServer "JarvisAssistente-<usuario>"), mensagens --pedir/--falar,
                      reinício (sobe processo novo com --reiniciado e encerra), autostart na 1ª execução, --demo
jarvis/assistente.py  núcleo: estados, palavra de ativação, janela de seguimento, comandos locais, fila do "cérebro",
                      escolha do motor, fallback entre motores, prévia falada quando a IA é lenta, sinais Qt para a interface
jarvis/motores.py     MotorNemotron (HTTP NVIDIA: lightning e "pro"), MotorClaude (`claude -p`), MotorCLI (Gemini/Codex CLI)
jarvis/memoria.py     Memoria (fatos ensinados → memoria.json) e Conversa (últimas trocas de qualquer IA → conversa.json)
ferramentas/comparar_modelos.py   compara modelos da API grátis da NVIDIA (acertos × latência, ≤ 60 s por modelo)
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
4. Memória ("lembra que…", "esquece…", "o que você sabe de mim") é resolvida antes de tudo (`texto.comando_memoria`).
5. Roteamento (`texto.rotear`): "pergunta pro X ..." manda direto; modo fixo; **automático** = 4 tipos de pedido, cada um
   com uma **fila** de IAs (config.json → `roteamento`; a 1ª disponível responde, se falhar tenta a próxima):

   | Tipo | Exemplos | Fila | Por quê |
   |---|---|---|---|
   | `pc` | abre, cria, arquivo, pasta, git, projeto, Kairos, Mesa | Claude → Codex → Gemini | só o Claude Code mexe no PC de verdade (permissão `auto`); Codex roda em sandbox só-leitura |
   | `internet` | pesquisa, notícia, clima, cotação, dólar, "quem ganhou" | Gemini → Claude | Gemini CLI tem a busca do Google embutida e poupa o limite do Claude |
   | `inteligente` | compara, analisa, calcula, planeja, "pensa bem", pedido > 30 palavras | Kimi (nvidia_pro) → Gemini → Claude → Nemotron | o modelo grátis que mais acertou o teste de raciocínio |
   | `rapido` | papo, comentário, curiosidade, conta simples, "que horas são" | Nemotron lightning → Gemini → Claude | ~0,5 s; voz precisa de resposta rápida |

   Verbo de ação ("abre", "pesquisa") ou assunto do dia ("clima") manda; substantivo solto ("servidor", "site", "código")
   só decide se não for pergunta difícil ("compara Python e Rust para um servidor web" → inteligente).
   Se um modelo da NVIDIA (sem internet) perceber que precisa de internet/PC, responde `ENCAMINHAR` e o Jarvis repassa à
   próxima IA com ferramentas (Claude/Gemini/Codex). CLI sem login sai da fila por 10 min (não atrasa todo pedido).
6. IA lenta em pedido que não é papo: o Jarvis fala uma prévia ("Pesquisando.", "Abrindo.") enquanto espera.
   Papo rápido não ganha prévia (ficava "Um momento… Pois é.").
7. Resposta → tira markdown/emojis/links → corta em ~600 caracteres no fim de frase ("O resto está no painel.") → fala em trechos
   (sintetiza o próximo enquanto toca o atual). A troca vai para `conversa.json`.

### Memória e continuidade (20/11/2026)
- **Fatos ensinados** (`memoria.json`, gitignorado — é pessoal): vão no prompt de **todas** as IAs (Nemotron/Kimi no
  system, Claude no `--append-system-prompt`, Gemini/Codex junto do pedido pelo stdin). Máx. 300 fatos, ~5000 caracteres no prompt.
- **Conversa compartilhada** (`conversa.json`, gitignorado): últimas 10 trocas, de qualquer IA. Os modelos da NVIDIA
  recebem como histórico do chat; Gemini/Codex recebem as 6 últimas em texto; o Claude recebe só as que **outras** IAs
  responderam depois da última dele (o resto já está na sessão dele).
- **Sessão do Claude** em `preferencias.json → claude_sessao`, retomada com `--resume` mesmo depois de reiniciar.
- Conversa (e sessão do Claude) só zera após **12 h sem uso** (`conversa.nova_conversa_apos_min = 720`; 0 = nunca) ou
  com "nova conversa". Antes zerava após 15 min e em todo reinício. A memória de fatos nunca zera sozinha.
- Teste real (20/11): "lembra que meu café favorito é expresso sem açúcar" → "Anotado." → **reiniciou** →
  "o que você sabe de mim?" → Claude: "Você gosta de café expresso sem açúcar…" (4,3 s).

### Motores
| Motor | Como | Latência medida (18/11/2026) | Observações |
|---|---|---|---|
| Nemotron | `POST https://integrate.api.nvidia.com/v1/chat/completions`, modelo `nvidia/nemotron-3.5-lightning-30b-a3b`, `chat_template_kwargs.enable_thinking=false` | ~0,5 s | Sem internet/PC. Histórico de 10 turnos. Normaliza respostas EM MAIÚSCULAS. |
| Claude | `claude -p <texto> --output-format json --permission-mode auto --model sonnet --strict-mcp-config --append-system-prompt <regras de voz>` na pasta `Data` | 5–6 s simples; 15–25 s com ferramenta/web | Conta logada. `--resume <session_id>` mantém a conversa. Cancelar mata a árvore de processos (`taskkill /T`). `--strict-mcp-config` corta o contexto de ~52k para ~14k tokens. |
| Kimi (`nvidia_pro`) | mesma API da NVIDIA, modelo do config `nvidia_pro.modelo` (escolhido no teste de raciocínio) | — (medir com a chave) | Perguntas difíceis. Mesma chave do Nemotron. Sem internet/PC (ENCAMINHAR). |
| Gemini | `gemini -o json -p " "` com o pedido no **stdin** (Gemini CLI 0.70, conta Google logada) | — (medir após login) | Busca do Google embutida. Sem aprovação no modo headless: só lê/pesquisa, não altera arquivos. |
| Codex | `codex exec --skip-git-repo-check -c model_reasoning_effort=low -` com o pedido no stdin (Codex CLI 0.171, conta ChatGPT já logada em `~/.codex`) | 4,8–6,2 s ("responda só: ok") | Sandbox só-leitura, aprovação "never": lê arquivos, não altera. Reserva para pedidos de PC. |

**Instalação das CLIs (atenção):** o app do Claude no Windows é um pacote MSIX; um `npm install -g` rodado de dentro
dele cai numa pasta `AppData` **virtual** que o resto do Windows não vê (o Jarvis e o terminal não achavam o `gemini`).
Por isso Gemini e Codex ficam em `%USERPROFILE%\.local\npm` (`npm install -g --prefix "%USERPROFILE%\.local\npm" @google/gemini-cli @openai/codex`)
e o Jarvis procura lá (`motores.achar_cli`). Essa pasta **não está no PATH**: no terminal, rode `& "$HOME\.local\npm\gemini.cmd"`.
O mesmo vale para `pip install` feito de dentro do app: rode no terminal normal.

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
- `preferencias.json` (gitignorado): motor escolhido, popup ligado, posição do popup, se o autostart já foi definido,
  `claude_sessao` (sessão do Claude retomada com `--resume`), `microfone` escolhido sozinho.
- `memoria.json` / `memoria.bak.json` / `conversa.json` (gitignorados, pessoais): memória ensinada e conversa recente.
- Logins das CLIs ficam nas pastas delas, fora do projeto: Gemini em `~/.gemini` (conta Google), Codex em `~/.codex`
  (conta ChatGPT), Claude em `~/.claude`. O Jarvis não guarda nenhuma dessas credenciais.
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
- **Microfone deste PC (20/11/2026):** o "Microfone (High Definition Audio)" FUNCIONA, mas em silêncio dá rms ~0,5 —
  igual a uma entrada vazia — e a fala chega baixa (sonda mediu ~10). Ele ouviu "Agora sim hein" às 00:27. Entradas
  conhecidas: HD Audio (padrão), Camo (celular, só com o app aberto), fone Bluetooth "AL-1115 Hands-Free" (desconectado).
- **Vigia do microfone** (`voz.py`): percentil 95 do volume em janelas de 10 s; 6 janelas quietas seguidas (1 min) →
  aviso "Nenhum som há 1 min" e começa a **procurar outro microfone com som**: a cada ~20 s mede todas as entradas num
  processo separado (enxerga fone Bluetooth conectado depois) e, se outra tiver som (rms ≥ 5), salva em
  `preferencias.json → microfone`, reinicia sozinho e fala "Microfone conectado". `microfone.dispositivo` no config.json
  (parte do nome, ex.: `"Camo"`) tem prioridade sobre essa escolha automática.
- Cada frase captada vai para o log com o volume de pico ("Frase captada: … volume pico …") — base para calibrar
  `microfone.limiar_minimo` (180 hoje; se ele não reagir à sua voz, baixar).
- Encerramento usa `os._exit` depois de gravar tudo: em 20/11 um Jarvis ficou vivo após "reiniciar" e dois ouviam/falavam juntos.
- No Windows 11 o ícone novo da bandeja nasce escondido em "^": arraste para a barra uma vez.

## Testes
- `python -m unittest discover -s tests -v` — 55 testes (ativação, comandos locais, roteamento por tipo, memória e
  conversa em disco, memória chegando no prompt de cada IA, CLI pelo stdin, limpeza de fala, motor Nemotron sem rede).
- Teste de janela de seguimento (20/11/2026, `--ouvir`): "Jarvis, quanto é 7 vezes 8?" → "Cinquenta e seis." → em 1 s,
  sem o nome, "Ô Maria, pega o carregador pra mim lá na sala" → **ignorado** (IGNORAR); "Jarvis, qual a capital da
  Austrália?" → "Canberra." → "agora sim hein" → "Pois é." (antes respondia "Não entendi o pedido…").
- Ouvido sem hardware (18/11/2026): voz sintetizada + ruído injetados na fila do microfone → detector de fala →
  Google → ativação: "Jarvis, quanto é dois mais dois?" virou comando "Quanto é 2 + 2"; frase sem o nome foi ignorada;
  "que horas são, Jarvis?" virou "Que horas são"; 22 s de silêncio digital dispararam o aviso de microfone mudo.
- Teste real feito em 18/11/2026: Nemotron 0,6 s ("capital do Japão"); Claude com ferramenta 15,7 s (contou arquivos da pasta);
  encaminhamento Nemotron → Claude com busca web 22 s (jogo do Flamengo); reinício via `--pedir "reinicia"`; popup em todos os estados.
