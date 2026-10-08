import html
import os
import re
import shutil
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QSignalBlocker, QTimer, QRectF, QObject, QThread, Signal
from PySide6.QtGui import QPainter, QColor, QFont, QFontMetrics
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QFileDialog,
    QMessageBox,
    QSlider,
    QStyle,
    QDialog,
    QTextEdit,
    QDialogButtonBox,
    QSplitter,
    QInputDialog,
    QProgressDialog,
)

from .database import (
    init_db,
    add_song,
    update_lyrics_path,
    update_song_preparation,
    update_song_stems,
    mark_song_synced,
    list_songs,
    delete_song,
    MUSIC_DIR,
    get_song_by_audio_path,
)
from .player import Player
from .lrc import (
    parse_lrc,
    parse_editor_lines,
    format_lrc,
    active_index,
)
from .transcription import write_transcription
from .youtube_download import YouTubeDownloadWorker
from .preparation import StemSeparationWorker, LyricsAlignmentWorker



class KaraokeTextWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = "♪"
        self._fill_ratio = 0.0
        self._base_font_size = 43
        self.setMinimumHeight(78)

    def set_karaoke_text(self, text, fill_ratio=0.0):
        self._text = text or ""
        self._fill_ratio = max(0.0, min(1.0, float(fill_ratio)))
        self.update()

    def _fitted_font(self):
        size = self._base_font_size
        available = max(1, self.width() - 20)
        while size > 24:
            font = QFont(self.font())
            font.setPointSize(size)
            font.setBold(True)
            if QFontMetrics(font).horizontalAdvance(self._text) <= available:
                return font
            size -= 1
        font = QFont(self.font())
        font.setPointSize(size)
        font.setBold(True)
        return font

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        font = self._fitted_font()
        painter.setFont(font)
        metrics = QFontMetrics(font)

        text_width = metrics.horizontalAdvance(self._text)
        x = (self.width() - text_width) / 2.0
        baseline = (self.height() - metrics.height()) / 2.0 + metrics.ascent()

        painter.setPen(QColor("#ffffff"))
        painter.drawText(int(x), int(baseline), self._text)

        fill_width = text_width * self._fill_ratio
        if fill_width > 0:
            painter.save()
            painter.setClipRect(QRectF(x, 0, fill_width, self.height()))
            painter.setPen(QColor("#ffd700"))
            painter.drawText(int(x), int(baseline), self._text)
            painter.restore()


class SeekSlider(QSlider):
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.orientation() == Qt.Orientation.Horizontal:
                position = int(event.position().x())
                span = max(1, self.width())
            else:
                position = int(self.height() - event.position().y())
                span = max(1, self.height())

            value = QStyle.sliderValueFromPosition(
                self.minimum(),
                self.maximum(),
                position,
                span,
            )
            self.setValue(value)
            self.sliderMoved.emit(value)
            event.accept()
            return

        super().mousePressEvent(event)


def format_ms(milliseconds):
    milliseconds = max(0, int(milliseconds))
    total_seconds = milliseconds // 1000
    return f"{total_seconds // 60:02d}:{total_seconds % 60:02d}"


def unique_destination(directory, filename):
    name, extension = os.path.splitext(filename)
    destination = os.path.join(directory, filename)
    counter = 1

    while os.path.exists(destination):
        destination = os.path.join(
            directory,
            f"{name}_{counter}{extension}",
        )
        counter += 1

    return destination


AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".wma"}


def infer_artist_title(path):
    stem = Path(path).stem.strip()
    if " - " in stem:
        artist, title = stem.split(" - ", 1)
        if artist.strip() and title.strip():
            return artist.strip(), title.strip()
    return "", stem


def matching_lrc_path(audio_path):
    candidate = Path(audio_path).with_suffix(".lrc")
    return candidate if candidate.exists() else None


class TranscriptionWorker(QObject):
    finished = Signal(str, str)
    failed = Signal(str)

    def __init__(self, audio_path, output_path):
        super().__init__()
        self.audio_path = audio_path
        self.output_path = output_path

    def run(self):
        try:
            output, language = write_transcription(
                self.audio_path,
                self.output_path,
                model_size="small",
            )
            self.finished.emit(str(output), language or "")
        except Exception as error:
            self.failed.emit(str(error))


class ManualLyricsDialog(QDialog):
    def __init__(self, song_title, initial_text='', parent=None):
        super().__init__(parent)
        self.setWindowTitle('Cole a letra correta')
        self.resize(760, 620)

        layout = QVBoxLayout(self)
        title = QLabel(f"Letra de: {song_title}")
        title.setStyleSheet('font-size: 18px; font-weight: bold;')
        layout.addWidget(title)

        info = QLabel(
            'Cole abaixo a letra correta da música. Esta etapa é obrigatória: o sistema não vai mais inventar '
            'a letra ouvindo o cantor. Ele usará o vocal apenas para localizar os tempos.\n\n'
            'Dica: use uma linha por verso e remova textos que não são cantados, como [Refrão], [Verso 1] e comentários.'
        )
        info.setWordWrap(True)
        layout.addWidget(info)

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


