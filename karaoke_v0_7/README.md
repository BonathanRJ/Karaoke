# Karaokê v0.7

Aplicativo desktop em Python + PySide6 para manter uma biblioteca local de músicas, reproduzir áudio, exibir letras sincronizadas em tela cheia e editar/gerar arquivos LRC.

Esta versão mantém compatibilidade com a estrutura compartilhada das versões anteriores e acrescenta melhorias para reduzir o trabalho manual de cadastrar músicas e sincronizar letras.

## Estrutura esperada

```text
karaoke\
|- .venv\
|- data\
|  `- karaoke.db
|- music\
|- karaoke_v0_1\
|- karaoke_v0_2\
|- karaoke_v0_3\
|- karaoke_v0_4\
|- karaoke_v0_5\
|- karaoke_v0_6\
`- karaoke_v0_7\
```

`.venv`, `data` e `music` são compartilhados entre as versões.

O banco usado continua sendo:

```text
..\data\karaoke.db
```

E a biblioteca física de áudio continua sendo:

```text
..\music\
```

## Executar

Abra `run_karaoke.bat` dentro de `karaoke_v0_7`.

O script procura automaticamente:

```text
..\.venv\Scripts\python.exe
```

Para instalar/atualizar as dependências:

```bat
..\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Dependências principais

- `PySide6`: interface e reprodução de áudio.
- `faster-whisper`: transcrição automática local da música para LRC.
- `yt-dlp`: download de áudio a partir de links do YouTube para agilizar testes e inclusão na biblioteca.

A aplicação continua abrindo mesmo se `faster-whisper` ainda não estiver instalado. Nesse caso, somente o recurso **Gerar letra automaticamente** mostrará a instrução para instalar as dependências.

Na primeira transcrição automática, o modelo Whisper pode precisar ser baixado. Depois disso ele pode ser reutilizado localmente.

**Compatibilidade PyAV:** o `faster-whisper` usado pelo projeto ainda passa o parâmetro `metadata_errors` para o PyAV. O PyAV 19 removeu esse parâmetro, portanto o projeto fixa `av>=11,<19`. Se o ambiente já tiver PyAV 19 instalado, execute `python -m pip install "av<19"` no mesmo ambiente virtual antes de gerar a letra.

## Volume

- O volume padrão agora é **50%**.
- Sempre que uma música é iniciada pela biblioteca, o volume volta para **50%**.
- O controle de volume mostra o percentual atual, por exemplo `50%`.
- O percentual fica sincronizado entre a tela principal e a tela cheia do karaokê.

## Biblioteca de músicas

Não é mais necessário cadastrar todas as músicas uma a uma.

A tela principal possui três formas de inclusão:

### Adicionar música

Mantém o comportamento individual. O usuário escolhe um arquivo e informa título/artista.

Se existir um `.lrc` com o mesmo nome e na mesma pasta do áudio, ele é copiado e associado automaticamente.

Exemplo:

```text
Queen - Bohemian Rhapsody.mp3
Queen - Bohemian Rhapsody.lrc
```

### Importar pasta

Permite selecionar uma pasta inteira. O programa procura arquivos de áudio também em subpastas e importa todos de uma vez.

Formatos reconhecidos:

```text
.mp3 .wav .ogg .flac .m4a .aac .wma
```

Quando o nome do arquivo estiver no padrão:

```text
Artista - Título.mp3
```

o sistema separa automaticamente artista e título.

Se não houver ` - `, o nome do arquivo vira o título e o artista fica vazio.

Se houver um `.lrc` correspondente ao lado do áudio, a letra também é importada automaticamente.

Também é possível selecionar a própria pasta compartilhada `music` para cadastrar no banco arquivos que já tenham sido colocados nela manualmente.

> A importação local trabalha com arquivos fornecidos pelo usuário. Para testes rápidos, a versão v0.7 também possui download de áudio por link do YouTube, descrito na seção seguinte.

## Baixar música pelo YouTube

A tela principal agora possui o botão:

```text
Baixar do YouTube
```

Fluxo:

1. clique em **Baixar do YouTube**;
2. cole o link completo do vídeo;
3. o aplicativo consulta as informações do vídeo;
4. baixa somente o áudio para a pasta compartilhada `..\music\`;
5. usa o título do vídeo/faixa como título da música;
6. usa artista, criador, canal ou uploader como artista quando essa informação estiver disponível;
7. cadastra automaticamente o arquivo no banco compartilhado;
8. atualiza a biblioteca sem exigir cadastro manual.

O download é executado em uma thread separada para não congelar a interface. Uma janela mostra o andamento do download.

### Formato do áudio

Se `ffmpeg` estiver disponível no Windows, o download é convertido automaticamente para MP3 a 192 kbps.

Se `ffmpeg` não estiver instalado, o programa prioriza áudio `m4a`, que normalmente pode ser reproduzido diretamente pelo player. Assim, o `ffmpeg` não é obrigatório para testar o recurso.

O nome físico do arquivo inclui o ID do vídeo do YouTube para evitar colisões quando dois vídeos possuem o mesmo título.

### Observação sobre uso

Use o recurso para conteúdo que você tenha autorização para baixar, conteúdo próprio, livre/licenciado ou material permitido pelos termos aplicáveis. O aplicativo não contorna DRM nem mecanismos de proteção. Alguns vídeos podem exigir autenticação ou podem não estar disponíveis para download.

### Atualização do yt-dlp

O YouTube altera seu funcionamento com frequência. Se o download deixar de funcionar mesmo com um link válido, atualize primeiro o `yt-dlp` no ambiente compartilhado:

```bat
..\.venv\Scripts\python.exe -m pip install -U yt-dlp
```

## Letras e sincronização

Existem agora três níveis de trabalho.

### 1. LRC já existente

Se a música já vier acompanhada de um `.lrc`, nenhuma sincronização manual é necessária.

### 2. Geração automática

No **Editor de letra**, use:

```text
Gerar letra automaticamente
```

O sistema usa `faster-whisper` localmente para:

1. ouvir/transcrever o áudio;
2. detectar os trechos cantados;
3. obter timestamp das palavras quando disponível;
4. gerar automaticamente um `.lrc` estendido;
5. salvar e associar o arquivo à música;
6. carregar o resultado no editor para revisão.

Exemplo do formato gerado:

```text
[00:12.50]<00:12.50>Eu <00:13.10>sei <00:13.80>que <00:15.20>vou <00:16.00>te <00:17.40>amar
```

A transcrição automática reduz drasticamente o trabalho de marcar linha por linha e palavra por palavra, mas pode cometer erros no texto, especialmente com música alta, backing vocal, pronúncia ou mixagem. Por isso o editor manual continua disponível para revisão/correção.

### 3. Ajuste manual

O comportamento anterior continua disponível:

- **Sincronizar linha atual**: grava o tempo da linha selecionada.
- **Sincronizar palavras da linha**: abre o modo preciso por palavra.
- Durante a sincronização por palavra, pressione **F8** no começo de cada palavra.
- **Remover tempos**: mantém o texto e apaga timestamps para refazer a sincronização.

## Tela do karaokê

A tela cheia mantém:

- linha anterior;
- linha atual;
- próxima linha;
- preenchimento amarelo progressivo;
- suporte a timestamps por palavra;
- fallback para LRC com timestamp somente por linha;
- barra clicável/arrastável de progresso;
- `<< 5s`;
- `Pausar` / `Continuar`;
- `Stop`;
- `5s >>`;
- volume com percentual;
- `Espaço` para play/pause;
- `Esc` para fechar.

Quando existe sincronização por palavra, palavras já cantadas ficam preenchidas em amarelo, palavras futuras permanecem brancas e a palavra atual recebe preenchimento progressivo de acordo com seu intervalo real.

Quando existe somente timestamp por linha, o preenchimento é distribuído do início da linha atual até o início da próxima, preservando compatibilidade com LRCs antigos.

## Arquivos importantes para futuras alterações

```text
app/main.py
```

Interface, biblioteca, editor de letra, tela de karaokê, importação em lote e fluxo de transcrição.

```text
app/player.py
```

Player de áudio, posição, play/pause/stop e volume padrão de 50%.

```text
app/database.py
```

SQLite, cadastro/listagem/exclusão de músicas e caminhos compartilhados.

```text
app/lrc.py
```

Parser e gerador de LRC tradicional e LRC estendido por palavra.

```text
app/transcription.py
```

Integração com `faster-whisper` e geração automática do LRC sincronizado.

## Contexto para um próximo chat

Ao continuar este projeto em outro chat, considere como requisitos já estabelecidos:

- preservar `..\data\karaoke.db` e `..\music\` compartilhados;
- não criar banco novo dentro da pasta da versão;
- volume de cada música deve começar em 50%;
- sempre mostrar o percentual do volume;
- manter inclusão individual de música;
- manter importação em lote de uma pasta/biblioteca;
- manter o botão **Baixar do YouTube**, salvando o áudio em `..\music\` e cadastrando-o automaticamente;
- manter o download em segundo plano para não travar a interface;
- detectar automaticamente `Artista - Título` pelo nome do arquivo;
- importar `.lrc` de mesmo nome automaticamente;
- manter geração automática de letra via Whisper;
- manter edição manual como fallback para corrigir a transcrição;
- manter compatibilidade com LRC tradicional e LRC estendido por palavra;
- preservar o efeito de preenchimento amarelo da letra durante a reprodução;
- não depender de serviços pagos ou APIs externas para o funcionamento básico.
