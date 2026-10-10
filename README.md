# Karaokê — versão 1.20

Aplicativo de karaokê para Windows, desenvolvido em Python com interface gráfica PySide6. Gerencia uma biblioteca local de instrumentais e letras sincronizadas, organiza uma fila de reprodução e permite preparar músicas por download, separação de voz/instrumental e edição de letras.

## 1. Estrutura do projeto

```text
karaoke/
├── app/                     # Código da aplicação
├── data/
│   └── karaoke.db           # Banco SQLite: biblioteca e nomes personalizados
├── music/
│   ├── original/            # Gravação completa da música
│   ├── instrumental/        # Áudio instrumental (sem vocal)
│   ├── vocal/               # Voz isolada
│   ├── lyric/               # Letras sincronizadas (.lrc)
│   └── cover/               # Capas dos álbuns (opcional)
├── run_karaoke.bat          # Inicialização no Windows
├── requirements.txt         # Dependências utilizadas pelo instalador
└── README.md                # Documentação do projeto
```

A estrutura apresenta uma única instalação atual do aplicativo, sem subpastas de versões antigas. Pastas de dados e músicas devem ser preservadas durante atualizações.

## 2. Instalação e execução no Windows

**Pré-requisitos:** Python 3 instalado e disponível para o script de inicialização; conexão com a internet na primeira instalação dos pacotes; FFmpeg disponível quando requerido pelos recursos de processamento de áudio.

1. Extraia o projeto para uma pasta local.
2. Execute `run_karaoke.bat`.
3. Na primeira execução, o iniciador cria o ambiente virtual `.venv` e instala os pacotes relacionados em `requirements.txt`.
4. Aguarde a inicialização da interface.

A separação de fontes de áudio utiliza o Demucs e pode necessitar do FFmpeg. Recursos de transcrição/sincronização também podem exigir download de modelos e arquivos adicionais.

### Dependências Python (`requirements.txt`)

As dependências declaradas para este projeto são:

```text
PySide6>=6.8,<7
faster-whisper>=1.1,<2
av>=11,<19
yt-dlp>=2024.8.6,<2027
demucs>=4,<5
```

| Biblioteca | Uso principal |
|---|---|
| `PySide6` | Interface gráfica e reprodução multimídia via Qt |
| `faster-whisper` | Reconhecimento/transcrição de voz para auxiliar o trabalho com letras |
| `av` | Processamento e leitura de conteúdo multimídia |
| `yt-dlp` | Download de conteúdo de áudio/vídeo compatível |
| `demucs` | Separação de vocal e instrumental |

**Importante:** a seção acima documenta o conteúdo de `requirements.txt`, mas não substitui esse arquivo. Mantenha `requirements.txt` na raiz, pois o iniciador pode depender dele para instalar os pacotes.

## 3. Biblioteca de músicas

O aplicativo verifica as pastas de música ao iniciar. A biblioteca apresenta automaticamente apenas músicas para as quais existe **um instrumental em `music/instrumental` e uma letra `.lrc` correspondente em `music/lyric`**. O vínculo considera o nome-base dos arquivos e admite o sufixo `[Instrumental]`.

A tabela da biblioteca contém as colunas **Código | Artista | Música**, ordenadas primeiro por artista e depois por título. Cada música possui um código único de quatro dígitos, como `0001`, persistido no banco local. É possível pesquisar músicas pelo código.

- **Clique simples:** seleciona um item sem colocá-lo na fila.
- **Duplo clique:** adiciona a música à fila. Se ela for a primeira, a reprodução começa; as seguintes aguardam.
- **Renomear:** ajusta apenas o artista e o título exibidos na biblioteca e na fila, persistindo em `data/karaoke.db`; não renomeia os arquivos físicos.

Os botões antigos **Adicionar música**, **Importar pasta** e **Excluir** não fazem parte da biblioteca. Download, separação de áudio e editor de letras permanecem disponíveis.

## 4. Fila de reprodução

