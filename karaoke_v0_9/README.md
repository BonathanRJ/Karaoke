# Karaokê v0.9

Aplicativo desktop em Python + PySide6 para biblioteca local, reprodução de áudio, karaokê em tela cheia, download de áudio por link, separação de vocal/instrumental e sincronização de letra fornecida manualmente pelo usuário.

## Regra obrigatória para futuras alterações por IA

Este README é a principal fonte de contexto do projeto.

**Toda IA que alterar funcionalidades, fluxo, dependências, banco de dados, estrutura de pastas ou decisões importantes do projeto deve atualizar este README na mesma entrega.**

Ao criar uma nova versão, a IA deve:

1. ler este README antes de alterar o projeto;
2. preservar as decisões já adotadas, salvo quando o usuário pedir mudança;
3. atualizar o número da versão e o fluxo atual;
4. documentar novas dependências e comandos de instalação;
5. documentar mudanças no banco e nos arquivos gerados;
6. registrar problemas conhecidos e decisões relevantes para que um novo chat consiga continuar o desenvolvimento sem depender do histórico anterior.

## Estrutura compartilhada

```text
karaoke\
|- .venv\
|- data\
|  `- karaoke.db
|- music\
|- karaoke_v0_5\
|- karaoke_v0_6\
|- karaoke_v0_7\
|- karaoke_v0_8\
`- karaoke_v0_9\
```

`.venv`, `data` e `music` ficam fora da pasta de cada versão e são compartilhados.

Banco:

```text
..\data\karaoke.db
```

Músicas e arquivos gerados:

```text
..\music\
```

## Executar

Dentro de `karaoke_v0_9`, execute:

```bat
run_karaoke.bat
```

O script usa o ambiente compartilhado:

```text
..\.venv\Scripts\python.exe
```

Para instalar/atualizar dependências a partir da raiz `karaoke`:

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\karaoke_v0_9\requirements.txt
```

## Dependências

- `PySide6`: interface e áudio.
- `yt-dlp`: download de áudio por link.
- `demucs`: separação da música em vocal e instrumental.
- `faster-whisper`: análise do vocal para localizar os tempos da letra fornecida pelo usuário.
- `av>=11,<19`: compatibilidade atual do `faster-whisper` com PyAV.
- `FFmpeg`: instalado no Windows e disponível no PATH. É usado para conversão de áudio e também ajuda o Demucs a abrir formatos como M4A.

Na primeira execução, Demucs e Whisper podem baixar modelos. Eles ficam em cache para reutilização.

## Decisão principal da v0.9: a IA não cria mais a letra

Nas versões anteriores, o Whisper ouvia o vocal e tentava descobrir ao mesmo tempo **quais palavras estavam sendo cantadas** e **em que momento elas eram cantadas**.

Isso causava erros de texto, especialmente em canto, pronúncia artística, backing vocals e trechos com efeitos.

A partir da v0.9, a letra fornecida pelo usuário é a fonte de verdade.

O Whisper é usado somente como referência para localizar os tempos do vocal.

Fluxo conceitual:

```text
letra correta fornecida pelo usuário
              +
        vocal separado
              |
              v
 localizar os momentos cantados
              |
              v
      gerar LRC sincronizado
```

O texto digitado/colado pelo usuário deve ser preservado no LRC. O reconhecimento do áudio não deve substituir palavras da letra manual.

## Novo fluxo obrigatório

O fluxo recomendado é baixar somente a **música normal com vocal**.

Na tela principal existe o botão:

```text
Baixar música + separar
```

O fluxo agora é:

```text
Link do YouTube
      |
      v
baixar música normal
      |
      v
manter música original
      |
      v
Demucs separa
   |             |
   v             v
vocal          instrumental
   |
   v
PARAR E PEDIR A LETRA AO USUÁRIO
   |
   v
usuário cola a letra correta
   |
   v
Whisper analisa o vocal somente para tempos
   |
   v
alinhar letra manual aos tempos reconhecidos
   |
   v
gerar LRC
   |
   v
player usa o instrumental
```

### Etapas detalhadas

1. o usuário cola o link da música normal;
2. o sistema baixa a faixa e mantém o original;
3. o Demucs separa `vocals` e `instrumental`;
4. a separação é salva no banco imediatamente;
5. o sistema abre obrigatoriamente a janela **Cole a letra correta**;
6. o usuário cola a letra completa, preferencialmente uma linha por verso;
7. o sistema analisa o vocal separado;
8. a letra manual é comparada ao que foi detectado no áudio somente para encontrar pontos de referência de tempo;
9. palavras não reconhecidas são posicionadas entre pontos de referência próximos;
10. o LRC é criado usando exatamente as palavras fornecidas pelo usuário;
11. o cadastro passa a usar o instrumental como áudio principal do karaokê.

Se o usuário cancelar a etapa de letra, original, vocal e instrumental continuam salvos. A letra fica pendente e pode ser adicionada depois pelo Editor de letra.

## Como colar a letra

Para melhorar a sincronização:

- use uma linha por verso/frase cantada;
- mantenha a ordem exata da música;
- cole somente o que realmente é cantado;
- evite cabeçalhos como `[Refrão]`, `[Verso 1]`, `[Ponte]` e comentários de sites;
- repetições devem aparecer repetidas na letra, exatamente na ordem em que são cantadas.

O sistema preserva o texto fornecido. Pontuação, acentos e grafia vêm da letra manual, não do reconhecimento do Whisper.

## Precisão e indicador de correspondência

Após sincronizar, o programa mostra um percentual de **correspondência com o vocal**.

Esse percentual não significa “porcentagem de letra correta”, porque a letra correta já foi fornecida pelo usuário. Ele indica quanto da letra encontrou pontos diretos de referência no reconhecimento do vocal.

- correspondência alta: normalmente exige pouca revisão;
- correspondência parcial: a letra continua correta, mas alguns tempos podem precisar de ajuste no Editor.

Mesmo no novo fluxo não é possível prometer sincronização 100% perfeita para toda música. Refrões repetidos, backing vocals, sobreposição de vozes e partes muito rápidas ainda podem deslocar tempos.

## Editor de letra

O Editor mantém:

- colar/digitar letra;
- sincronizar linha atual manualmente;
- sincronizar palavras da linha com F8;
- remover tempos;
- revisar o LRC gerado.

Na v0.9, os botões principais relacionados ao fluxo automático são:

```text
Preparar voz/instrumental
Sincronizar letra colada
```

### Preparar voz/instrumental

Se ainda não houver stems, usa a música original e executa o Demucs.

Ao terminar, pede a letra correta antes da sincronização.

Se vocal e instrumental já existirem, não repete a separação desnecessariamente: o usuário pode seguir direto para a letra.

### Sincronizar letra colada

Exige que exista um stem vocal.

Abre a janela de letra com o conteúdo já existente como preenchimento, quando houver. Depois usa o vocal apenas para localizar tempos e salva o LRC.

## Arquivos gerados

Exemplo:

```text
music\
|- Nome da música [id].mp3                 <- original com vocal
|- Nome da música [id] [Vocals].wav        <- voz separada
|- Nome da música [id] [Instrumental].mp3  <- faixa usada no karaokê
`- Nome da música [id] [Instrumental].lrc  <- letra manual sincronizada
```

