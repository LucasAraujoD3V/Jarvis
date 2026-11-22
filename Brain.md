# Jarvis — Brain

Assistente de voz pessoal do Windows, estilo "Jarvis". Liga sozinho com o computador, fica na bandeja (perto do relógio),
escuta o microfone, responde com voz e mostra um popup animado (anéis girando, núcleo que pulsa no ritmo da fala).

Junta três peças que já existiam na pasta `Data`:
- **Leitor de voz** (`Data\Leitor de voz`): a ideia de escutar, transcrever e falar com Edge TTS. Lá o texto era colado na
  janela do Claude com `pyautogui`; aqui virou **serviço**: o Jarvis chama a IA direto, sem depender de janela aberta.
- **Nemotron da Mesa-Traders** (`Mesa-Traders\src\brain.js`): mesma API da NVIDIA (formato OpenAI), agora com um modelo
  rápido e uma **conta própria** (pendente — ver Configuração).
- **Claude Code logado no PC** (`claude -p`): usa a conta já logada, sem chave de API.

Desde 20/11/2026 também usa **Gemini CLI** (conta Google) e **Codex CLI** (conta ChatGPT), tem **memória que você
ensina por voz** e escolhe a IA pelo tipo de pedido. **Regra: nenhuma API paga** — só contas já logadas por linha de
comando ou coisas grátis (API grátis da NVIDIA).

