# Karaokê — versão 1.20

Aplicativo de karaokê para Windows, desenvolvido em Python com interface gráfica PySide6. Gerencia uma biblioteca local de instrumentais e letras sincronizadas, organiza uma fila de reprodução e permite preparar músicas por download, separação de voz/instrumental e edição de letras.

## 1. Estrutura do projeto

```text
karaoke/
├── app/                     # Código da aplicação (detalhado abaixo)
├── assets/fonts/            # Fonte Inter usada na letra
├── assets/sounds/           # Efeitos sonoros da pontuação
├── data/
│   ├── karaoke.db           # Banco SQLite: biblioteca e nomes personalizados
│   └── settings.ini         # Preferências (visão da biblioteca)
├── music/
│   ├── original/            # Gravação completa da música
│   ├── instrumental/        # Áudio instrumental (sem vocal)
│   ├── vocal/               # Voz isolada
│   ├── lyric/               # Letras sincronizadas (.lrc)
│   └── cover/               # Uma pasta por álbum, com a capa dentro (opcional)
├── run_karaoke.bat          # Inicialização no Windows
├── requirements.txt         # Dependências utilizadas pelo instalador
└── README.md                # Documentação do projeto
```

A estrutura apresenta uma única instalação atual do aplicativo, sem subpastas de versões antigas. Pastas de dados e músicas devem ser preservadas durante atualizações.

### Código (`app/`)

| Arquivo | Responsabilidade |
|---|---|
| `main.py` | Ponto de entrada (`python -m app.main`) |
| `main_window.py` | Janela principal: fila, player, downloads e cadastro |
| `library_panel.py` | Biblioteca: visões de lista e ícones, busca e capas |
| `karaoke_window.py` | Tela do karaokê em tela cheia |
| `lyrics_view.py` | Letra no estilo Spotify: rolagem, cores, sublinhado e clique |
| `lyrics_editor.py` | Editor de letra |
| `covers.py` | Pastas de capa por álbum e cores extraídas da capa |
| `album_art.py` | Download automático da capa do álbum (iTunes/Deezer) |
| `workflows.py` | Fluxo "separar voz → colar letra → sincronizar", usado pelo editor e pelo download |
| `tasks.py` | Execução de tarefas demoradas em segundo plano, com janela de progresso |
| `database.py` | Banco SQLite e descoberta de músicas nas pastas |
| `lrc.py` | Leitura e escrita de arquivos LRC |
| `player.py` | Reprodução de áudio |
| `youtube_download.py` | Download do YouTube (yt-dlp) |
| `stem_separation.py` | Separação de voz e instrumental (Demucs) |
| `transcription.py` | Sincronização da letra colada com o vocal (Whisper) |
| `online_lyrics.py` | Busca de letras sincronizadas (LRCLIB/NetEase) |
| `genius_lyrics.py` | Texto da letra no Genius e correção das letras sincronizadas |
| `preparation.py` | Tarefas de separação, sincronização e busca de letra |
| `scoring.py` | Pontuação: microfone, afinação da voz original e cálculo da nota |
| `score_overlay.py` | Animação "Sua nota é..." no fim da música |

## 2. Instalação e execução no Windows

**Pré-requisitos:** Python 3 instalado e disponível para o script de inicialização; conexão com a internet na primeira instalação dos pacotes; FFmpeg disponível quando requerido pelos recursos de processamento de áudio.

1. Extraia o projeto para uma pasta local.
2. Execute `run_karaoke.bat`.
3. Na primeira execução, o iniciador cria o ambiente virtual `.venv` e instala os pacotes relacionados em `requirements.txt`.
4. Aguarde a inicialização da interface.

A separação de fontes de áudio utiliza o Demucs e pode necessitar do FFmpeg. Recursos de transcrição/sincronização também podem exigir download de modelos e arquivos adicionais.

### Sincronização automática da letra

A letra colada é sincronizada com o vocal separado usando o Whisper (`faster-whisper`), que é **multilíngue**: funciona com músicas em português, inglês, espanhol etc.