class WordSyncDialog(QDialog):
    def __init__(self, line_data, player, parent=None):
        super().__init__(parent)
        self.player = player
        self.original = line_data
        self.tokens = self._tokenize(line_data.get("text", ""))
        self.times = [None] * len(self.tokens)
        self.current_index = 0

        existing = line_data.get("words") or []
        if existing and len(existing) == len(self.tokens):
            self.times = [item["time"] for item in existing]
            first_unmarked = next((i for i, value in enumerate(self.times) if value is None), len(self.times))
            self.current_index = min(first_unmarked, max(0, len(self.tokens) - 1))

        self.setWindowTitle("Sincronização por palavra")
        self.resize(760, 560)

        layout = QVBoxLayout(self)
        info = QLabel(
            "Dê Play na música e marque cada palavra no instante em que o cantor começa a cantá-la. "
            "O intervalo até a próxima palavra define a duração real do preenchimento amarelo."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self.clock_label = QLabel("Tempo atual: 00:00.00")
        self.clock_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.clock_label)

        self.word_list = QListWidget()
        self.word_list.setStyleSheet("QListWidget { font-size: 20px; } QListWidget::item { padding: 7px; }")
        layout.addWidget(self.word_list)

        playback = QHBoxLayout()
        back = QPushButton("-5 s")
        back.clicked.connect(lambda: self.seek_relative(-5000))
        playback.addWidget(back)

        self.play_button = QPushButton("Play/Pause (Espaço)")
        self.play_button.clicked.connect(self.player.toggle)
        playback.addWidget(self.play_button)

        forward = QPushButton("+5 s")
        forward.clicked.connect(lambda: self.seek_relative(5000))
        playback.addWidget(forward)
        layout.addLayout(playback)

        actions = QHBoxLayout()
        previous = QPushButton("Palavra anterior")
        previous.clicked.connect(self.previous_word)
        actions.addWidget(previous)

        self.mark_button = QPushButton("Marcar palavra (F8)")
        self.mark_button.setShortcut("F8")
        self.mark_button.clicked.connect(self.mark_current_word)
        actions.addWidget(self.mark_button)

        undo = QPushButton("Limpar marca atual")
        undo.clicked.connect(self.clear_current_mark)
        actions.addWidget(undo)

        next_button = QPushButton("Próxima palavra")
        next_button.clicked.connect(self.next_word)
        actions.addWidget(next_button)
        layout.addLayout(actions)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.timer = QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self.refresh_clock)
        self.timer.start()
        self.finished.connect(self.timer.stop)

        self.rebuild_list()
        self.refresh_clock()

    @staticmethod
    def _tokenize(text):
        # Cada item inclui os espaços que vêm depois da palavra, preservando visualmente a frase.
        return re.findall(r"\S+\s*", text) if text.strip() else []

    def refresh_clock(self):
        milliseconds = self.player.position()
        minutes = milliseconds // 60000
        seconds = (milliseconds // 1000) % 60
        centiseconds = (milliseconds % 1000) // 10
        self.clock_label.setText(f"Tempo atual: {minutes:02d}:{seconds:02d}.{centiseconds:02d}")

    def rebuild_list(self):
        self.word_list.clear()
        for index, token in enumerate(self.tokens):
            value = self.times[index]
            if value is None:
                stamp = "--:--.--"
            else:
                stamp = f"{value // 60000:02d}:{(value // 1000) % 60:02d}.{(value % 1000) // 10:02d}"
            item = QListWidgetItem(f"{index + 1:02d}   {stamp}   {token.rstrip()}")
            self.word_list.addItem(item)

        if self.tokens:
            self.current_index = max(0, min(self.current_index, len(self.tokens) - 1))
            self.word_list.setCurrentRow(self.current_index)
            self.word_list.scrollToItem(self.word_list.currentItem())

    def mark_current_word(self):
        if not self.tokens:
            return
        self.current_index = self.word_list.currentRow() if self.word_list.currentRow() >= 0 else self.current_index
        self.times[self.current_index] = self.player.position()
        if self.current_index < len(self.tokens) - 1:
            self.current_index += 1
        self.rebuild_list()

    def clear_current_mark(self):
        if not self.tokens:
            return
        row = self.word_list.currentRow()
        if row >= 0:
            self.current_index = row
        self.times[self.current_index] = None
        self.rebuild_list()

    def previous_word(self):
        if self.tokens:
            self.current_index = max(0, self.word_list.currentRow() - 1)
            self.rebuild_list()

    def next_word(self):
        if self.tokens:
            row = self.word_list.currentRow()
            self.current_index = min(len(self.tokens) - 1, max(0, row) + 1)
            self.rebuild_list()

    def seek_relative(self, delta_ms):
        duration = self.player.media.duration()
        target = self.player.position() + delta_ms
        if duration > 0:
            target = min(target, duration)
        self.player.set_position(max(0, target))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Space:
            self.player.toggle()
            return
        super().keyPressEvent(event)

    def result_line(self):
        if not self.tokens or any(value is None for value in self.times):
            return None
        words = [
            {"time": self.times[i], "text": self.tokens[i]}
            for i in range(len(self.tokens))
        ]
        return {
            "time": self.original.get("time") if self.original.get("time") is not None else self.times[0],
            "text": self.original.get("text", ""),
            "words": words,
        }


class LyricsEditor(QDialog):
    def __init__(self, song, player, parent=None):
        super().__init__(parent)

        self.song = song
        self.player = player
        self.position = self.player.position()

        self.setWindowTitle(
            f"Editor de letra — {song['title']}"
        )
        self.resize(850, 650)

        layout = QVBoxLayout(self)

        info = QLabel(
            "Uma linha por vez. Coloque o cursor na linha desejada "
            "e clique em 'Sincronizar linha atual' no momento correto."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText(
            "Cole ou digite a letra aqui, uma linha por linha..."
        )
        self.text_edit.setStyleSheet(
            "QTextEdit { font-size: 17px; }"
        )
        layout.addWidget(self.text_edit)

        controls = QHBoxLayout()

        self.sync_button = QPushButton(
            "Sincronizar linha atual"
        )
        self.sync_button.clicked.connect(self.sync_current_line)
        controls.addWidget(self.sync_button)

        self.word_sync_button = QPushButton("Sincronizar palavras da linha")
        self.word_sync_button.clicked.connect(self.sync_words_current_line)
        controls.addWidget(self.word_sync_button)

        self.auto_sync_button = QPushButton("Sincronizar letra colada")
        self.auto_sync_button.setToolTip(
            "Usa o vocal separado somente para localizar os tempos. O texto da letra é preservado."
        )
        self.auto_sync_button.clicked.connect(self.synchronize_pasted_lyrics)
        controls.addWidget(self.auto_sync_button)

        self.prepare_button = QPushButton("Preparar voz/instrumental")
        self.prepare_button.setToolTip(
            "Separa a música original em vocal e instrumental. Depois a letra correta será obrigatória."
        )
        self.prepare_button.clicked.connect(self.prepare_karaoke_automatically)
        controls.addWidget(self.prepare_button)

        self.clear_button = QPushButton("Remover tempos")
        self.clear_button.clicked.connect(self.remove_times)
        controls.addWidget(self.clear_button)

        controls.addStretch()

        self.clock_label = QLabel("Tempo atual: 00:00.00")
        controls.addWidget(self.clock_label)

        layout.addLayout(controls)

        playback_controls = QHBoxLayout()

        self.back_button = QPushButton("-5 s")
        self.back_button.clicked.connect(lambda: self.seek_relative(-5000))
        playback_controls.addWidget(self.back_button)

        self.play_pause_button = QPushButton("Play")
        self.play_pause_button.clicked.connect(self.player.toggle)
        playback_controls.addWidget(self.play_pause_button)

        self.forward_button = QPushButton("+5 s")
        self.forward_button.clicked.connect(lambda: self.seek_relative(5000))
        playback_controls.addWidget(self.forward_button)

        playback_controls.addStretch()
        layout.addLayout(playback_controls)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.player.position_changed.connect(
            self.update_clock
        )
        self.player.state_changed.connect(
            self.update_play_button
        )

        self.clock_timer = QTimer(self)
        self.clock_timer.setInterval(50)
        self.clock_timer.timeout.connect(self.refresh_player_position)
        self.finished.connect(self.clock_timer.stop)
        self.clock_timer.start()

        self.load_existing()
        self.update_clock(self.player.position())
        self.update_play_button(
            "playing"
            if self.player.media.playbackState().name == "PlayingState"
            else "paused"
        )

    def load_existing(self):
        path = self.song["lyrics_path"]

        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as file:
                    self.text_edit.setPlainText(file.read())
            except OSError as error:
                QMessageBox.warning(
                    self,
                    "Erro",
                    f"Não foi possível ler a letra:\n{error}",
                )

    def update_clock(self, milliseconds):
        self.position = milliseconds

        minutes = milliseconds // 60000
        seconds = (milliseconds // 1000) % 60
        centiseconds = (milliseconds % 1000) // 10

        self.clock_label.setText(
            f"Tempo atual: {minutes:02d}:{seconds:02d}.{centiseconds:02d}"
        )


    def refresh_player_position(self):
        current_position = self.player.position()

        if current_position != self.position:
            self.update_clock(current_position)

    def seek_relative(self, delta_ms):
        duration = self.player.media.duration()
        new_position = self.player.position() + delta_ms

        if duration > 0:
            new_position = min(new_position, duration)

        self.player.set_position(max(0, new_position))
        self.update_clock(self.player.position())

    def update_play_button(self, state):
        if state == "playing":
            self.play_pause_button.setText("Pause")
        else:
            self.play_pause_button.setText("Play")

    def sync_current_line(self):
        cursor = self.text_edit.textCursor()
        line_number = cursor.blockNumber()

        rows = parse_editor_lines(
            self.text_edit.toPlainText()
        )

        if not rows:
            return

        if line_number < 0 or line_number >= len(rows):
            return

        # Mantém os timestamps das demais linhas.
        # Somente a linha selecionada recebe o timestamp atual.
        rows[line_number]["time"] = self.position

        # Reescreve o texto, mas tenta preservar a posição lógica
        # da linha selecionada.
        new_text = format_lrc(rows)

        self.text_edit.setPlainText(new_text)

        cursor = self.text_edit.textCursor()
        cursor.movePosition(
            cursor.MoveOperation.Start
        )

        for _ in range(line_number):
            cursor.movePosition(
                cursor.MoveOperation.Down
            )

        self.text_edit.setTextCursor(cursor)
        self.text_edit.ensureCursorVisible()

    def sync_words_current_line(self):
        cursor = self.text_edit.textCursor()
        line_number = cursor.blockNumber()
        rows = parse_editor_lines(self.text_edit.toPlainText())

        if not rows or line_number < 0 or line_number >= len(rows):
            return

        if not rows[line_number].get("text", "").strip():
            QMessageBox.information(self, "Sincronização", "A linha selecionada não possui palavras.")
            return

        dialog = WordSyncDialog(rows[line_number], self.player, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        synced = dialog.result_line()
        if synced is None:
            QMessageBox.warning(
                self,
                "Sincronização incompleta",
                "Marque todas as palavras antes de salvar a sincronização da linha.",
            )
            return

        rows[line_number] = synced
        self.text_edit.setPlainText(format_lrc(rows))

        cursor = self.text_edit.textCursor()
        cursor.movePosition(cursor.MoveOperation.Start)
        for _ in range(line_number):
            cursor.movePosition(cursor.MoveOperation.Down)
        self.text_edit.setTextCursor(cursor)
        self.text_edit.ensureCursorVisible()

    def _plain_lyrics_from_editor(self):
        rows = parse_editor_lines(self.text_edit.toPlainText())
        return "\n".join(row.get("text", "") for row in rows if row.get("text", "").strip())

    def synchronize_pasted_lyrics(self):
        vocals_path = self.song.get("vocals_path")
        if not vocals_path or not os.path.exists(vocals_path):
            QMessageBox.information(
                self,
                "Prepare a música primeiro",
                "Primeiro separe a música em vocal e instrumental usando 'Preparar voz/instrumental'.",
            )
            return

        initial = self._plain_lyrics_from_editor()
        dialog = ManualLyricsDialog(self.song["title"], initial, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.start_lyrics_alignment(dialog.lyrics_text())

    def start_lyrics_alignment(self, lyrics_text):
        vocals_path = self.song.get("vocals_path")
        if not vocals_path or not os.path.exists(vocals_path):
            QMessageBox.warning(self, "Vocal não encontrado", "O arquivo de vocal separado não foi localizado.")
            return

        output_path = self.song.get("lyrics_path") or (
            os.path.splitext(self.song["audio_path"])[0] + ".lrc"
        )

        self.auto_sync_button.setEnabled(False)
        self.auto_sync_button.setText("Sincronizando...")
        self.lyrics_progress = QProgressDialog(
            "Localizando os tempos da letra...", "Ocultar", 0, 100, self
        )
        self.lyrics_progress.setWindowTitle("Sincronizar letra")
        self.lyrics_progress.setAutoClose(False)
        self.lyrics_progress.setAutoReset(False)
        self.lyrics_progress.setMinimumDuration(0)
        self.lyrics_progress.show()

        self.alignment_thread = QThread(self)
        self.alignment_worker = LyricsAlignmentWorker(
            vocals_path, lyrics_text, output_path, model_size="small"
        )
        self.alignment_worker.moveToThread(self.alignment_thread)
        self.alignment_thread.started.connect(self.alignment_worker.run)
        self.alignment_worker.progress.connect(self.on_alignment_progress)
        self.alignment_worker.finished.connect(self.on_alignment_finished)
        self.alignment_worker.failed.connect(self.on_alignment_failed)
        self.alignment_worker.finished.connect(self.alignment_thread.quit)
        self.alignment_worker.failed.connect(self.alignment_thread.quit)
        self.alignment_thread.finished.connect(self.alignment_worker.deleteLater)
        self.alignment_thread.finished.connect(self.alignment_thread.deleteLater)
        self.alignment_thread.start()

    def on_alignment_progress(self, value, message):
        if getattr(self, "lyrics_progress", None):
            self.lyrics_progress.setValue(value)
            self.lyrics_progress.setLabelText(message)

    def on_alignment_finished(self, path, language, confidence):
        mark_song_synced(self.song["id"], path)
        self.song["lyrics_path"] = path
        self.song["prepared_automatically"] = 1
        try:
            self.text_edit.setPlainText(Path(path).read_text(encoding="utf-8"))
        except OSError as error:
            QMessageBox.warning(self, "Letra sincronizada", f"O LRC foi criado, mas não pôde ser aberto:\n{error}")
        else:
            quality = (
                "A correspondência com o vocal ficou alta." if confidence >= 70
                else "A correspondência com o vocal ficou parcial; revise os tempos no editor."
            )
            QMessageBox.information(
                self,
                "Letra sincronizada",
                f"A letra que você colou foi preservada e sincronizada.\n\n{quality}\n"
                f"Referência de correspondência: {confidence}%."
            )
        if getattr(self, "lyrics_progress", None):
            self.lyrics_progress.setValue(100)
            self.lyrics_progress.close()
        self.auto_sync_button.setEnabled(True)
        self.auto_sync_button.setText("Sincronizar letra colada")

    def on_alignment_failed(self, message):
        if getattr(self, "lyrics_progress", None):
            self.lyrics_progress.close()
        self.auto_sync_button.setEnabled(True)
        self.auto_sync_button.setText("Sincronizar letra colada")
        QMessageBox.critical(self, "Erro ao sincronizar letra", message)

    def prepare_karaoke_automatically(self):
        existing_vocals = self.song.get("vocals_path")
        if existing_vocals and os.path.exists(existing_vocals):
            dialog = ManualLyricsDialog(self.song["title"], self._plain_lyrics_from_editor(), self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.start_lyrics_alignment(dialog.lyrics_text())
            return

        source_path = self.song.get("source_audio_path") or self.song.get("audio_path")
        if not source_path or not os.path.exists(source_path):
            QMessageBox.warning(self, "Arquivo não encontrado", "A música original com vocal não foi localizada.")
            return

        answer = QMessageBox.question(
            self,
            "Preparar voz e instrumental",
            "O sistema vai separar a música em vocal e instrumental. Depois disso você precisará colar a letra correta para continuar. Deseja iniciar?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self.prepare_button.setEnabled(False)
        self.prepare_button.setText("Separando...")
        self.preparation_progress = QProgressDialog(
            "Separando voz e instrumental...", "Ocultar", 0, 100, self
        )
        self.preparation_progress.setWindowTitle("Preparar voz/instrumental")
        self.preparation_progress.setAutoClose(False)
        self.preparation_progress.setAutoReset(False)
        self.preparation_progress.setMinimumDuration(0)
        self.preparation_progress.show()

        self.preparation_thread = QThread(self)
        self.preparation_worker = StemSeparationWorker(source_path, MUSIC_DIR)
        self.preparation_worker.moveToThread(self.preparation_thread)
        self.preparation_thread.started.connect(self.preparation_worker.run)
        self.preparation_worker.progress.connect(self.on_preparation_progress)
        self.preparation_worker.finished.connect(self.on_preparation_finished)
        self.preparation_worker.failed.connect(self.on_preparation_failed)
        self.preparation_worker.finished.connect(self.preparation_thread.quit)
        self.preparation_worker.failed.connect(self.preparation_thread.quit)
        self.preparation_thread.finished.connect(self.preparation_worker.deleteLater)
        self.preparation_thread.finished.connect(self.preparation_thread.deleteLater)
        self.preparation_thread.start()

    def on_preparation_progress(self, value, message):
        if getattr(self, "preparation_progress", None):
            self.preparation_progress.setValue(value)
            self.preparation_progress.setLabelText(message)

    def on_preparation_finished(self, instrumental_path, vocals_path):
        source_path = self.song.get("source_audio_path") or self.song.get("audio_path")
        try:
            update_song_stems(self.song["id"], instrumental_path, vocals_path, source_path)
            self.song["source_audio_path"] = source_path
            self.song["audio_path"] = instrumental_path
            self.song["vocals_path"] = vocals_path
            self.song["prepared_automatically"] = 0
            self.player.load(instrumental_path)
        except Exception as error:
            QMessageBox.warning(self, "Separação concluída", f"Os arquivos foram gerados, mas houve erro ao atualizar a biblioteca:\n{error}")
            return
        finally:
            if getattr(self, "preparation_progress", None):
                self.preparation_progress.setValue(100)
                self.preparation_progress.close()
            self.prepare_button.setEnabled(True)
            self.prepare_button.setText("Preparar voz/instrumental")

        dialog = ManualLyricsDialog(self.song["title"], self._plain_lyrics_from_editor(), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.start_lyrics_alignment(dialog.lyrics_text())
        else:
            QMessageBox.information(
                self,
                "Separação concluída",
                "Vocal e instrumental foram gerados. A letra continua pendente e pode ser colada depois pelo botão 'Sincronizar letra colada'.",
            )

    def on_preparation_failed(self, message):
        if getattr(self, "preparation_progress", None):
            self.preparation_progress.close()
        self.prepare_button.setEnabled(True)
        self.prepare_button.setText("Preparar voz/instrumental")
        QMessageBox.critical(self, "Erro ao separar música", message)

    def remove_times(self):
        rows = parse_editor_lines(
            self.text_edit.toPlainText()
        )

        for row in rows:
            row["time"] = None
            row["words"] = []

        self.text_edit.setPlainText(
            format_lrc(rows)
        )

    def save(self):
        path = self.song["lyrics_path"]

        if not path:
            path = (
                os.path.splitext(
                    self.song["audio_path"]
                )[0]
                + ".lrc"
            )

        try:
            with open(path, "w", encoding="utf-8") as file:
                file.write(self.text_edit.toPlainText())

            update_lyrics_path(
                self.song["id"],
                path,
            )

            self.accept()

        except OSError as error:
            QMessageBox.critical(
                self,
                "Erro ao salvar",
                f"Não foi possível salvar a letra:\n{error}",
            )


class KaraokeWindow(QMainWindow):
    def __init__(self, song, player, parent=None):
        super().__init__(parent)

        self.song = song
        self.player = player
        self.lines = []
        self.last_index = -2
        self.karaoke_timer = QTimer(self)

        self.setWindowTitle(
            f"Karaokê — {song['title']}"
        )

        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #080808;
                color: #f2f2f2;
            }

            QLabel#title {
                font-size: 30px;
                font-weight: bold;
            }

            QLabel#artist {
                font-size: 19px;
                color: #aaaaaa;
            }

            QLabel#previous, QLabel#next {
                font-size: 27px;
                color: #777777;
            }

            QLabel#current {
                font-size: 43px;
                font-weight: bold;
                color: #ffffff;
            }

            QPushButton {
                padding: 10px 18px;
                font-size: 15px;
            }
            """
        )

        root = QWidget()
        self.setCentralWidget(root)

        layout = QVBoxLayout(root)
        layout.setContentsMargins(45, 30, 45, 25)

        self.title_label = QLabel(song["title"])
        self.title_label.setObjectName("title")
        self.title_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )
        layout.addWidget(self.title_label)

        self.artist_label = QLabel(song["artist"])
        self.artist_label.setObjectName("artist")
        self.artist_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )
        layout.addWidget(self.artist_label)

        layout.addStretch()

        self.previous_label = QLabel("")
        self.previous_label.setObjectName("previous")
        self.previous_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )
        self.previous_label.setWordWrap(True)
        layout.addWidget(self.previous_label)

        self.current_label = KaraokeTextWidget()
        layout.addWidget(self.current_label)

        self.next_label = QLabel("")
        self.next_label.setObjectName("next")
        self.next_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )
        self.next_label.setWordWrap(True)
        layout.addWidget(self.next_label)

        layout.addStretch()

        self.progress = SeekSlider(
            Qt.Orientation.Horizontal
        )
        self.progress.setRange(0, 0)
        self.progress.setTracking(True)
        self.progress.sliderMoved.connect(
            self.seek
        )
        self.progress.sliderReleased.connect(
            lambda: self.seek(self.progress.value())
        )
        layout.addWidget(self.progress)

        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )
        layout.addWidget(self.time_label)

        controls = QHBoxLayout()

        self.back_button = QPushButton("<< 5s")
        self.back_button.clicked.connect(
            lambda: self.seek_relative(-5000)
        )
        controls.addWidget(self.back_button)

        self.play_button = QPushButton("Pausar")
        self.play_button.clicked.connect(
            self.player.toggle
        )
        controls.addWidget(self.play_button)

        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(
            self.stop_and_rewind
        )
        controls.addWidget(self.stop_button)

        self.forward_button = QPushButton("5s >>")
        self.forward_button.clicked.connect(
            lambda: self.seek_relative(5000)
        )
        controls.addWidget(self.forward_button)

        controls.addWidget(QLabel("Volume"))

        self.volume = QSlider(
            Qt.Orientation.Horizontal
        )
        self.volume.setRange(0, 100)
        self.volume.setValue(self.player.volume())
        self.volume.setMaximumWidth(180)
        self.volume.valueChanged.connect(
            self.player.set_volume
        )
        controls.addWidget(self.volume)

        self.volume_percent = QLabel(f"{self.player.volume()}%")
        self.volume_percent.setMinimumWidth(42)
        controls.addWidget(self.volume_percent)

        controls.addStretch()

        close_button = QPushButton(
            "Fechar (Esc)"
        )
        close_button.clicked.connect(self.close)
        controls.addWidget(close_button)

        layout.addLayout(controls)

        self.load_lyrics()

        self.player.position_changed.connect(
            self.refresh
        )
        self.player.duration_changed.connect(
            self.set_duration
        )
        self.player.state_changed.connect(
            self.update_play_button
        )
        self.player.volume_changed.connect(
            self.update_volume_display
        )

        self.karaoke_timer.setInterval(40)
        self.karaoke_timer.timeout.connect(
            lambda: self.refresh(self.player.position())
        )
        self.karaoke_timer.start()

        self.setAttribute(
            Qt.WidgetAttribute.WA_DeleteOnClose
        )

        self.showFullScreen()

        self.set_duration(self.player.media.duration())
        self.refresh(
            self.player.media.position()
        )
        self.update_play_button(
            "playing"
            if self.player.media.playbackState().name == "PlayingState"
            else "paused"
        )

    def load_lyrics(self):
        path = self.song["lyrics_path"]

        if not path or not os.path.exists(path):
            self.lines = []
            return

        try:
            with open(path, "r", encoding="utf-8") as file:
                self.lines = parse_lrc(
                    file.read()
                )
        except OSError:
            self.lines = []

    def set_duration(self, milliseconds):
        self.progress.setRange(
            0,
            max(0, milliseconds)
        )

    def seek(self, milliseconds):
        self.player.set_position(
            milliseconds
        )
        self.refresh(milliseconds)

    def seek_relative(self, delta_ms):
        duration = self.player.media.duration()
        target = self.player.position() + delta_ms
        if duration > 0:
            target = min(target, duration)
        self.seek(max(0, target))

    def stop_and_rewind(self):
        self.player.stop()
        self.player.set_position(0)
        self.refresh(0)

    def update_play_button(self, state):
        if state == "playing":
            self.play_button.setText("Pausar")
        else:
            self.play_button.setText("Continuar")

    def update_volume_display(self, value):
        with QSignalBlocker(self.volume):
            self.volume.setValue(value)
        self.volume_percent.setText(f"{value}%")

    def render_current_line(self, index, milliseconds):
        if not self.lines:
            return

        if index < 0:
            self.current_label.set_karaoke_text(self.lines[0]["text"], 0.0)
            return

        line = self.lines[index]
        text = line["text"]
        words = line.get("words") or []

        if words:
            line_end = (
                self.lines[index + 1]["time"]
                if index + 1 < len(self.lines)
                else self.player.media.duration()
            )

            font = self.current_label._fitted_font()
            metrics = QFontMetrics(font)
            total_width = max(1, metrics.horizontalAdvance(text))
            filled_width = 0.0

            for word_index, word in enumerate(words):
                start = word["time"]
                if word_index + 1 < len(words):
                    end = words[word_index + 1]["time"]
                else:
                    end = max(start + 1, line_end)

                word_text = word["text"]
                word_width = metrics.horizontalAdvance(word_text)

                if milliseconds >= end:
                    filled_width += word_width
                    continue

                if milliseconds <= start:
                    break

                progress = max(
                    0.0,
                    min(1.0, (milliseconds - start) / max(1, end - start)),
                )
                filled_width += word_width * progress
                break

            self.current_label.set_karaoke_text(
                text,
                filled_width / total_width,
            )
            return

        start = line["time"]
        if index + 1 < len(self.lines):
            end = self.lines[index + 1]["time"]
        else:
            end = self.player.media.duration()

        progress = (
            1.0
            if end <= start
            else max(0.0, min(1.0, (milliseconds - start) / (end - start)))
        )
        self.current_label.set_karaoke_text(text, progress)

    def refresh(self, milliseconds):
        with QSignalBlocker(self.progress):
            self.progress.setValue(
                min(
                    milliseconds,
                    self.progress.maximum()
                )
            )

        duration = self.player.media.duration()

        self.time_label.setText(
            f"{format_ms(milliseconds)} / "
            f"{format_ms(duration)}"
        )

        if not self.lines:
            self.previous_label.setText("")
            self.current_label.set_karaoke_text(
                "Nenhuma letra LRC cadastrada", 0.0
            )
            self.next_label.setText("")
            return

        index = active_index(
            self.lines,
            milliseconds
        )

        self.render_current_line(
            index,
            milliseconds,
        )

        if index == self.last_index:
            return

        self.last_index = index

        if index > 0:
            self.previous_label.setText(
                self.lines[index - 1]["text"]
            )
        else:
            self.previous_label.setText("")

        next_index = index + 1

        if index < 0:
            next_index = 1

        if next_index < len(self.lines):
            self.next_label.setText(
                self.lines[next_index]["text"]
            )
        else:
            self.next_label.setText("")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return

        if event.key() == Qt.Key.Key_Space:
            self.player.toggle()
            return

        super().keyPressEvent(event)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.player = Player(self)
        self.current_song = None
        self.karaoke_windows = []

        self.setWindowTitle(
            "Karaokê v0.9"
        )
        self.resize(1100, 700)

        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #181818;
                color: #eeeeee;
            }

            QLineEdit, QListWidget, QTextEdit {
                background: #222222;
                color: #eeeeee;
                border: 1px solid #444444;
                padding: 6px;
            }

            QPushButton {
                padding: 8px 12px;
            }

            QLabel#nowPlaying {
                font-size: 18px;
                font-weight: bold;
            }
            """
        )

        self.build_ui()
        self.player.position_changed.connect(
            self.update_progress
        )
        self.player.duration_changed.connect(
            self.update_duration
        )
        self.player.state_changed.connect(
            self.update_play_button
        )
        self.player.volume_changed.connect(
            self.update_volume_display
        )

        self.refresh_library()

    def build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        root = QVBoxLayout(central)

        header = QHBoxLayout()

        title = QLabel("Karaokê v0.9")
        title.setStyleSheet(
            "font-size: 24px; font-weight: bold;"
        )
        header.addWidget(title)

        header.addStretch()

        self.search = QLineEdit()
        self.search.setPlaceholderText(
            "Buscar título ou artista..."
        )
        self.search.setMaximumWidth(350)
        self.search.textChanged.connect(
            self.refresh_library
        )
        header.addWidget(self.search)

        root.addLayout(header)

        splitter = QSplitter(
            Qt.Orientation.Horizontal
        )

        # Biblioteca
        library_widget = QWidget()
        library_layout = QVBoxLayout(
            library_widget
        )

        self.song_list = QListWidget()
        self.song_list.itemDoubleClicked.connect(
            self.play_selected
        )
        self.song_list.itemSelectionChanged.connect(
            self.selection_changed
        )
        library_layout.addWidget(
            self.song_list
        )

        library_buttons = QHBoxLayout()

        add_button = QPushButton(
            "Adicionar música"
        )
        add_button.clicked.connect(
            self.add_music
        )
        library_buttons.addWidget(add_button)

        import_folder_button = QPushButton(
            "Importar pasta"
        )
        import_folder_button.clicked.connect(
            self.import_music_folder
        )
        library_buttons.addWidget(import_folder_button)

        youtube_button = QPushButton(
            "Baixar música + separar"
        )
        youtube_button.clicked.connect(
            self.download_from_youtube
        )
        library_buttons.addWidget(youtube_button)

        delete_button = QPushButton(
            "Excluir"
        )
        delete_button.clicked.connect(
            self.delete_selected
        )
        library_buttons.addWidget(
            delete_button
        )

        library_layout.addLayout(
            library_buttons
        )

        splitter.addWidget(
            library_widget
        )

        # Painel direito
        right = QWidget()
        right_layout = QVBoxLayout(right)

        self.now_playing = QLabel(
            "Nenhuma música selecionada"
        )
        self.now_playing.setObjectName(
            "nowPlaying"
        )
        self.now_playing.setWordWrap(True)
        right_layout.addWidget(
            self.now_playing
        )

        controls = QHBoxLayout()

        self.play_button = QPushButton(
            "Play"
        )
        self.play_button.clicked.connect(
            self.player.toggle
        )
        controls.addWidget(
            self.play_button
        )

        stop_button = QPushButton(
            "Stop"
        )
        stop_button.clicked.connect(
            self.player.stop
        )
        controls.addWidget(
            stop_button
        )

        karaoke_button = QPushButton(
            "Abrir karaokê"
        )
        karaoke_button.clicked.connect(
            self.open_karaoke
        )
        controls.addWidget(
            karaoke_button
        )

        lyrics_button = QPushButton(
            "Editar letra"
        )
        lyrics_button.clicked.connect(
            self.edit_lyrics
        )
        controls.addWidget(
            lyrics_button
        )

        right_layout.addLayout(
            controls
        )

        self.progress = QSlider(
            Qt.Orientation.Horizontal
        )
        self.progress.setRange(0, 0)
        self.progress.sliderMoved.connect(
            self.seek
        )
        right_layout.addWidget(
            self.progress
        )

        self.time_label = QLabel(
            "00:00 / 00:00"
        )
        right_layout.addWidget(
            self.time_label
        )

        volume_row = QHBoxLayout()
        volume_row.addWidget(
            QLabel("Volume")
        )

        self.volume = QSlider(
            Qt.Orientation.Horizontal
        )
        self.volume.setRange(0, 100)
        self.volume.setValue(self.player.volume())
        self.volume.valueChanged.connect(
            self.player.set_volume
        )
        volume_row.addWidget(
            self.volume
        )

        self.volume_percent = QLabel(f"{self.player.volume()}%")
        self.volume_percent.setMinimumWidth(42)
        volume_row.addWidget(self.volume_percent)

        right_layout.addLayout(
            volume_row
        )

        queue_label = QLabel(
            "Fila"
        )
        queue_label.setStyleSheet(
            "font-size: 18px; font-weight: bold;"
        )
        right_layout.addWidget(
            queue_label
        )

        self.queue_list = QListWidget()
        right_layout.addWidget(
            self.queue_list
        )

        clear_queue = QPushButton(
            "Limpar fila"
        )
        clear_queue.clicked.connect(
            self.queue_list.clear
        )
        right_layout.addWidget(
            clear_queue
        )

        right_layout.addStretch()

        splitter.addWidget(right)

        splitter.setSizes([600, 500])

        root.addWidget(splitter)

    def refresh_library(self, selected_id=None):
        current = self.song_list.currentItem()
        if selected_id is None and current:
            selected_id = current.data(
                Qt.ItemDataRole.UserRole
            )["id"]

        self.song_list.clear()

        for song in list_songs(
            self.search.text()
        ):
            item = QListWidgetItem(
                f"{song['title']} — {song['artist']}"
                if song["artist"]
                else song["title"]
            )

            item.setData(
                Qt.ItemDataRole.UserRole,
                dict(song)
            )

            self.song_list.addItem(item)

            if (
                selected_id is not None
                and song["id"] == selected_id
            ):
                item.setSelected(True)

    def selection_changed(self):
        item = self.song_list.currentItem()

        if not item:
            return

        song = item.data(
            Qt.ItemDataRole.UserRole
        )

        self.current_song = song

        self.now_playing.setText(
            f"{song['title']} — {song['artist']}"
            if song["artist"]
            else song["title"]
        )

    def add_music(self):
        source, _ = QFileDialog.getOpenFileName(
            self,
            "Selecionar música",
            "",
            "Áudio (*.mp3 *.wav *.ogg *.flac *.m4a *.aac);;Todos os arquivos (*.*)",
        )

        if not source:
            return

        title, ok = QInputDialog.getText(
            self,
            "Título",
            "Título da música:",
            text=os.path.splitext(
                os.path.basename(source)
            )[0],
        )

        if not ok or not title.strip():
            return

        artist, ok = QInputDialog.getText(
            self,
            "Artista",
            "Artista:",
        )

        if not ok:
            return

        destination = unique_destination(
            str(MUSIC_DIR),
            os.path.basename(source)
        )

        lyrics_destination = ""
        try:
            shutil.copy2(
                source,
                destination
            )
            source_lrc = matching_lrc_path(source)
            if source_lrc:
                lyrics_destination = os.path.splitext(destination)[0] + ".lrc"
                shutil.copy2(source_lrc, lyrics_destination)
        except OSError as error:
            QMessageBox.critical(
                self,
                "Erro",
                f"Não foi possível copiar a música:\n{error}",
            )
            return

        add_song(
            title.strip(),
            artist.strip(),
            destination,
            lyrics_destination,
        )

        self.refresh_library()

    def import_music_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "Selecionar pasta com músicas",
            "",
        )
        if not folder:
            return

        sources = sorted(
            path for path in Path(folder).rglob("*")
            if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS
        )
        if not sources:
            QMessageBox.information(
                self,
                "Importar pasta",
                "Nenhum arquivo de áudio compatível foi encontrado.",
            )
            return

        imported = 0
        skipped = 0
        errors = []

        for source_path in sources:
            try:
                if source_path.resolve().parent == Path(MUSIC_DIR).resolve():
                    destination = str(source_path.resolve())
                else:
                    shared_candidate = Path(MUSIC_DIR) / source_path.name
                    if shared_candidate.exists() and get_song_by_audio_path(str(shared_candidate)):
                        skipped += 1
                        continue
                    destination = (
                        str(shared_candidate)
                        if not shared_candidate.exists()
                        else unique_destination(str(MUSIC_DIR), source_path.name)
                    )
                    shutil.copy2(source_path, destination)

                if get_song_by_audio_path(destination):
                    skipped += 1
                    continue

                source_lrc = matching_lrc_path(source_path)
                lyrics_destination = ""
                if source_lrc:
                    if Path(destination).resolve().parent == Path(MUSIC_DIR).resolve() and source_path.resolve().parent == Path(MUSIC_DIR).resolve():
                        lyrics_destination = str(source_lrc.resolve())
                    else:
                        lyrics_destination = os.path.splitext(destination)[0] + ".lrc"
                        shutil.copy2(source_lrc, lyrics_destination)

                artist, title = infer_artist_title(source_path)
                add_song(
                    title,
                    artist,
                    destination,
                    lyrics_destination,
                )
                imported += 1
            except Exception as error:
                errors.append(f"{source_path.name}: {error}")

        self.refresh_library()
        message = f"{imported} música(s) importada(s)."
        if skipped:
            message += f" {skipped} já estava(m) cadastrada(s)."
        if errors:
            message += f" {len(errors)} arquivo(s) tiveram erro.\n\n" + "\n".join(errors[:8])
        QMessageBox.information(self, "Importar pasta", message)

    def download_from_youtube(self):
        url, ok = QInputDialog.getText(
            self,
            "Baixar do YouTube",
            "Cole o link do vídeo do YouTube:",
        )
        if not ok or not url.strip():
            return

        if not re.match(r"^https?://", url.strip(), re.IGNORECASE):
            QMessageBox.warning(
                self,
                "Link inválido",
                "Informe um link completo começando com http:// ou https://.",
            )
            return

        self.youtube_progress = QProgressDialog(
            "Preparando download...",
            "Cancelar visualização",
            0,
            100,
            self,
        )
        self.youtube_progress.setWindowTitle("Baixar do YouTube")
        self.youtube_progress.setAutoClose(False)
        self.youtube_progress.setAutoReset(False)
        self.youtube_progress.setMinimumDuration(0)
        self.youtube_progress.setValue(0)
        self.youtube_progress.show()

        self.youtube_source_url = url.strip()
        self.youtube_thread = QThread(self)
        self.youtube_worker = YouTubeDownloadWorker(
            self.youtube_source_url,
            MUSIC_DIR,
        )
        self.youtube_worker.moveToThread(self.youtube_thread)
        self.youtube_thread.started.connect(self.youtube_worker.run)
        self.youtube_worker.progress.connect(self.on_youtube_progress)
        self.youtube_worker.finished.connect(self.on_youtube_finished)
        self.youtube_worker.failed.connect(self.on_youtube_failed)
        self.youtube_worker.finished.connect(self.youtube_thread.quit)
        self.youtube_worker.failed.connect(self.youtube_thread.quit)
        self.youtube_thread.finished.connect(self.youtube_worker.deleteLater)
        self.youtube_thread.finished.connect(self.youtube_thread.deleteLater)
        self.youtube_thread.start()

    def on_youtube_progress(self, value, message):
        if getattr(self, "youtube_progress", None):
            self.youtube_progress.setValue(value)
            self.youtube_progress.setLabelText(message)

    def on_youtube_finished(self, title, artist, audio_path):
        if getattr(self, "youtube_progress", None):
            self.youtube_progress.setValue(100)
            self.youtube_progress.close()

        existing = get_song_by_audio_path(audio_path)
        if existing:
            self.refresh_library(existing["id"])
            QMessageBox.information(
                self,
                "Música já cadastrada",
                "Esse áudio já existe na biblioteca. Se quiser refazer a separação, abra o Editor de letra e use "
                "'Preparar voz/instrumental'.",
            )
            return

        song_id = add_song(
            title or Path(audio_path).stem,
            artist or "",
            audio_path,
            "",
            source_audio_path=audio_path,
            source_url=getattr(self, "youtube_source_url", ""),
        )
        self.refresh_library(song_id)
        self.start_automatic_preparation(song_id, audio_path, title, artist)

    def start_automatic_preparation(self, song_id, source_path, title="", artist=""):
        self.pending_preparation = {
            "song_id": song_id,
            "source_path": source_path,
            "title": title,
            "artist": artist,
        }
        self.auto_prepare_progress = QProgressDialog(
            "Separando voz e instrumental...",
            "Ocultar",
            0,
            100,
            self,
        )
        self.auto_prepare_progress.setWindowTitle("Separando voz e instrumental")
        self.auto_prepare_progress.setAutoClose(False)
        self.auto_prepare_progress.setAutoReset(False)
        self.auto_prepare_progress.setMinimumDuration(0)
        self.auto_prepare_progress.show()

        self.auto_prepare_thread = QThread(self)
        self.auto_prepare_worker = StemSeparationWorker(source_path, MUSIC_DIR)
        self.auto_prepare_worker.moveToThread(self.auto_prepare_thread)
        self.auto_prepare_thread.started.connect(self.auto_prepare_worker.run)
        self.auto_prepare_worker.progress.connect(self.on_auto_prepare_progress)
        self.auto_prepare_worker.finished.connect(self.on_auto_prepare_finished)
        self.auto_prepare_worker.failed.connect(self.on_auto_prepare_failed)
        self.auto_prepare_worker.finished.connect(self.auto_prepare_thread.quit)
        self.auto_prepare_worker.failed.connect(self.auto_prepare_thread.quit)
        self.auto_prepare_thread.finished.connect(self.auto_prepare_worker.deleteLater)
        self.auto_prepare_thread.finished.connect(self.auto_prepare_thread.deleteLater)
        self.auto_prepare_thread.start()

    def on_auto_prepare_progress(self, value, message):
        if getattr(self, "auto_prepare_progress", None):
            self.auto_prepare_progress.setValue(value)
            self.auto_prepare_progress.setLabelText(message)

    def on_auto_prepare_finished(self, instrumental_path, vocals_path):
        pending = getattr(self, "pending_preparation", {})
        song_id = pending.get("song_id")
        source_path = pending.get("source_path")
        if song_id:
            update_song_stems(song_id, instrumental_path, vocals_path, source_path)
        pending["instrumental_path"] = instrumental_path
        pending["vocals_path"] = vocals_path
        self.pending_preparation = pending

        if getattr(self, "auto_prepare_progress", None):
            self.auto_prepare_progress.setValue(100)
            self.auto_prepare_progress.close()
        self.refresh_library(song_id)

        title = pending.get("title") or Path(source_path or instrumental_path).stem
        dialog = ManualLyricsDialog(title, '', self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            QMessageBox.information(
                self,
                "Música separada",
                "Download, vocal e instrumental estão prontos. A letra ficou pendente. Abra o Editor de letra quando quiser continuar.",
            )
            return
        self.start_downloaded_song_alignment(dialog.lyrics_text())

    def start_downloaded_song_alignment(self, lyrics_text):
        pending = getattr(self, "pending_preparation", {})
        vocals_path = pending.get("vocals_path")
        instrumental_path = pending.get("instrumental_path")
        if not vocals_path or not os.path.exists(vocals_path):
            QMessageBox.warning(self, "Vocal não encontrado", "O vocal separado não foi localizado.")
            return
        output_path = os.path.splitext(instrumental_path)[0] + ".lrc"

        self.auto_align_progress = QProgressDialog(
            "Localizando os tempos da letra...", "Ocultar", 0, 100, self
        )
        self.auto_align_progress.setWindowTitle("Sincronizando letra correta")
        self.auto_align_progress.setAutoClose(False)
        self.auto_align_progress.setAutoReset(False)
        self.auto_align_progress.setMinimumDuration(0)
        self.auto_align_progress.show()

        self.auto_align_thread = QThread(self)
        self.auto_align_worker = LyricsAlignmentWorker(
            vocals_path, lyrics_text, output_path, model_size="small"
        )
        self.auto_align_worker.moveToThread(self.auto_align_thread)
        self.auto_align_thread.started.connect(self.auto_align_worker.run)
        self.auto_align_worker.progress.connect(self.on_auto_align_progress)
        self.auto_align_worker.finished.connect(self.on_auto_align_finished)
        self.auto_align_worker.failed.connect(self.on_auto_align_failed)
        self.auto_align_worker.finished.connect(self.auto_align_thread.quit)
        self.auto_align_worker.failed.connect(self.auto_align_thread.quit)
        self.auto_align_thread.finished.connect(self.auto_align_worker.deleteLater)
        self.auto_align_thread.finished.connect(self.auto_align_thread.deleteLater)
        self.auto_align_thread.start()

    def on_auto_align_progress(self, value, message):
        if getattr(self, "auto_align_progress", None):
            self.auto_align_progress.setValue(value)
            self.auto_align_progress.setLabelText(message)

    def on_auto_align_finished(self, lyrics_path, language, confidence):
        pending = getattr(self, "pending_preparation", {})
        song_id = pending.get("song_id")
        if song_id:
            mark_song_synced(song_id, lyrics_path)
        if getattr(self, "auto_align_progress", None):
            self.auto_align_progress.setValue(100)
            self.auto_align_progress.close()
        self.refresh_library(song_id)
        quality = (
            "A correspondência com o vocal ficou alta." if confidence >= 70
            else "A correspondência ficou parcial; vale revisar os tempos no Editor de letra."
        )
        QMessageBox.information(
            self,
            "Karaokê pronto",
            "Processo concluído.\n\n"
            "1. música original mantida\n"
            "2. vocal separado\n"
            "3. instrumental gerado\n"
            "4. sua letra foi preservada\n"
            "5. os tempos foram sincronizados com o vocal\n\n"
            f"{quality} Referência de correspondência: {confidence}%."
        )

    def on_auto_align_failed(self, message):
        if getattr(self, "auto_align_progress", None):
            self.auto_align_progress.close()
        pending = getattr(self, "pending_preparation", {})
        self.refresh_library(pending.get("song_id"))
        QMessageBox.critical(
            self,
            "Não foi possível sincronizar a letra",
            f"A música, o vocal e o instrumental foram mantidos.\n\n{message}\n\n"
            "Abra o Editor de letra para tentar novamente sem baixar a música outra vez.",
        )

    def on_auto_prepare_failed(self, message):
        if getattr(self, "auto_prepare_progress", None):
            self.auto_prepare_progress.close()
        pending = getattr(self, "pending_preparation", {})
        self.refresh_library(pending.get("song_id"))
        QMessageBox.critical(
            self,
            "Download concluído, mas a separação falhou",
            "A música original foi mantida na biblioteca, então você não precisa baixá-la novamente.\n\n"
            f"Erro durante a separação:\n{message}\n\n"
            "Abra o Editor de letra e tente preparar voz/instrumental novamente.",
        )

    def on_youtube_failed(self, message):
        if getattr(self, "youtube_progress", None):
            self.youtube_progress.close()
        QMessageBox.critical(
            self,
            "Erro no download do YouTube",
            message,
        )

    def get_selected_song(self):
        item = self.song_list.currentItem()

        if not item:
            QMessageBox.information(
                self,
                "Selecione uma música",
                "Selecione uma música na biblioteca primeiro.",
            )
            return None

        return item.data(
            Qt.ItemDataRole.UserRole
        )

    def play_selected(self, *_):
        song = self.get_selected_song()

        if not song:
            return

        if not os.path.exists(
            song["audio_path"]
        ):
            QMessageBox.warning(
                self,
                "Arquivo não encontrado",
                f"O arquivo não existe:\n{song['audio_path']}",
            )
            return

        self.current_song = song
        self.player.load(
            song["audio_path"]
        )
        self.player.set_volume(Player.DEFAULT_VOLUME)
        self.player.play()

        self.queue_list.addItem(
            f"{song['title']} — {song['artist']}"
            if song["artist"]
            else song["title"]
        )

    def delete_selected(self):
        song = self.get_selected_song()

        if not song:
            return

        answer = QMessageBox.question(
            self,
            "Excluir música",
            f"Excluir '{song['title']}' da biblioteca?",
        )

        if answer != QMessageBox.StandardButton.Yes:
            return

        delete_song(
            song["id"]
        )

        self.current_song = None
        self.refresh_library()

    def edit_lyrics(self):
        song = self.get_selected_song()

        if not song:
            return

        if not os.path.exists(song["audio_path"]):
            QMessageBox.warning(
                self,
                "Arquivo não encontrado",
                f"O arquivo não existe:\n{song['audio_path']}",
            )
            return

        self.current_song = song

        if not self.player.is_loaded(song["audio_path"]):
            self.player.load(song["audio_path"])

        dialog = LyricsEditor(
            song,
            self.player,
            self,
        )

        dialog.exec()

        self.refresh_library()

    def open_karaoke(self):
        song = self.get_selected_song()

        if not song:
            return

        if not os.path.exists(
            song["audio_path"]
        ):
            QMessageBox.warning(
                self,
                "Arquivo não encontrado",
                f"O arquivo não existe:\n{song['audio_path']}",
            )
            return

        self.current_song = song

        # Se a música ainda não estiver carregada,
        # carregamos antes de abrir a tela.
        self.player.load(
            song["audio_path"]
        )

        window = KaraokeWindow(
            song,
            self.player,
            self,
        )

        self.karaoke_windows.append(window)

        def remove_window():
            if window in self.karaoke_windows:
                self.karaoke_windows.remove(
                    window
                )

        window.destroyed.connect(
            remove_window
        )

    def seek(self, milliseconds):
        self.player.set_position(
            milliseconds
        )

    def update_progress(self, milliseconds):
        with QSignalBlocker(self.progress):
            self.progress.setValue(
                min(
                    milliseconds,
                    self.progress.maximum()
                )
            )

        self.time_label.setText(
            f"{format_ms(milliseconds)} / "
            f"{format_ms(self.player.media.duration())}"
        )

    def update_duration(self, milliseconds):
        self.progress.setRange(
            0,
            max(0, milliseconds)
        )

    def update_play_button(self, state):
        if state == "playing":
            self.play_button.setText(
                "Pause"
            )
        else:
            self.play_button.setText(
                "Play"
            )

    def update_volume_display(self, value):
        with QSignalBlocker(self.volume):
            self.volume.setValue(value)
        self.volume_percent.setText(f"{value}%")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_F11:
            if self.isFullScreen():
                self.showNormal()
            else:
                self.showFullScreen()
            return

        if event.key() == Qt.Key.Key_Space:
            self.player.toggle()
            return

        super().keyPressEvent(event)


def main():
    app = QApplication(sys.argv)

    init_db()

    window = MainWindow()
    window.show()

    sys.exit(
        app.exec()
    )


if __name__ == "__main__":
    main()