**Reforma de 22/11/2026** (o Lucas achou que ele "não escuta direito, não tem personalidade, é limitado e o Nemotron é
fraco"):
- **Personalidade:** `config.json → persona` (calmo, leal, humor seco, fala natural, chama o Lucas pelo nome, tem
  opinião, não inventa). Saiu o `estilo_resposta` — eram as 4 regras de economia de token do chat do Claude Code
  ("frases de 3 a 6 palavras, sem artigos") que tinham ido parar no prompt de TODAS as IAs e deixavam a fala robótica.
- **Claude primeiro em tudo**, num **processo que fica aberto** (`stream-json`): a resposta chega em pedaços e o Jarvis
  **fala frase a frase enquanto o Claude ainda escreve**. Nemotron/Kimi/Gemini/Codex viraram reserva.
- **Escuta:** Whisper local (faster-whisper). Com placa NVIDIA funcionando é o principal; sem ela o Google segue
  principal e o Whisper transcreve quando o Google não entende (antes a frase se perdia). Ver "Transcrição".
- Hora e data respondidas na hora, sem IA. Voz: cache das frases curtas e corte do silêncio que o Edge põe nas pontas
  ("Sim?" tinha 1,9 s de áudio, agora 0,4 s). `edge-tts` ≥ 7.3.1 (a 7.2.8 leva 403 da Microsoft e cai na voz do Windows).

**Spider ("aranha")** = `Data\Spider`: painel-teia com todas as contas de IA, nuvem e VPS do Lucas (Nemotron no centro,
roda na VPS 1 em `http://100.119.108.26:3060` com o Tailscale). Ainda não está ligado ao Jarvis (ideia: o Jarvis
perguntar ao Spider qual conta usar, e o Spider mostrar as IAs do Jarvis na teia).

## Pendências (22/11/2026)
1. **Atualizar o PC fixo** (onde o Jarvis roda; o código está no GitHub `LucasAraujoD3V/Jarvis`), num terminal normal
   (não de dentro do app do Claude — ver "Instalação das CLIs"):
   `git pull` na pasta do Jarvis → `pip install -U -r requirements.txt` (traz faster-whisper e edge-tts novo) →
   reiniciar o Jarvis pela bandeja. Na 1ª vez ele baixa o Whisper "small" (~480 MB) e testa a placa de vídeo.
2. **GPU para o Whisper (opcional, recomendado):** `pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"` (~1,5 GB) e
   apagar `whisper_cuda` do `preferencias.json` para ele testar de novo. Sem isso o Whisper roda na CPU só como reserva.
3. **Conferir o login do Claude Code no PC fixo** (`claude` no terminal; se pedir, `/login`). Sem login o Jarvis avisa
   "Claude sem login" e responde pelas reservas. No notebook (22/11) o login estava vencido.
4. Testar pela voz de verdade no PC fixo: escuta (Whisper/Google), streaming do Claude, personalidade. Ainda não feito.
5. **Login do Gemini** e **chave NVIDIA nova** viraram opcionais (só reserva). Gemini: `& "$HOME\.local\npm\gemini.cmd"`
   → Login with Google. NVIDIA: build.nvidia.com → colar no painel → `python ferramentas/comparar_modelos.py`.
6. **Ollama: não instalado (decisão).** GPU GTX 1650 de 4 GB só roda modelo de ~3–4B, mais fraco que o Claude.

## Como usar

- **Falar:** "Jarvis, <pedido>". Ex.: "Jarvis, que horas são?", "Jarvis, pergunta pro Claude o status da Mesa Traders".
  - Só "Jarvis" → ele responde "Sim?" e escuta o próximo pedido sem precisar do nome.
  - Depois de cada resposta há uma **janela de 8 s** para continuar a conversa sem repetir o nome.
  - O nome pode vir no fim: "que horas são, Jarvis?".
- **Comandos de voz locais** (frase exata, sem IA): "cancela"/"para de falar", "obrigado" (encerra), "nova conversa",
  "usa o Claude" / "usa o Gemini" / "usa o Codex" / "usa o Kimi" / "usa o Nemotron" / "modo automático", "desliga", "reinicia",
  "que horas são" / "que dia é hoje" (respondidos na hora).
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
                      escolha do motor, fallback entre motores, FalaEmFluxo (fala enquanto o Claude escreve), sinais Qt
jarvis/motores.py     persona/regras_voz, MotorNemotron (HTTP NVIDIA), MotorClaude (processo fixo stream-json),
                      MotorCLI (Gemini/Codex CLI)
jarvis/memoria.py     Memoria (fatos ensinados → memoria.json) e Conversa (últimas trocas de qualquer IA → conversa.json)
ferramentas/comparar_modelos.py   compara modelos da API grátis da NVIDIA (acertos × latência, ≤ 60 s por modelo)
jarvis/voz.py         Ouvinte (microfone 30 ms → detector de fala com limiar adaptativo → stt.Transcritor)
                      Falante (Edge TTS em memória → miniaudio → corta silêncio → sounddevice; cache de frases curtas;
                      falar_fila = fala trechos conforme chegam; fallback SAPI)
jarvis/stt.py         Transcritor: Whisper local (faster-whisper, GPU ou CPU) e Google, um como reserva do outro;
                      normaliza volume, filtra alucinação do Whisper, testa a GPU num processo separado
jarvis/texto.py       regras puras e testadas: palavra de ativação, comandos locais (hora/data), roteamento,
                      limpeza de markdown, DivisorFala (resposta em pedaços → frases para falar)
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
1. Microfone contínuo; fala detectada por energia (limiar = 20º percentil do ruído × 2,6, mínimo 20) → frase termina com 0,9 s de silêncio.
2. `stt.Transcritor` transcreve (ver "Transcrição"). Sem "Jarvis" e fora da janela de seguimento → ignora.
3. `comando_local` (frase exata) ou IA. Microfone fica **mudo enquanto ele fala** (não ouve a própria voz).
4. Memória ("lembra que…", "esquece…", "o que você sabe de mim") é resolvida antes de tudo (`texto.comando_memoria`).
5. Roteamento (`texto.rotear`): "pergunta pro X ..." manda direto; modo fixo; **automático** = 4 tipos de pedido, cada um
   com uma **fila** de IAs (config.json → `roteamento`; a 1ª disponível responde, se falhar tenta a próxima).
   **Desde 22/11/2026 o Claude é o 1º em todos** (personalidade, ferramentas e internet); o tipo só decide a reserva:

   | Tipo | Exemplos | Fila | Reserva serve para |
   |---|---|---|---|
   | `pc` | abre, cria, arquivo, pasta, git, projeto, Kairos, Mesa | Claude → Codex → Gemini | Codex pode escrever na pasta Data (`-s workspace-write`) |
   | `internet` | pesquisa, notícia, clima, cotação, dólar, "quem ganhou" | Claude → Gemini | Gemini CLI tem a busca do Google embutida |
   | `inteligente` | compara, analisa, calcula, planeja, "pensa bem", pedido > 30 palavras | Claude → Kimi → Gemini → Nemotron | Kimi = o grátis que mais acertou raciocínio |
   | `rapido` | papo, comentário, curiosidade, conta simples | Claude → Nemotron → Gemini | Nemotron ~0,5 s (precisa da chave NVIDIA) |

   Verbo de ação ("abre", "pesquisa") ou assunto do dia ("clima") manda; substantivo solto ("servidor", "site", "código")
   só decide se não for pergunta difícil ("compara Python e Rust para um servidor web" → inteligente).
   Se um modelo da NVIDIA (sem internet) perceber que precisa de internet/PC, responde `ENCAMINHAR` e o Jarvis repassa à
   próxima IA com ferramentas (Claude/Gemini/Codex). CLI sem login sai da fila por 10 min (não atrasa todo pedido).
6. **Claude (streaming):** cada pedaço de texto vai para `FalaEmFluxo` → `DivisorFala` solta a 1ª frase assim que ela
   termina → `Falante.falar_fila` sintetiza e fala enquanto o resto chega. A 1ª frase fica segura até se saber que não
   é `IGNORAR`. Se o Claude for usar ferramenta sem ter dito nada, fala a prévia ("Abrindo."). Se cair no meio depois de
   já ter falado, diz "Perdi a conexão no meio da resposta" (outra IA repetiria do zero).
   **Outras IAs (sem streaming):** IA lenta em pedido que não é papo ganha a prévia falada enquanto espera.
7. Resposta → tira markdown/emojis/links → corta em ~600 caracteres no fim de frase ("O resto está no painel.").
   A troca vai para `conversa.json`.

### Transcrição (22/11/2026) — `config.json → transcricao`
- `motor: "auto"`: testa a placa NVIDIA **num processo separado** (falta de DLL do CUDA pode derrubar o processo) e
  guarda o resultado em `preferencias.json → whisper_cuda` (refaz se o ctranslate2 mudar). GPU ok → **Whisper "small"
  na GPU é o principal**. Sem GPU → **Google principal + Whisper na CPU como reserva** (quando o Google não entende ou
  está sem internet). `motor: "whisper"`/`"google"` força um deles; `gpu: "nao"` nunca usa a placa; `reserva: false`
  desliga a reserva; `modelo: "base"` é ~3× mais rápido na CPU e erra bem mais.
- Medido no notebook i7-1360P (sem NVIDIA), int8: "small" 5,4 s por frase (3,9 s com 8 threads — o padrão agora),
  "base" 1,5–2 s. "small" acertou "Jarvis, que horas são?" e "Mesa Traders" até com voz em rms ~25 + chiado (o nível do
  microfone do PC fixo); "base" errou o nome. Na GTX 1650 (CUDA) a expectativa é < 1 s — **não medido ainda**.
- Normaliza o volume antes de transcrever (pico 0,6, ganho até 400×). Com voz sintética limpa o Google já acertava baixo
  mesmo sem isso — o problema do microfone real deve ser qualidade (eco/distância), por isso o Whisper.
- Filtra alucinações típicas do Whisper em silêncio ("Legendas pela comunidade Amara.org", "Obrigado por assistir"…) e
  usa `hotwords="Jarvis"`. Ruído puro → nada (testado).

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
| Claude | **Processo fixo** (22/11): `claude -p --input-format stream-json --output-format stream-json --verbose --include-partial-messages --permission-mode auto --model sonnet --strict-mcp-config --append-system-prompt <persona + regras de ação + memória> [--resume <sessão>]` na pasta `Data`. Cada pedido = 1 linha JSON `{"type":"user","message":{...}}`; hora, memória nova e trocas de outras IAs vão na mensagem | antes: 5–6 s simples (abria processo novo a cada pedido); agora a partida é paga uma vez (aquecido ao ligar) e a fala começa na 1ª frase — **não medido com Claude real** (login vencido no notebook) | Conta logada (assinatura). Cancelar mata a árvore (`taskkill /T`) e o próximo pedido sobe outro com `--resume`. Sessão que sumiu → começa outra. "OAuth expired"/sem login → sai da fila por 5 min e avisa. `--strict-mcp-config` corta o contexto de ~52k para ~14k tokens. |
| Kimi (`nvidia_pro`) | mesma API da NVIDIA, modelo do config `nvidia_pro.modelo` (escolhido no teste de raciocínio) | — (medir com a chave) | Perguntas difíceis. Mesma chave do Nemotron. Sem internet/PC (ENCAMINHAR). |
| Gemini | `gemini -o json -p " "` com o pedido no **stdin** (Gemini CLI 0.70, conta Google logada) | — (medir após login) | Busca do Google embutida. Sem aprovação no modo headless: só lê/pesquisa, não altera arquivos. |
| Codex | `codex exec --skip-git-repo-check -s workspace-write -c model_reasoning_effort=low -` com o pedido no stdin (Codex CLI 0.171, conta ChatGPT já logada em `~/.codex`) | 4,8–6,2 s ("responda só: ok"); 14,9 s contando arquivos; 8 s respondendo papo como reserva do Claude (22/11, já com a persona: "Oi, Lucas. Tá aqui.") | Desde 22/11 pode escrever na pasta Data (`workspace-write`), aprovação "never". Reserva para pedidos de PC. |

**Instalação das CLIs (atenção):** o app do Claude no Windows é um pacote MSIX; um `npm install -g` rodado de dentro
dele cai numa pasta `AppData` **virtual** que o resto do Windows não vê (o Jarvis e o terminal não achavam o `gemini`).
Por isso Gemini e Codex ficam em `%USERPROFILE%\.local\npm` (`npm install -g --prefix "%USERPROFILE%\.local\npm" @google/gemini-cli @openai/codex`)
e o Jarvis procura lá (`motores.achar_cli`). Essa pasta **não está no PATH**: no terminal, rode `& "$HOME\.local\npm\gemini.cmd"`.
O mesmo vale para `pip install` feito de dentro do app: rode no terminal normal.

- Modo de permissão do Claude = `auto` (classificador bloqueia ação arriscada; o prompt também manda não fazer nada irreversível sem confirmar).
- Quem ele é = `persona` do config.json (vale para todas as IAs). O Claude ainda recebe `REGRAS_ACAO` (motores.py): fazer
  em vez de explicar, dizer o que vai fazer antes de tarefa demorada, abrir coisas com `Start-Process`, nada irreversível sem sim.
- **Não pôr regras de economia de token do chat (frases de 3–6 palavras, sem artigos) no Jarvis**: valem só para o
  Claude Code no chat com o Lucas. No Jarvis deixavam a fala robótica (corrigido em 22/11).

## Configuração

- **Todas as credenciais estão no `Cofre.md` na raiz da pasta `Data`, fora do versionamento e nunca postado no git.**
  `.env.example` mostra as variáveis que o projeto precisa (só `NVIDIA_API_KEY`).
- `config.json` (versionado): nome, `usuario` ("Lucas"), palavras de ativação (variações que o reconhecimento costuma
  errar: jarbas, javis...), `saudacao`/`respostas_ativacao` (listas sorteadas; aceitam `{usuario}` e `{periodo}` =
  Bom dia/Boa tarde/Boa noite), `persona`, voz (`pt-BR-AntonioNeural`, +8%, -4Hz), microfone, `transcricao`, janela de
  seguimento, motores, roteamento, popup.
- `.env` (gitignorado): `NVIDIA_API_KEY` — conta **nova** do Nemotron, só do Jarvis (separada da Mesa-Traders). **Pendente.**
  Sem ela o modo automático manda tudo para o Claude (funciona, mas é mais lento e gasta o limite do plano).
- `preferencias.json` (gitignorado): motor escolhido, popup ligado, posição do popup, se o autostart já foi definido,
  `claude_sessao` (sessão do Claude retomada com `--resume`), `microfone` escolhido sozinho, `whisper_cuda` (resultado
  do teste da placa de vídeo; apague para testar de novo).
- `memoria.json` / `memoria.bak.json` / `conversa.json` (gitignorados, pessoais): memória ensinada e conversa recente.
- Logins das CLIs ficam nas pastas delas, fora do projeto: Gemini em `~/.gemini` (conta Google), Codex em `~/.codex`
  (conta ChatGPT), Claude em `~/.claude`. O Jarvis não guarda nenhuma dessas credenciais.
- `logs/jarvis.log` (gitignorado, 1 MB × 4): tudo que ouviu (com nome), respostas, erros.
- Python 3.13 em `C:\Users\Lucas\AppData\Local\Programs\Python\Python313`. Dependências: `pip install -r requirements.txt`.
- Autostart: `Jarvis.lnk` na pasta Inicializar → `pythonw.exe "...\Jarvis\jarvis.pyw" --boot` (espera 8 s antes de abrir o
  microfone; no boot não abre o painel, só bandeja + saudação). A pasta do projeto está fixada no iCloud (`attrib +P`)
  para os arquivos estarem sempre locais na hora do boot.

## Limitações conhecidas
- Voz neural (Edge) precisa de internet e leva ~1,4–2 s por frase nova (rede); frases curtas repetidas saem do cache na
  hora. Sem internet cai na voz do Windows (SAPI). **edge-tts antigo (7.2.8) leva HTTP 403** e também cai no SAPI:
  manter `edge-tts>=7.3.1`. Sem internet a transcrição segue pelo Whisper (se instalado).
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
- **Calibração do limiar (20/11/2026): `microfone.limiar_minimo` 180 → 20.** Medido (rms por bloco de 30 ms, igual ao
  Jarvis): silêncio = 0,5–0,9; voz do Windows tocada no alto-falante chega com blocos de 10–44; o log "Som sem frase"
  mostrou falas que o limiar 180 deixou passar com p99 de 34 a 97. Simulando o detector do Jarvis nessas gravações:
  limiar 20 capta a fala baixa e dá **0 disparos falsos em 2,5 min de sala quieta**; 25 ou mais já perdia a fala baixa.
  Com ruído de fundo o limiar sobe sozinho (20º percentil do ruído × 2,6). Pelo alto-falante o Google não transcreveu
  (nem amplificando 337×): é qualidade do som que chega, não volume — por isso não há ganho digital.
  **Se ele ainda não reagir à voz normal:** Configurações do Windows → Sistema → Som → Microfone (High Definition Audio)
  → Volume de entrada 100 e, em Propriedades → Níveis, "Aumento do microfone" +10/+20 dB (isso o usuário faz).
- Log para calibrar: "Frase captada: … volume pico … (limiar …)" a cada frase, e **"Som sem frase (1 min): pico, p99"**
  quando houve som bem acima do ruído num minuto sem nenhuma frase captada (= fala que o limiar deixou passar).
- Encerramento usa `os._exit` depois de gravar tudo: em 20/11 um Jarvis ficou vivo após "reiniciar" e dois ouviam/falavam juntos.
- No Windows 11 o ícone novo da bandeja nasce escondido em "^": arraste para a barra uma vez.

## Testes
- `python -m unittest discover -s tests -v` — 90 testes (ativação, comandos locais, hora/data, roteamento com Claude
  primeiro, memória e conversa em disco, memória no prompt de cada IA, CLI pelo stdin e só com login, limpeza de fala,
  DivisorFala, motor Nemotron sem rede, **MotorClaude em processo fixo** com um Claude falso (`tests/claude_falso.py`,
  imita o stream-json real: streaming, sessão, retomada, sem login, processo morrendo, cancelar), transcrição
  (normalização, alucinação, reserva Google↔Whisper), fala em fila/fluxo, corte de silêncio). 3 testes precisam do PySide6.
- Integração (22/11/2026, notebook, Assistente real + Edge TTS real + Claude falso, alto-falante mudo): papo em
  streaming falou "Oi, Lucas." e depois "Tudo certo por aqui."; "que horas são" respondido sem IA; ferramenta → "Abrindo." +
  resultado; fala que não era para ele → quieto; Claude sem login → Codex respondeu em 8 s. Transcritor real: sonda da GPU
  recusou "sem placa NVIDIA" em 0,5 s sem travar; voz fraca transcrita certa em 0,7–1,0 s; ruído → nada.
- **Não testado ainda (22/11):** Claude de verdade em streaming (login vencido no notebook), Whisper na GTX 1650, voz
  real do Lucas no microfone do PC fixo.
- Teste de janela de seguimento (20/11/2026, `--ouvir`): "Jarvis, quanto é 7 vezes 8?" → "Cinquenta e seis." → em 1 s,
  sem o nome, "Ô Maria, pega o carregador pra mim lá na sala" → **ignorado** (IGNORAR); "Jarvis, qual a capital da
  Austrália?" → "Canberra." → "agora sim hein" → "Pois é." (antes respondia "Não entendi o pedido…").
- Ouvido pelo ar (20/11): voz do Windows no alto-falante → com limiar 20 o Jarvis registrou "Frase captada" (pico 39–52),
  mas o Google não transcreveu a voz sintética captada assim; com a voz real o Jarvis já transcreveu ("Agora sim hein", 00:27).
- Ouvido sem hardware (18/11/2026): voz sintetizada + ruído injetados na fila do microfone → detector de fala →
  Google → ativação: "Jarvis, quanto é dois mais dois?" virou comando "Quanto é 2 + 2"; frase sem o nome foi ignorada;
  "que horas são, Jarvis?" virou "Que horas são"; 22 s de silêncio digital dispararam o aviso de microfone mudo.
- Teste real feito em 18/11/2026: Nemotron 0,6 s ("capital do Japão"); Claude com ferramenta 15,7 s (contou arquivos da pasta);
  encaminhamento Nemotron → Claude com busca web 22 s (jogo do Flamengo); reinício via `--pedir "reinicia"`; popup em todos os estados.