A fila define a sequência real das músicas. O comando **Abrir karaokê** abre a reprodução baseada na primeira música da fila, independentemente do item selecionado na biblioteca. Se a faixa já estiver tocando, a abertura da janela não a reinicia.

- A música atual ocupa a primeira posição, com destaque visual discreto.
- As músicas adicionadas durante a reprodução entram ao final, sem interromper a faixa atual.
- Ao terminar a música ou pressionar **Pular**, a atual sai da fila e a seguinte assume automaticamente.
- **Remover selecionada** retira somente o item escolhido da fila.
- **Limpar fila** remove todas as músicas; a confirmação só é necessária quando há mais de uma música.
- Um histórico temporário da sessão permite retornar a faixas reproduzidas anteriormente.

## 5. Tela do karaokê e letras sincronizadas

A tela apresenta título e artista no topo, capa do álbum quando disponível e trechos da letra sincronizados com os timestamps dos arquivos `.lrc`.

- O verso atual aparece em destaque; versos próximos ficam em tonalidades mais suaves, oferecendo tempo para leitura.
- A apresentação comporta até **seis trechos simultâneos**: até dois anteriores, o atual e até três seguintes.
- A troca de versos usa uma transição curta de opacidade, sem mudar os tempos informados pelo LRC.
- **Sem capa:** o fundo da área de letras é preto.
- **Com capa:** o aplicativo escolhe uma tonalidade representativa da imagem e a escurece para manter a legibilidade da letra.
- O painel inferior de reprodução permanece preto, separado visualmente da área das letras.

### Controles de reprodução

- **Voltar:** se a música estiver em até **2 segundos** de reprodução, tenta retornar à última faixa tocada; após esse ponto, reinicia a música atual. Sem histórico anterior, reinicia a atual.
- **Play/Pause:** botão central circular que alterna entre ícones de reproduzir e pausar.
- **Pular:** avança para a próxima música da fila.
- **Progresso:** exibe tempo decorrido e duração total (`m:ss`) e permite alterar a posição de reprodução.
- **Volume:** controla a intensidade do áudio.
- **Fechar (Esc):** fecha a tela de karaokê.

Na organização da barra inferior, os controles de reprodução ficam centralizados acima de uma linha horizontal com volume à esquerda, progresso ao centro e comando de fechamento à direita. Os ícones Voltar e Pular são de 20 px e o botão Play/Pause tem 38 px, conforme os ajustes visuais até a v1.20.

## 6. Como nomear e associar arquivos

Para que o aplicativo associe instrumental, letra e capa, use o mesmo nome-base nos arquivos. Exemplo:

```text
music/instrumental/Arctic Monkeys - Do I Wanna Know [Instrumental].mp3
music/lyric/Arctic Monkeys - Do I Wanna Know [Instrumental].lrc
music/cover/Arctic Monkeys - Do I Wanna Know [Instrumental].jpg
```

A capa também pode omitir o sufixo `[Instrumental]` do nome. São aceitos os formatos **`.jpg`, `.jpeg`, `.png` e `.webp`**. A capa é opcional; o LRC correspondente ao instrumental é necessário para a música aparecer automaticamente na biblioteca.

O vínculo usa o **nome físico** do arquivo, e não o artista ou título editado pela função Renomear. Assim, modificar o nome exibido não quebra a associação com áudio, letra e capa.

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
- **Capa não aparece:** confira `music/cover`, a extensão da imagem e sua correspondência com o nome físico do instrumental.
- **Nome personalizado desapareceu:** verifique se `data/karaoke.db` foi preservado entre atualizações.
- **Falhas de instalação:** confira a disponibilidade do Python, acesso à internet e dependências listadas em `requirements.txt`.
- **Problemas de áudio ou processamento:** verifique FFmpeg e as dependências multimídia instaladas.

---

**Referência de funcionalidades:** evolução do projeto até a versão **1.20**. A documentação descreve o funcionamento previsto nas atualizações; a execução final deve ser validada no ambiente Windows em que o karaokê está instalado.
