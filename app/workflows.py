"""Fluxos de preparação de uma música que envolvem várias etapas e telas."""
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from .database import LYRIC_DIR, MUSIC_DIR, mark_song_synced, update_song_stems
from .preparation import GeniusLyricsWorker, LyricsAlignmentWorker, StemSeparationWorker
from .tasks import run_task


class ManualLyricsDialog(QDialog):
    def __init__(self, song, initial_text='', parent=None):
        super().__init__(parent)
        self.song = song
        self.setWindowTitle('Cole a letra correta')
        self.resize(760, 620)

        layout = QVBoxLayout(self)
        title = QLabel(f"Letra de: {song['title']}")
        title.setStyleSheet('font-size: 18px; font-weight: bold;')
        layout.addWidget(title)

        info = QLabel(
            'Cole abaixo a letra correta da música. Esta etapa é obrigatória: o sistema não vai mais inventar '
            'a letra ouvindo o cantor. Ele usará o vocal apenas para localizar os tempos.\n\n'
            'Dica: use uma linha por verso e remova textos que não são cantados, como [Refrão], [Verso 1] e comentários. '
            'O botão "Buscar letra no Genius" já traz a letra nesse formato.'
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        genius_row = QHBoxLayout()
        self.genius_button = QPushButton('Buscar letra no Genius')
        self.genius_button.clicked.connect(self.fill_from_genius)
        genius_row.addWidget(self.genius_button)
        genius_row.addStretch()
        layout.addLayout(genius_row)

        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText('Cole aqui a letra completa...')
        self.text_edit.setPlainText(initial_text or '')
        self.text_edit.setStyleSheet('QTextEdit { font-size: 17px; }')
        layout.addWidget(self.text_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Sincronizar esta letra')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self):
        if not self.text_edit.toPlainText().strip():
            QMessageBox.warning(self, 'Letra obrigatória', 'Cole a letra da música antes de continuar.')
            return
        super().accept()

    def lyrics_text(self):
        return self.text_edit.toPlainText().strip()

    def fill_from_genius(self):
        self.genius_button.setEnabled(False)
        run_task(
            self,
            GeniusLyricsWorker(self.song.get('artist') or '', self.song['title']),
            self._on_genius_lyrics,
            self._on_genius_failed,
            title='Genius',
            text='Buscando a letra no Genius...',
            busy=True,
        )

    def _on_genius_lyrics(self, lines):
        self.genius_button.setEnabled(True)
        if self.text_edit.toPlainText().strip():
            answer = QMessageBox.question(self, 'Genius', 'Substituir o texto atual pela letra do Genius?')
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.text_edit.setPlainText('\n'.join(lines))

    def _on_genius_failed(self, message):
        self.genius_button.setEnabled(True)
        QMessageBox.warning(self, 'Genius', message)


def lyrics_path_for(audio_path):
    """Onde fica o LRC de um instrumental: music/lyric/<nome do instrumental>.lrc."""
    return LYRIC_DIR / (Path(audio_path).stem + ".lrc")


def alignment_quality(confidence):
    if confidence >= 70:
        return f"A correspondência com o vocal ficou alta ({confidence}%)."
    return (
        f"A correspondência com o vocal ficou parcial ({confidence}%); "
        "vale revisar os tempos no Editor de letra."
    )


def online_offset_message(offset_ms, offset_checked):
    """Explica o ajuste de atraso aplicado à letra baixada da internet."""
    if offset_ms:
        direction = "atrasada" if offset_ms > 0 else "adiantada"
        return (
            f"A letra foi {direction} em {abs(offset_ms) / 1000:.1f} s "
            "para coincidir com o vocal desta versão da música."
        )
    if offset_checked:
        return "Os tempos já coincidem com o vocal desta versão; nenhum ajuste foi necessário."
    return (
        "Como não há vocal separado, não foi possível conferir "
        "se os tempos batem com esta versão."
    )


class PreparationFlow(QObject):
    """Separar voz/instrumental → pedir a letra correta → sincronizá-la com o vocal.

    Usado pelo Editor de letra e pelo "Baixar música + separar". O dicionário
    `song` é atualizado conforme o processo avança; quem usa o fluxo reage aos
    sinais para atualizar a própria tela.
    """

    stems_ready = Signal(str, str)        # instrumental, vocal
    separation_failed = Signal(str)
    lyrics_synced = Signal(str, int)      # caminho do LRC, correspondência (%)
    lyrics_skipped = Signal()             # o usuário fechou a janela da letra
    sync_failed = Signal(str)

    def __init__(self, song, widget, lyrics_hint=lambda: ""):
        super().__init__(widget)
        self.song = song
        self.widget = widget
        self.lyrics_hint = lyrics_hint    # texto inicial da janela "Cole a letra"

    def separate(self):
        source = self.song.get("source_audio_path") or self.song.get("audio_path")
        run_task(
            self.widget,
            StemSeparationWorker(source, MUSIC_DIR),
            self._on_stems_ready,
            self.separation_failed.emit,
            title="Separando voz e instrumental",
            text="Separando voz e instrumental...",
        )

    def _on_stems_ready(self, instrumental_path, vocals_path):
        source = self.song.get("source_audio_path") or self.song.get("audio_path")
        update_song_stems(self.song["id"], instrumental_path, vocals_path, source)
        self.song.update(
            source_audio_path=source,
            audio_path=instrumental_path,
            vocals_path=vocals_path,
            prepared_automatically=0,
        )
        self.stems_ready.emit(instrumental_path, vocals_path)
        self.ask_lyrics_and_sync()

    def ask_lyrics_and_sync(self):
        dialog = ManualLyricsDialog(self.song, self.lyrics_hint(), self.widget)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self.lyrics_skipped.emit()
            return
        self.sync(dialog.lyrics_text())

    def sync(self, lyrics_text):
        vocals_path = self.song.get("vocals_path")
        if not vocals_path or not Path(vocals_path).exists():
            self.sync_failed.emit("O arquivo de vocal separado não foi localizado.")
            return
        output_path = self.song.get("lyrics_path") or str(lyrics_path_for(self.song["audio_path"]))
        run_task(
            self.widget,
            LyricsAlignmentWorker(vocals_path, lyrics_text, output_path),
            self._on_synced,
            self.sync_failed.emit,
            title="Sincronizar letra",
            text="Localizando os tempos da letra...",
        )

    def _on_synced(self, lyrics_path, _language, confidence):
        mark_song_synced(self.song["id"], lyrics_path)
        self.song.update(lyrics_path=lyrics_path, prepared_automatically=1)
        self.lyrics_synced.emit(lyrics_path, confidence)
