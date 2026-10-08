import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from .stem_separation import separate_vocals_and_instrumental
from .transcription import write_transcription


class KaraokePreparationWorker(QObject):
    progress = Signal(int, str)
    finished = Signal(str, str, str, str)
    failed = Signal(str)

    def __init__(self, source_audio_path, music_dir, model_size='small'):
        super().__init__()
        self.source_audio_path = str(source_audio_path)
        self.music_dir = Path(music_dir)
        self.model_size = model_size

    def run(self):
        work_dir = None
        try:
            source = Path(self.source_audio_path).resolve()
            if not source.exists():
                raise RuntimeError(f'Arquivo original não encontrado: {source}')

            self.music_dir.mkdir(parents=True, exist_ok=True)
            work_dir = Path(tempfile.mkdtemp(prefix='karaoke_demucs_'))

            self.progress.emit(8, 'Preparando separação de voz e instrumental...')
            vocals_path, instrumental_path = separate_vocals_and_instrumental(
                source,
                work_dir,
                self.music_dir,
            )

            self.progress.emit(62, 'Voz separada. Gerando letra e sincronização...')
            lyrics_path = str(Path(instrumental_path).with_suffix('.lrc'))
            output, language = write_transcription(
                vocals_path,
                lyrics_path,
                model_size=self.model_size,
            )

            self.progress.emit(95, 'Finalizando arquivos do karaokê...')
            self.progress.emit(100, 'Karaokê preparado.')
            self.finished.emit(
                str(Path(instrumental_path).resolve()),
                str(Path(vocals_path).resolve()),
                str(Path(output).resolve()),
                language or '',
            )
        except Exception as error:
            self.failed.emit(str(error))
        finally:
            if work_dir:
                shutil.rmtree(work_dir, ignore_errors=True)
