# Karaokê v0.8

Aplicativo desktop em Python + PySide6 para biblioteca local, reprodução de áudio, karaokê em tela cheia, edição de LRC, download de áudio por link e preparação automática de uma faixa normal para uso em karaokê.

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
`- karaoke_v0_8\
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

Dentro de `karaoke_v0_8`, execute:

```bat
run_karaoke.bat
```

O script usa:

```text
..\.venv\Scripts\python.exe
```

Instale/atualize as dependências a partir da raiz `karaoke`:

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\karaoke_v0_8\requirements.txt
```

## Dependências

- `PySide6`: interface e áudio.
- `yt-dlp`: download de áudio por link.
- `demucs`: separação da música em vocal e instrumental.
- `faster-whisper`: reconhecimento da voz e geração da letra sincronizada.
- `av>=11,<19`: compatibilidade atual do `faster-whisper` com PyAV.

Na primeira execução, Demucs e Whisper podem baixar modelos. Esses modelos ficam em cache e são reutilizados nas próximas músicas.

## Fluxo principal da v0.8

O fluxo recomendado agora é baixar **somente a versão normal da música, com vocal**.

Na tela principal existe:

```text
Baixar + preparar karaokê
```

Ao informar um link, o sistema faz automaticamente:

```text
YouTube / link de áudio
        |
        v
música original com vocal
        |
        v
Demucs
   |             |
   v             v
vocals.wav     instrumental
   |             |
   v             |
Whisper          |
   |             |
   v             |
LRC sincronizado |
   |             |
   +-------> biblioteca
                  |
                  v
          tocar instrumental
```

Etapas:

1. baixa a música normal;
2. mantém o arquivo original como referência;
3. separa a faixa em `vocals` e `no_vocals` usando Demucs;
4. usa somente o stem de vocal para o reconhecimento da letra;
5. gera um `.lrc` com timestamps por linha e, quando disponível, por palavra;
6. grava o instrumental na pasta compartilhada `music`;
7. atualiza o cadastro para que o player passe a tocar o instrumental;
8. mantém o original e o vocal vinculados ao cadastro para reprocessamento.

O usuário não precisa baixar manualmente uma versão normal e outra instrumental.

## Arquivos gerados

Exemplo para uma música baixada:

