import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from .genius_lyrics import fetch_genius_lyrics, genius_corrected_lines
from .lrc import format_lrc
from .online_lyrics import LyricsNotFound, audio_duration_seconds, find_synced_lyrics, shift_to_vocals
from .stem_separation import separate_vocals_and_instrumental
from .transcription import write_aligned_lyrics


class StemSeparationWorker(QObject):
    progress = Signal(int, str)
    finished = Signal(str, str)
    failed = Signal(str)

    def __init__(self, source_audio_path, music_dir):
        super().__init__()
        self.source_audio_path = str(source_audio_path)
        self.music_dir = Path(music_dir)

    def run(self):
        work_dir = None
        try:
            source = Path(self.source_audio_path).resolve()
            if not source.exists():
                raise RuntimeError(f'Arquivo original não encontrado: {source}')

            self.music_dir.mkdir(parents=True, exist_ok=True)
            work_dir = Path(tempfile.mkdtemp(prefix='karaoke_demucs_'))

            self.progress.emit(10, 'Separando voz e instrumental...')
            vocals_path, instrumental_path = separate_vocals_and_instrumental(
                source,
                work_dir,
                self.music_dir,
            )
            self.progress.emit(100, 'Separação concluída. Agora cole a letra correta.')
            self.finished.emit(
                str(Path(instrumental_path).resolve()),
                str(Path(vocals_path).resolve()),
            )
        except Exception as error:
            self.failed.emit(str(error))
        finally:
            if work_dir:
                shutil.rmtree(work_dir, ignore_errors=True)


class LyricsAlignmentWorker(QObject):
    progress = Signal(int, str)
    finished = Signal(str, str, int)
    failed = Signal(str)

    def __init__(self, vocals_path, lyrics_text, output_path, model_size=None):
        super().__init__()
        self.vocals_path = str(vocals_path)
        self.lyrics_text = lyrics_text
        self.output_path = str(output_path)
        self.model_size = model_size

    def run(self):
        try:
            self.progress.emit(5, 'Carregando o modelo de reconhecimento de voz...')

            def on_progress(fraction):
                self.progress.emit(
                    10 + int(fraction * 85),
                    'Analisando o vocal para localizar os tempos...',
                )

            output, language, confidence = write_aligned_lyrics(
                self.vocals_path,
                self.lyrics_text,
                self.output_path,
                model_size=self.model_size,
                progress=on_progress,
            )
            self.progress.emit(100, 'Letra sincronizada.')
            self.finished.emit(str(Path(output).resolve()), language or '', int(confidence))
        except Exception as error:
            self.failed.emit(str(error))


class OnlineLyricsWorker(QObject):
    """Letra sincronizada da internet: tempos do LRCLIB/NetEase, texto conferido no
    Genius e, se houver vocal separado, atraso ajustado para esta versão da música."""

    finished = Signal(str, str, int, bool, str)   # LRC, fonte, ajuste (ms), conferido no vocal, nota do texto
    failed = Signal(str)

    def __init__(self, artist, title, audio_path, vocals_path=None):
        super().__init__()
        self.artist = artist
        self.title = title
        self.audio_path = audio_path
        self.vocals_path = vocals_path

    def run(self):
        try:
            duration = audio_duration_seconds(self.audio_path) if self.audio_path else None
            lines, source = find_synced_lyrics(self.artist, self.title, duration)
            lines, text_note = genius_corrected_lines(lines, self.artist, self.title)
            offset_ms = shift_to_vocals(lines, self.vocals_path) if self.vocals_path else 0
            self.finished.emit(format_lrc(lines), source, int(offset_ms), bool(self.vocals_path), text_note)
        except LyricsNotFound as error:
            self.failed.emit(str(error))
        except Exception as error:
            self.failed.emit(f'Não foi possível buscar a letra:\n{error}')


class GeniusLyricsWorker(QObject):
    """Texto da letra no Genius (sem tempos), uma linha por verso."""

    finished = Signal(list)   # linhas
    failed = Signal(str)

    def __init__(self, artist, title):
        super().__init__()
        self.artist = artist
        self.title = title

    def run(self):
        try:
            self.finished.emit(fetch_genius_lyrics(self.artist, self.title))
        except LyricsNotFound as error:
            self.failed.emit(str(error))
        except Exception as error:
            self.failed.emit(f'Não foi possível consultar o Genius:\n{error}')