Se não for possível converter o instrumental para MP3, ele pode permanecer em WAV.

## FFmpeg e Windows

O FFmpeg deve estar disponível no PATH.

Instalação usada durante o desenvolvimento:

```powershell
winget install Gyan.FFmpeg
```

Depois de instalar, feche e abra o PowerShell e valide:

```powershell
ffmpeg -version
```

A v0.9 também força UTF-8 ao executar o Demucs para evitar falhas com caracteres Unicode presentes em títulos de vídeos do YouTube.

## Download por link

O download usa `yt-dlp` e prioriza áudio de melhor qualidade.

Quando o FFmpeg está disponível, converte o download para MP3.

Use o recurso somente para conteúdo que você tenha autorização para baixar ou utilizar.

## Volume

- padrão: `50%`;
- cada nova música começa em 50%;
- percentual aparece ao lado do slider;
- tela principal e tela cheia permanecem sincronizadas.

## Biblioteca

Permite:

- adicionar música individualmente;
- importar uma pasta;
- reconhecer nomes no padrão `Artista - Música.ext`;
- associar `.lrc` com o mesmo nome;
- baixar música por link;
- separar original em vocal/instrumental;
- continuar posteriormente uma música cuja letra ainda esteja pendente.

Formatos reconhecidos:

```text
.mp3 .wav .ogg .flac .m4a .aac .wma
```

## Banco de dados

A tabela `songs` utiliza, entre outras, estas colunas:

```text
source_audio_path
vocals_path
source_url
prepared_automatically
```

Significado:

- `audio_path`: áudio usado pelo player; após separação passa a apontar para o instrumental.
- `source_audio_path`: música original com vocal.
- `vocals_path`: vocal separado pelo Demucs.
- `lyrics_path`: LRC com a letra manual sincronizada.
- `source_url`: link usado para baixar a música.
- `prepared_automatically`: `1` somente depois que a letra manual já foi sincronizada; enquanto só os stems existem, permanece `0`.

A v0.9 adiciona duas rotinas de atualização lógica:

- salvar vocal/instrumental mesmo antes da letra (`update_song_stems`);
- marcar a música como sincronizada depois da criação do LRC (`mark_song_synced`).

## Arquivos importantes

```text
app/main.py
```

Interface, biblioteca, editor, download e coordenação do fluxo em duas etapas.

```text
app/youtube_download.py
```

Download usando `yt-dlp`.

```text
app/stem_separation.py
```

Demucs, geração de vocal/instrumental e proteção UTF-8 para nomes de arquivos.

```text
app/preparation.py
```

Workers de segundo plano separados em:

- `StemSeparationWorker`: somente separação;
- `LyricsAlignmentWorker`: somente sincronização da letra manual.

```text
app/transcription.py
```

Analisa o vocal, cria pontos de referência e alinha a letra manual. No fluxo principal da v0.9, o texto reconhecido pelo Whisper não substitui a letra fornecida pelo usuário.

```text
app/lrc.py
```

Leitura e geração de LRC, inclusive timestamps por palavra.

```text
app/database.py
```

SQLite, migrações e persistência dos caminhos compartilhados.

## Problemas conhecidos / cuidados

- Demucs e Whisper podem demorar na primeira execução devido ao download dos modelos.
- Avisos do Hugging Face sobre symlinks ou requisições sem token não significam necessariamente falha.
- Se a letra colada não estiver na mesma ordem da gravação, a sincronização perde precisão.
- Se a versão da música tiver introdução, refrões ou versos diferentes da letra copiada, os tempos podem deslocar.
- Backing vocals e duetos ainda são casos difíceis.
- A sincronização automática deve sempre continuar editável manualmente.

## Objetivo das próximas evoluções

Priorizar precisão e facilidade de revisão em vez de tentar adivinhar o texto da música. Possíveis evoluções:

- melhorar o algoritmo de alinhamento entre letra manual e vocal;
- mostrar visualmente quais linhas tiveram alta/baixa confiança;
- permitir corrigir deslocamento global de uma linha ou bloco;
- permitir recalcular somente um trecho da música;
- melhorar tratamento de duetos e backing vocals.