```text
music\
|- Nome da música [id].mp3                 <- original baixado
|- Nome da música [id] [Vocals].wav        <- vocal separado
|- Nome da música [id] [Instrumental].mp3  <- instrumental, se FFmpeg existir
`- Nome da música [id] [Instrumental].lrc  <- letra sincronizada
```

Quando `ffmpeg` não estiver disponível, o instrumental permanece em `.wav`.

## FFmpeg

O FFmpeg não é obrigatório para a separação do Demucs porque os stems podem ser gerados em WAV.

Quando `ffmpeg` está disponível no PATH do Windows:

- o download do YouTube é convertido para MP3;
- o instrumental final também é convertido para MP3 a 192 kbps;
- isso reduz bastante o espaço usado no disco.

Sem FFmpeg, o projeto mantém formatos compatíveis disponíveis e o instrumental pode ficar em WAV.

## Preparar uma música já existente

O Editor de letra agora possui:

```text
Preparar karaokê automaticamente
```

Use esse botão quando a música já estiver na biblioteca e ainda for a versão normal com vocal.

O programa:

1. usa `source_audio_path` quando disponível;
2. caso contrário usa o próprio `audio_path` atual;
3. separa vocal/instrumental;
4. transcreve o vocal;
5. gera o LRC;
6. altera o `audio_path` para o instrumental.

Se a faixa já for instrumental e não houver um original com vocal salvo, não há voz suficiente para o Whisper descobrir sozinho os tempos da letra.

## Recuperação em caso de erro

Se o download terminar e a separação/transcrição falhar:

- o áudio original permanece cadastrado;
- ele não precisa ser baixado novamente;
- abra o Editor de letra;
- clique em **Preparar karaokê automaticamente** para tentar novamente.

## Reconhecimento de canto

A transcrição agora é executada com parâmetros mais tolerantes a canto:

- `word_timestamps=True`;
- `vad_filter=False`;
- `beam_size=5`;
- `condition_on_previous_text=False`.

A separação prévia do vocal aumenta bastante a chance de reconhecimento porque bateria, baixo, guitarra e outros instrumentos deixam de competir diretamente com a voz.

Mesmo assim, canto não é tão previsível quanto fala comum. O LRC deve continuar podendo ser revisado no editor.

## Editor de letra

Mantém os recursos anteriores:

- colar/digitar letra;
- sincronizar linha atual;
- sincronizar palavras com F8;
- remover tempos;
- gerar letra automaticamente a partir do áudio atual;
- preparar automaticamente uma música normal separando vocal/instrumental.

A opção **Gerar letra automaticamente** apenas transcreve o arquivo fornecido. Para uma música completa com instrumentos, prefira **Preparar karaokê automaticamente**, que primeiro separa a voz.

## Volume

- padrão: `50%`;
- toda nova música carregada começa em 50%;
- o percentual é mostrado ao lado do slider;
- tela principal e tela cheia permanecem sincronizadas.

## Biblioteca

Continua permitindo:

- adicionar uma música individualmente;
- importar uma pasta inteira;
- reconhecer `Artista - Música.ext`;
- associar `.lrc` com o mesmo nome;
- baixar e preparar uma música por link.

Formatos locais reconhecidos:

```text
.mp3 .wav .ogg .flac .m4a .aac .wma
```

## Banco de dados

A tabela `songs` continua compatível com versões anteriores. A v0.8 adiciona automaticamente, se ainda não existirem:

```text
source_audio_path
vocals_path
source_url
prepared_automatically
```

Significado:

- `audio_path`: arquivo usado para tocar no karaokê; após preparação passa a ser o instrumental.
- `source_audio_path`: música original com vocal.
- `vocals_path`: stem de vocal gerado pelo Demucs.
- `lyrics_path`: LRC sincronizado.
- `source_url`: link usado no download, quando aplicável.
- `prepared_automatically`: indica que a faixa já passou pelo fluxo Demucs + Whisper.

A migração é feita por `ALTER TABLE` durante `init_db()` e preserva os registros existentes.

## Arquivos importantes

```text
app/main.py
```

Interface, biblioteca, editor, tela de karaokê, download e orquestração do fluxo automático.

```text
app/youtube_download.py
```

Download do áudio usando `yt-dlp`.

```text
app/stem_separation.py
```

Integração com Demucs e geração de vocal/instrumental.

```text
app/preparation.py
```

Pipeline em thread: separar -> transcrever -> gerar LRC.

```text
app/transcription.py
```

Integração com `faster-whisper`.

```text
app/player.py
```

Player e volume padrão de 50%.

```text
app/database.py
```

SQLite, migração de colunas, caminhos compartilhados e metadados dos arquivos gerados.

```text
app/lrc.py
```

Parser/gerador de LRC tradicional e por palavra.

## Contexto para um próximo chat

Ao continuar o projeto, considerar como requisitos já estabelecidos:

- manter `.venv`, `data` e `music` compartilhados na raiz `karaoke`;
- não criar um ambiente virtual para cada versão;
- volume inicial sempre 50%, exibindo percentual;
- biblioteca deve aceitar cadastro individual, importação em lote e download por link;
- fluxo preferencial: baixar apenas a música normal com vocal;
- separar automaticamente voz e instrumental com Demucs;
- transcrever o vocal separado com faster-whisper;
- gerar LRC automaticamente;
- tocar o instrumental no karaokê;
- manter a música original para permitir reprocessamento;
- manter sincronização manual por linha/F8 como fallback;
- em caso de falha no processamento, nunca exigir novo download se o original já estiver salvo;
- preservar compatibilidade do banco compartilhado entre versões;
- usar o botão **Preparar karaokê automaticamente** para músicas normais já cadastradas.

## Uso responsável

Use o download para conteúdo que você tenha autorização para baixar ou utilizar. O projeto não implementa contorno de DRM nem mecanismos de proteção.