- **Modelo:** `large-v3-turbo` (constante `ALIGNMENT_MODEL` em `app/transcription.py`). Ele é baixado uma única vez (~1,6 GB). Sem internet, o aplicativo usa o modelo `small` já instalado.
- **Idioma:** é identificado pela própria letra colada, e não pelos primeiros segundos do áudio. Isso evita que músicas brasileiras com introdução instrumental ou com "yeah" sejam reconhecidas como inglês.
- **Vocabulário:** as palavras da letra orientam o reconhecimento, o que ajuda com gírias, nomes e grafias informais.
- **Comparação aproximada:** grafias como "tá"/"está", "pra"/"para", "cê"/"você" e pequenos erros de reconhecimento continuam valendo como referência de tempo.
- **Pausas:** em trechos instrumentais, as palavras sem referência ficam junto da linha a que pertencem, em vez de serem espalhadas pela pausa.
- **Resultado por linha:** o LRC gerado contém só o tempo de início de cada linha (`[mm:ss.xx]Texto`), que é o que a tela do karaokê usa. O alinhamento palavra a palavra serve apenas para localizar esse início com precisão.

A porcentagem de correspondência exibida no fim indica quantas palavras foram localizadas diretamente no vocal. Abaixo de 70%, revise os tempos no Editor de letra.

### Buscar letra já sincronizada na internet

No Editor de letra, o botão **Buscar letra sincronizada** procura uma letra que outras pessoas já sincronizaram, usando o artista e o título exibidos na biblioteca. Marcações de vídeo do YouTube no título, como "(Official Video)" ou "[bpOSxM0rNPM]", são ignoradas. Só fontes gratuitas são usadas, nesta ordem:

1. **LRCLIB** (lrclib.net), pela API oficial. A busca usa a duração da música para achar a mesma versão. Quando o servidor responde "ocupado" (erro 503, comum nesse serviço), o aplicativo tenta de novo automaticamente.
2. **NetEase Cloud Music**, pelo pacote `syncedlyrics`.

O Musixmatch não é usado: ele não oferece letra sincronizada de graça e bloqueou o acesso não oficial. Megalobiz e Lyricsify também ficaram de fora porque não respondem mais às buscas.

- Letras com duração muito diferente da sua música (mais de 12 s) são descartadas, por serem de outra versão.
- **Ajuste de atraso:** se a música já tiver vocal separado, o aplicativo compara o padrão de voz/pausa da letra com o vocal e corrige diferenças de até 15 s, como uma introdução mais longa no vídeo do YouTube. Diferenças abaixo de 1 s não são corrigidas.
- A letra encontrada aparece no editor, sem tempos por palavra. Confira tocando a música e clique em **Salvar**.

### Texto conferido no Genius

As letras sincronizadas do LRCLIB/NetEase às vezes têm o texto diferente do original. O **Genius** (genius.com) tem transcrições revisadas, com vozes de apoio entre parênteses, mas sem tempos. Por isso o aplicativo junta os dois: os **tempos** vêm da letra sincronizada e o **texto** vem do Genius.

- **Automático:** o "Buscar letra sincronizada" e o "Baixar música" já conferem o texto no Genius. A mensagem final diz quantas linhas foram corrigidas.
- **Conferir texto no Genius** (Editor de letra): corrige o texto da letra aberta sem mexer nos tempos. Serve também para letras já salvas.
- **Buscar letra no Genius** (janela "Cole a letra correta"): preenche a letra antes de sincronizá-la com o vocal, já sem `[Refrão]`, `[Verso 1]` etc.

Como a correção funciona:

