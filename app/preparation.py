import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, Signal

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

    def __init__(self, vocals_path, lyrics_text, output_path, model_size='small'):
        super().__init__()
        self.vocals_path = str(vocals_path)
        self.lyrics_text = lyrics_text
        self.output_path = str(output_path)
        self.model_size = model_size

    def run(self):
        try:
            self.progress.emit(10, 'Analisando o vocal para localizar os tempos...')
            output, language, confidence = write_aligned_lyrics(
                self.vocals_path,
                self.lyrics_text,
                self.output_path,
                model_size=self.model_size,
            )
            self.progress.emit(100, 'Letra sincronizada.')
            self.finished.emit(str(Path(output).resolve()), language or '', int(confidence))
        except Exception as error:
            self.failed.emit(str(error))