- **Linha presente nas duas fontes:** mantém o tempo e passa a usar o texto do Genius.
- **Linha que o Genius juntou com outra:** o texto é dividido entre as linhas originais, sem perder nenhum tempo.
- **Linha que só o Genius tem:** entra com tempo estimado entre as vizinhas.
- **Linha que só a letra sincronizada tem:** é mantida, porque o Genius às vezes não repete o refrão.
- **Pausas instrumentais:** são preservadas.
- **Letras diferentes demais** (menos da metade das linhas em comum, provavelmente outra versão): nada é alterado.
- **Marca d'água do Genius:** letras "sósias" de outro alfabeto, como o "е" cirílico no lugar do "e", são corrigidas.

O acesso usa o pacote `lyricsgenius` pelas rotas públicas do site (as mesmas da busca do genius.com). A API oficial, que exige criar uma conta e uma chave de acesso, não é necessária.

### Dependências Python (`requirements.txt`)

As dependências declaradas para este projeto são:

```text
PySide6>=6.8,<7
faster-whisper>=1.1,<2
av>=11,<19
yt-dlp>=2024.8.6,<2027
demucs>=4,<5
syncedlyrics>=1.0.1,<2
lyricsgenius>=3.15,<4
```

| Biblioteca | Uso principal |
|---|---|
| `PySide6` | Interface gráfica e reprodução multimídia via Qt |
| `faster-whisper` | Reconhecimento/transcrição de voz para auxiliar o trabalho com letras |
| `av` | Processamento e leitura de conteúdo multimídia |
| `yt-dlp` | Download de conteúdo de áudio/vídeo compatível |
| `demucs` | Separação de vocal e instrumental |
| `syncedlyrics` | Busca de letras sincronizadas no NetEase (a busca no LRCLIB usa a API diretamente) |
| `lyricsgenius` | Texto da letra no Genius |

**Importante:** a seção acima documenta o conteúdo de `requirements.txt`, mas não substitui esse arquivo. Mantenha `requirements.txt` na raiz, pois o iniciador pode depender dele para instalar os pacotes.

## 3. Biblioteca de músicas

O aplicativo verifica as pastas de música ao iniciar. A biblioteca apresenta automaticamente apenas músicas para as quais existe **um instrumental em `music/instrumental` e uma letra `.lrc` correspondente em `music/lyric`**. O vínculo considera o nome-base dos arquivos e admite o sufixo `[Instrumental]`.

A biblioteca tem duas visões, alternadas pelos botões no canto superior direito da lista, como no Explorador de Arquivos. A última escolha fica salva em `data/settings.ini`.

- **Lista:** colunas **Código | Artista | Música | Álbum**, ordenadas primeiro por artista e depois por título.
- **Ícones:** a capa do álbum, com o artista e, abaixo, o nome da música. Músicas sem capa mostram um ♪.

Cada música possui um código único de quatro dígitos, como `0001`, persistido no banco local. A busca procura por título, artista, álbum ou código.

- **Clique simples:** seleciona um item sem colocá-lo na fila.
- **Duplo clique:** adiciona a música à fila. Se ela for a primeira, a reprodução começa; as seguintes aguardam.
- **Renomear:** ajusta o título, o artista e o álbum exibidos na biblioteca e na fila, persistindo em `data/karaoke.db`; não renomeia os arquivos físicos.

Os botões antigos **Adicionar música**, **Importar pasta** e **Excluir** não fazem parte da biblioteca. Download, separação de áudio e editor de letras permanecem disponíveis.

### Baixar músicas do YouTube

Abaixo da biblioteca há dois botões de download. Os dois pedem o link do vídeo e, depois, o nome da música, o artista e o álbum.

- **Baixar música:** para vídeos que **já são a versão instrumental** (karaokê).
  - O áudio é salvo em `music/original` e copiado para `music/instrumental` com o sufixo `[Instrumental]`, sem separar voz e instrumental.
  - Em seguida, o aplicativo busca automaticamente uma letra já sincronizada (LRCLIB/NetEase) e a salva em `music/lyric`. A música aparece na biblioteca na hora.
  - Como não há voz no áudio, os tempos da letra não são conferidos automaticamente. Se a versão instrumental tiver introdução diferente da original, ajuste no Editor de letra.
  - Se nenhuma letra for encontrada, o aplicativo oferece abrir o Editor de letra para colar uma letra sincronizada (`.lrc`). A música só aparece na biblioteca depois de ter letra.
- **Baixar música + separar:** para a música original com voz. O Demucs separa voz e instrumental, e depois você cola a letra para ser sincronizada com o vocal.

## 4. Fila de reprodução

A fila define a sequência real das músicas. O comando **Abrir karaokê** abre a reprodução baseada na primeira música da fila, independentemente do item selecionado na biblioteca. Se a faixa já estiver tocando, a abertura da janela não a reinicia.

- A música atual ocupa a primeira posição, com destaque visual discreto.
- As músicas adicionadas durante a reprodução entram ao final, sem interromper a faixa atual.
- Ao terminar a música ou pressionar **Pular**, a atual sai da fila e a seguinte assume automaticamente.
- **Remover selecionada** retira somente o item escolhido da fila.
- **Limpar fila** remove todas as músicas; a confirmação só é necessária quando há mais de uma música.
- **Play** retoma a música; depois de um **Stop**, recomeça a primeira música da fila. A tecla **Espaço** faz o mesmo.

## 5. Tela do karaokê e letras sincronizadas

A tela apresenta título e artista no topo, capa do álbum quando disponível e trechos da letra sincronizados com os timestamps dos arquivos `.lrc`.

- A letra segue o estilo do **Spotify Desktop**: a música inteira fica numa coluna que rola suavemente, mantendo a linha atual perto do centro da tela. A rolagem reproduz a rolagem suave do Spotify: curva *ease-in-out* de 0,5 s (constante `LYRICS_SCROLL_MS`).
- **Mouse:** ao passar o mouse sobre uma linha, ela fica sublinhada e o cursor vira uma mãozinha. Clicar leva a música para o início daquela linha e dá play, mesmo se estiver pausada (depois de um Stop, a fila é retomada a partir dali).
- **Rolagem:** a roda do mouse rola a letra para cima e para baixo, entre a primeira e a última linha. Enquanto você rola, a letra para de acompanhar a música. Ela volta sozinha 3 s depois (constante `LYRICS_FOLLOW_RESUME_MS`) ou na hora, ao clicar numa linha.
- A sincronia é **por linha**. Só o tempo de início de cada linha do LRC é usado; tempos por palavra (`<mm:ss.xx>`) que o arquivo tenha são ignorados na exibição.
- Linha atual em branco. As linhas já cantadas e as próximas usam tons da cor de fundo (meio-tom e tom claro, respectivamente), com transição de cor como no Spotify (`color 0.3s ease`, constante `LYRICS_COLOR_MS`). Com fundo azul `#003071`, por exemplo, ficam ~`#5182B8` e ~`#A1D3FF`.
- Fonte **Inter** Bold (`assets/fonts/Inter-Bold.ttf`), 64 px em Full HD e proporcional ao tamanho da tela. Sem o arquivo, é usada a Segoe UI.
- Em telas grandes, o texto começa a ~23% da largura e a capa aparece na margem esquerda, com cantos arredondados (15 px) e uma sombra suave. O nome da música e o do artista ficam logo abaixo da capa.
- **Capa interativa:** com o mouse sobre a capa, ela inclina levemente (até 6°), como um tabuleiro em que o canto sob o mouse afunda e o oposto levanta. Clicar na capa pausa ou continua a música; depois de um Stop, retoma a fila. Ajustes nas constantes `LYRICS_COVER_*`.
- Cores, tamanhos, velocidade da rolagem e posição podem ser ajustados nas constantes `LYRICS_*` de `app/lyrics_view.py`. As constantes `COVER_*` (luminosidade do fundo, tons evitados) ficam em `app/covers.py`.
- **Fundo com capa:** como no Spotify, a capa do álbum (`music/cover/<Artista> - <Álbum>/`) é reduzida a 16 cores (median cut) e escolhe-se a cor de destaque, na ordem vibrante → vibrante escura → vibrante clara → apagada → apagada escura. A cor de fundo é essa cor escurecida (luminosidade 22%, constante `COVER_BACKGROUND_LIGHTNESS`), e a letra usa tons dela. Tons de bege, marrom, pele e oliva (amarelados ou alaranjados pouco saturados) só são escolhidos se a capa não tiver outra opção, ajustável em `COVER_BEIGE_HUES` e `COVER_BEIGE_MAX_SATURATION`. Capas em preto e branco resultam num cinza-escuro.
- **Cor escolhida à mão:** para usar uma cor específica num álbum, crie um arquivo `cor.txt` na pasta dele com a cor em hexadecimal (por exemplo, `#5F0048` em `music/cover/Matuê - Máquina do Tempo/cor.txt`). Ela substitui a cor calculada da capa, e os tons da letra acompanham a nova cor.
- **Sem capa:** o fundo é preto, e a letra fica em tons de cinza.

### Controles

A tela do karaokê mantém apenas o botão **Fechar (Esc)**, no canto inferior direito. A tecla **Espaço** continua pausando e retomando a música.

### Pontuação

Com a tela do karaokê aberta, o microfone padrão do Windows é escutado durante a música. Quando a música termina, aparece **"Sua nota é..."** e começa um rufar de tambores de ~6 s enquanto os dígitos giram: a dezena é revelada primeiro e, no prato do rufar, a unidade. Em seguida vem a reação à nota e uma mensagem de parabéns. Cerca de 7 s depois, se houver outra música na fila, aparece **"Próxima música"** com o título, o artista e uma contagem de 10 s para dar tempo de passar o microfone; ao fim da contagem a música começa. Sem próxima música, a tela de nota apenas se fecha. Clique, **Enter**, **Espaço** ou **Esc** revelam a nota na hora e, depois, avançam uma etapa (inclusive começando a próxima música sem esperar a contagem).

| Nota | Reação |
|---|---|
| 95 ou mais | Canhões e chuva de confete + "Kids cheering" |
| 85 a 94 | Duas rajadas dos canhões de confete + torcida da plateia |
| 70 a 84 | Uma rajada menor de confete + aplausos |
| 55 a 69 | Bola de feno atravessando a tela e confete murcho + burburinho e palmas isoladas |
| Abaixo de 55 | Tomates jogados na tela + vaias |

Os sons ficam em `assets/sounds/` (`drumroll.wav`, `kids_cheering.mp3`, `crowd_cheer.mp3`, `crowd_applause.mp3`, `crowd_meh.wav`, `crowd_boo.mp3`) e podem ser trocados por outros com o mesmo nome. O prato do rufar precisa ficar em 6 s (`DRUMROLL_S` em `app/score_overlay.py`). Sem o arquivo, a animação acontece sem aquele som.

- **Com vocal separado** (`music/vocal`): a nota cantada é comparada com a da voz original, ignorando a oitava, de modo que vozes graves e agudas cantam a mesma música. Conta também quanto da parte cantada a pessoa acompanhou.
- **Sem vocal separado:** conta apenas se a pessoa cantou nos trechos com letra.
- **Sem microfone** (ou microfone bloqueado em *Configurações → Privacidade → Microfone*): a nota é sorteada entre 65 e 98, como nas máquinas de bar.
- Pular a música não mostra nota; só o fim natural da faixa.

## 6. Como nomear e associar arquivos

Para que o aplicativo associe instrumental e letra, use o mesmo nome-base nos arquivos. Exemplo:

```text
music/instrumental/Arctic Monkeys - Do I Wanna Know [Instrumental].mp3
music/lyric/Arctic Monkeys - Do I Wanna Know [Instrumental].lrc
```

O vínculo entre áudio e letra usa o **nome físico** do arquivo, e não o artista ou título editado pela função Renomear. Assim, modificar o nome exibido não quebra a associação.

### Capas por álbum

A capa é definida por **artista + álbum**, como cadastrados na biblioteca. Todas as faixas do mesmo álbum usam a mesma capa e, por isso, as mesmas cores no karaokê. Assim, cada álbum de um artista pode ter sua própria estética.

Ao cadastrar um álbum (Renomear ou download), o aplicativo cria a pasta do álbum dentro de `music/cover`. Basta colocar a imagem da capa dentro dela, com qualquer nome:

```text
music/cover/
├── Arctic Monkeys - AM/
│   └── capa.jpg
└── Taylor Swift - The Life of a Showgirl/
    └── capa.jpg
```

- **Download automático:** ao baixar uma música do YouTube informando o álbum, a capa é procurada em segundo plano no iTunes e no Deezer, pelo artista e álbum da pasta, e salva como `cover.jpg` (1000×1000). O resultado aparece na barra de status da janela principal. Se a pasta já tiver uma imagem, ela é mantida. Álbuns que não existem nas lojas (por exemplo, músicas lançadas só no YouTube) ficam sem capa, e a imagem pode ser colocada à mão.
- Se houver mais de uma imagem na pasta, vale a primeira em ordem alfabética.
- Ao abrir, o aplicativo também cria as pastas que faltarem para álbuns já cadastrados.
- Na comparação com o cadastro, maiúsculas, acentos e pontuação são ignorados, então "taylor swift - the life of a showgirl" encontra a mesma pasta. Caracteres proibidos no Windows (`\ / : * ? " < > |`) são retirados do nome da pasta.
- Ao mudar o nome de um álbum, uma pasta nova é criada. A antiga não é apagada, para não perder a imagem que estiver nela.
- Para "Artista feat. Outro", também vale a pasta do artista principal.
- Formatos aceitos: **`.jpg`, `.jpeg`, `.png` e `.webp`**.
- A capa vem **somente** da pasta do álbum, tanto na visão de ícones quanto no karaokê. Imagens soltas em `music/cover`, fora de uma pasta de álbum, são ignoradas. Música sem álbum fica sem capa: ♪ nos ícones e fundo preto no karaokê.
- Ao colocar, trocar ou remover a imagem de uma pasta de álbum, a visão de ícones se atualiza sozinha, sem reabrir o programa.
- A capa é opcional. O LRC correspondente ao instrumental é necessário para a música aparecer na biblioteca.

## 7. Atualização incremental e preservação de dados

As atualizações são entregues somente com os arquivos que sofreram alteração, preservando a estrutura relativa do projeto. Para aplicá-las:

1. Feche o aplicativo.
2. Faça backup de `data/` e `music/` antes de alterações importantes.
3. Extraia o ZIP incremental na raiz da instalação e substitua apenas os arquivos fornecidos.
4. Não apague `data/karaoke.db`: ele mantém os cadastros, códigos e nomes personalizados.
5. Não substitua nem remova `music/` quando a atualização não fornecer alterações específicas nessa pasta.

Toda a documentação é mantida neste **`README.md`**. Não é necessário criar arquivos adicionais como `README_ATUALIZACAO.txt`.

## 8. Observações e solução de problemas

- **Música não aparece na biblioteca:** confirme a presença do instrumental e do `.lrc` correspondente, com nomes-base compatíveis, nas pastas corretas.
- **Capa não aparece:** confira se a música tem álbum cadastrado (Renomear) e se a pasta `music/cover/<Artista> - <Álbum>/` contém uma imagem com extensão aceita.
- **Nome personalizado desapareceu:** verifique se `data/karaoke.db` foi preservado entre atualizações.
- **Falhas de instalação:** confira a disponibilidade do Python, acesso à internet e dependências listadas em `requirements.txt`.
- **Problemas de áudio ou processamento:** verifique FFmpeg e as dependências multimídia instaladas.

---

**Referência de funcionalidades:** evolução do projeto até a versão **1.20**. A documentação descreve o funcionamento previsto nas atualizações; a execução final deve ser validada no ambiente Windows em que o karaokê está instalado.
