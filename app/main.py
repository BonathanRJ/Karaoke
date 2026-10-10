import html
import os
import re
import shutil
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QSignalBlocker, QTimer, QRectF, QObject, QThread, Signal, QVariantAnimation, QEasingCurve, QSize, QPointF, QPropertyAnimation
from PySide6.QtGui import QPainter, QColor, QFont, QFontMetrics, QBrush, QPixmap, QIcon, QPolygonF
from PySide6.QtMultimedia import QMediaPlayer
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
    QTreeWidget,
    QTreeWidgetItem,
    QHeaderView,
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
    QGraphicsOpacityEffect,
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
    ORIGINAL_DIR,
    LYRIC_DIR,
    update_song_metadata,
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

        painter.setPen(QColor("#b6d8b6"))
        painter.drawText(int(x), int(baseline), self._text)

        fill_width = text_width * self._fill_ratio
        if fill_width > 0:
            painter.save()
            painter.setClipRect(QRectF(x, 0, fill_width, self.height()))
            painter.setPen(QColor("#ffffff"))
            painter.drawText(int(x), int(baseline), self._text)
            painter.restore()


class TransportButton(QPushButton):
    """Ícones de transporte próprios, com cores consistentes no Windows/Qt."""
    def __init__(self, kind, parent=None):
        super().__init__(parent)
        self.kind = kind
        self._hovered = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIconSize(QSize(28, 28))
        self.refresh_icon()

    def set_kind(self, kind):
        self.kind = kind
        self.refresh_icon()

    def enterEvent(self, event):
        self._hovered = True
        self.refresh_icon()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.refresh_icon()
        super().leaveEvent(event)

    def refresh_icon(self):
        color = '#111111' if self.kind in ('play', 'pause') else ('#ffffff' if self._hovered else '#b7b7bb')
        pix = QPixmap(32, 32)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        def triangle(points):
            painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in points]))
        if self.kind == 'back':
            triangle([(4, 16), (26, 2), (26, 30)])
            painter.drawRoundedRect(QRectF(2, 2, 4, 28), 0.6, 0.6)
        elif self.kind == 'forward':
            triangle([(28, 16), (6, 2), (6, 30)])
            painter.drawRoundedRect(QRectF(26, 2, 4, 28), 0.6, 0.6)
        elif self.kind == 'pause':
            painter.drawRoundedRect(QRectF(10, 8, 4, 16), 1, 1)
            painter.drawRoundedRect(QRectF(18, 8, 4, 16), 1, 1)
        else:
            triangle([(11, 7), (25, 16), (11, 25)])
        painter.end()
        self.setIcon(QIcon(pix))


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


def format_clock(milliseconds):
    total_seconds = max(0, int(milliseconds)) // 1000
    return f"{total_seconds // 60}:{total_seconds % 60:02d}"


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
            str(LYRIC_DIR / (Path(self.song["audio_path"]).stem + ".lrc"))
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


def cover_background_color(pixmap):
    """Extrai uma tonalidade representativa da capa, escura para leitura das letras."""
    image = pixmap.toImage()
    if image.isNull():
        return "#000000"
    image = image.scaled(72, 72, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    # Agrupa tons por matiz, evitando que brilhos, pretos e tons de pele
    # dominem uma capa com fundos coloridos.
    groups = {}
    for y in range(0, image.height(), 2):
        for x in range(0, image.width(), 2):
            color = image.pixelColor(x, y)
            hue, saturation, value, alpha = color.getHsv()
            if alpha < 180 or hue < 0 or saturation < 55 or value < 35:
                continue
            key = hue // 15
            weight = 1.0 + saturation / 255.0
            if key not in groups:
                groups[key] = [0.0, 0.0, 0.0, 0.0]
            bucket = groups[key]
            bucket[0] += weight
            bucket[1] += color.red() * weight
            bucket[2] += color.green() * weight
            bucket[3] += color.blue() * weight
    if not groups:
        return "#000000"
    dominant = max(groups.values(), key=lambda group: group[0])
    sample = QColor(round(dominant[1] / dominant[0]),
                    round(dominant[2] / dominant[0]),
                    round(dominant[3] / dominant[0]))
    hue, saturation, _, _ = sample.getHsv()
    if hue < 0:
        return "#000000"
    # Saturacao moderada e baixa luminosidade mantem a letra em contraste.
    return QColor.fromHsv(hue, min(195, max(115, saturation)), 65).name()


class KaraokeWindow(QMainWindow):
    def __init__(self, song, player, parent=None):
        super().__init__(parent)

        self.song = song
        self.player = player
        self.lines = []
        self.last_index = -2
        self._verse_animations = []
        self.karaoke_timer = QTimer(self)

        self.setWindowTitle(
            f"Karaokê — {song['title']}"
        )

        self.lyric_background = "#000000"
        self._applied_lyric_background = "#000000"
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #000000;
                color: #ffffff;
            }

            QLabel#title {
                font-size: 23px;
                font-weight: bold;
            }

            QLabel#artist {
                font-size: 16px;
                color: #aaaaaa;
            }

            QLabel#verse {
                background: transparent;
                font-weight: bold;
                color: #9ac49d;
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
        layout.setContentsMargins(0, 30, 0, 0)

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

        layout.addStretch(1)

        # A capa e opcional; a area de versos permanece centralizada sem imagem.
        center_row = QHBoxLayout()
        center_row.setSpacing(30)
        center_row.setContentsMargins(45, 0, 45, 0)
        self.cover_label = QLabel()
        self.cover_label.setFixedSize(220, 220)
        self.cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover_label.setStyleSheet("background: transparent;")
        self.cover_label.hide()
        self.load_cover()
        center_row.addWidget(self.cover_label, 0, Qt.AlignmentFlag.AlignVCenter)
        verse_layout = QVBoxLayout()
        verse_layout.setSpacing(16)
        self.verse_labels = []
        for _ in range(2):
            label = QLabel("")
            label.setObjectName("verse")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setWordWrap(True)
            label.setMinimumHeight(44)
            verse_layout.addWidget(label, 1)
            self.verse_labels.append(label)

        self.current_label = KaraokeTextWidget()
        self.current_label._base_font_size = 42
        self.current_label.setMinimumHeight(86)
        verse_layout.addWidget(self.current_label, 1)

        for _ in range(3):
            label = QLabel("")
            label.setObjectName("verse")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setWordWrap(True)
            label.setMinimumHeight(44)
            verse_layout.addWidget(label, 1)
            self.verse_labels.append(label)
        center_row.addLayout(verse_layout, 1)
        # Fade rapido apenas no momento da troca de verso; o relogio LRC
        # continua sendo usado diretamente para manter a sincronia.
        self._fade_widgets = [self.current_label, *self.verse_labels]
        for widget in self._fade_widgets:
            effect = QGraphicsOpacityEffect(widget)
            effect.setOpacity(1.0)
            widget.setGraphicsEffect(effect)
        layout.addLayout(center_row, 6)

        # A capa nao depende do titulo que aparece na biblioteca.
        self.last_index = None

        layout.addStretch(1)

        # Rodape preto: controles e progresso centralizados, utilitarios nas bordas.
        control_panel = QWidget()
        control_panel.setObjectName("karaokeControlPanel")
        control_layout = QVBoxLayout(control_panel)
        control_layout.setContentsMargins(16, 19, 16, 9)
        control_layout.setSpacing(5)

        transport = QHBoxLayout()
        transport.setSpacing(6)
        transport.addStretch(1)

        self.back_button = TransportButton("back")
        self.back_button.setObjectName("transportSecondary")
        self.back_button.setToolTip("Ate 2s: musica anterior; apos 2s: reiniciar musica")
        self.back_button.setIconSize(QSize(20, 20))
        self.back_button.clicked.connect(self.back_queue_song)
        transport.addWidget(self.back_button)

        self.play_button = TransportButton("play")
        self.play_button.setObjectName("transportPlay")
        self.play_button.setToolTip("Pausar / continuar (Espaco)")
        self.play_button.setIconSize(QSize(24, 24))
        self.play_button.clicked.connect(self.player.toggle)
        transport.addWidget(self.play_button)

        self.forward_button = TransportButton("forward")
        self.forward_button.setObjectName("transportSecondary")
        self.forward_button.setToolTip("Pular musica e tocar a proxima da fila")
        self.forward_button.setIconSize(QSize(20, 20))
        self.forward_button.clicked.connect(self.skip_queue_song)
        transport.addWidget(self.forward_button)
        transport.addStretch(1)
        control_layout.addLayout(transport)

        self.progress = SeekSlider(Qt.Orientation.Horizontal)
        self.progress.setObjectName("transportProgress")
        self.progress.setRange(0, 0)
        self.progress.setTracking(True)
        self.progress.sliderMoved.connect(self.seek)
        self.progress.sliderReleased.connect(lambda: self.seek(self.progress.value()))

        # Linha do tempo ocupa 60% da largura disponivel, centralizada.
        timeline_widget = QWidget()
        timeline_widget.setObjectName("karaokeTimeline")
        timeline = QHBoxLayout(timeline_widget)
        timeline.setContentsMargins(0, 0, 0, 0)
        timeline.setSpacing(8)
        self.elapsed_label = QLabel("0:00")
        self.elapsed_label.setObjectName("transportTime")
        self.elapsed_label.setFixedWidth(42)
        self.elapsed_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        timeline.addWidget(self.elapsed_label)
        timeline.addWidget(self.progress, 1)
        self.total_label = QLabel("0:00")
        self.total_label.setObjectName("transportTime")
        self.total_label.setFixedWidth(42)
        timeline.addWidget(self.total_label)
        # Uma unica linha: volume (esquerda), progresso (centro) e fechar (direita).
        # Proporcoes 2:6:2 preservam os 60% centrais da linha do tempo.
        footer_row = QHBoxLayout()
        footer_row.setContentsMargins(0, 0, 0, 0)
        footer_row.setSpacing(0)

        volume_widget = QWidget()
        volume_widget.setObjectName("karaokeFooterVolume")
        volume_layout = QHBoxLayout(volume_widget)
        volume_layout.setContentsMargins(0, 0, 8, 0)
        volume_layout.setSpacing(8)
        volume_layout.addWidget(QLabel("Volume"))
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(self.player.volume())
        self.volume.setFixedWidth(115)
        self.volume.valueChanged.connect(self.player.set_volume)
        volume_layout.addWidget(self.volume)
        self.volume_percent = QLabel(f"{self.player.volume()}%")
        self.volume_percent.setMinimumWidth(38)
        volume_layout.addWidget(self.volume_percent)
        volume_layout.addStretch(1)
        footer_row.addWidget(volume_widget, 2)

        footer_row.addWidget(timeline_widget, 6)

        close_widget = QWidget()
        close_widget.setObjectName("karaokeFooterClose")
        close_layout = QHBoxLayout(close_widget)
        close_layout.setContentsMargins(8, 0, 0, 0)
        close_layout.addStretch(1)
        close_button = QPushButton("Fechar (Esc)")
        close_button.setObjectName("karaokeClose")
        close_button.clicked.connect(self.close)
        close_layout.addWidget(close_button)
        footer_row.addWidget(close_widget, 2)
        control_layout.addLayout(footer_row)
        layout.addWidget(control_panel)

        self.setStyleSheet(self.styleSheet() + """
            QWidget#karaokeControlPanel { background-color: #000000; }
            QWidget#karaokeControlPanel QLabel { background: transparent; }
            QWidget#karaokeControlPanel QSlider { background: transparent; }
            QWidget#karaokeTimeline { background: transparent; }
            QWidget#karaokeFooterVolume, QWidget#karaokeFooterClose { background: transparent; }
            QPushButton#transportPlay {
                background: #ffffff; border: none; border-radius: 19px;
                min-width: 38px; max-width: 38px;
                min-height: 38px; max-height: 38px; padding: 0;
            }
            QPushButton#transportPlay:hover { background: #ffffff; }
            QPushButton#transportSecondary {
                background: transparent; border: none; padding: 3px;
                min-width: 40px; max-width: 40px;
                min-height: 40px; max-height: 40px;
            }
            QPushButton#transportSecondary:hover { background: transparent; }
            QLabel#transportTime { color: #c7c7c7; font-size: 12px; }
            QSlider#transportProgress::groove:horizontal {
                background: #666666; height: 4px; border-radius: 2px;
            }
            QSlider#transportProgress::sub-page:horizontal {
                background: #f4f4f4; border-radius: 2px;
            }
            QSlider#transportProgress::handle:horizontal {
                background: #ffffff; width: 10px; height: 10px;
                margin: -3px 0; border-radius: 5px;
            }
            QPushButton#karaokeClose {
                color: #dddddd; background: #1d1d1d; border: 1px solid #353535;
                border-radius: 5px; padding: 7px 12px; font-size: 12px;
            }
            QPushButton#karaokeClose:hover { background: #303030; color: #ffffff; }
        """)

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

    def apply_lyric_background(self):
        """Atualiza o fundo das letras mesmo ao voltar de uma capa para preto."""
        old = self._applied_lyric_background
        new = self.lyric_background
        self.setStyleSheet(self.styleSheet().replace(
            f"QMainWindow, QWidget {{\n                background: {old};",
            f"QMainWindow, QWidget {{\n                background: {new};",
            1,
        ))
        self._applied_lyric_background = new

    def load_cover(self):
        """Busca capa em music/cover pelo nome fisico do instrumental."""
        audio_path = self.song.get("audio_path") or ""
        if not audio_path:
            return
        cover_dir = MUSIC_DIR / "cover"
        stem = Path(audio_path).stem
        stems = [stem]
        cleaned = re.sub(r"\s*\[Instrumental\]$", "", stem, flags=re.I).strip()
        if cleaned != stem:
            stems.append(cleaned)
        if not cover_dir.is_dir():
            return
        candidates = {p.name.casefold(): p for p in cover_dir.iterdir()
                      if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")}
        for name in stems:
            for ext in (".jpg", ".jpeg", ".png", ".webp"):
                candidate = candidates.get((name + ext).casefold())
                if candidate:
                    image = QPixmap(str(candidate))
                    if not image.isNull():
                        self.cover_label.setPixmap(image.scaled(
                            220, 220, Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation))
                        self.cover_label.show()
                        self.lyric_background = cover_background_color(image)
                        self.apply_lyric_background()
                        return

    def set_song(self, song):
        """Atualiza a mesma tela quando a fila inicia outra faixa."""
        self.song = dict(song)
        self.setWindowTitle(f"Karaokê — {song['title']}")
        self.title_label.setText(song['title'])
        self.artist_label.setText(song.get('artist') or '')
        self.cover_label.clear()
        self.cover_label.hide()
        self.lyric_background = '#000000'
        self.apply_lyric_background()
        self.load_cover()
        self.load_lyrics()
        self.last_index = None
        self._stop_verse_animations()
        self.refresh(self.player.position())

    def _stop_verse_animations(self):
        for animation in self._verse_animations:
            animation.stop()
        self._verse_animations.clear()
        for widget in self._fade_widgets:
            widget.graphicsEffect().setOpacity(1.0)

    def fade_verse_transition(self):
        self._stop_verse_animations()
        for widget in self._fade_widgets:
            effect = widget.graphicsEffect()
            effect.setOpacity(0.45)
            animation = QPropertyAnimation(effect, b'opacity', self)
            animation.setDuration(360)
            animation.setStartValue(0.45)
            animation.setEndValue(1.0)
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._verse_animations.append(animation)
            animation.start()

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
        self.progress.setRange(0, max(0, milliseconds))
        self.total_label.setText(format_clock(milliseconds))

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

    def back_queue_song(self):
        main_window = self.parent()
        if main_window is not None and hasattr(main_window, "previous_or_restart_song"):
            main_window.previous_or_restart_song()
        else:
            self.seek(0)

    def skip_queue_song(self):
        main_window = self.parent()
        if main_window is not None and hasattr(main_window, "skip_song"):
            main_window.skip_song()

    def update_play_button(self, state):
        self.play_button.set_kind("pause" if state == "playing" else "play")
        self.play_button.setToolTip("Pausar (Espaço)" if state == "playing" else "Continuar (Espaço)")

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

        self.elapsed_label.setText(format_clock(milliseconds))
        self.total_label.setText(format_clock(duration))

        if not self.lines:
            for label in self.verse_labels:
                label.clear()
            self.current_label.set_karaoke_text(
                "Nenhuma letra LRC cadastrada", 0.0
            )
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

        # Dois versos anteriores e tres seguintes permanecem visiveis.
        # A primeira linha ja ocupa o foco antes do seu primeiro timestamp.
        focused = max(0, index)
        offsets = (-2, -1, 1, 2, 3)
        for label, offset in zip(self.verse_labels, offsets):
            target = focused + offset
            if 0 <= target < len(self.lines):
                label.setText(self.lines[target]["text"])
            else:
                label.setText("")
            distance = abs(offset)
            color = {1: "#b1d5b3", 2: "#81af86", 3: "#5e8b65"}[distance]
            size = {1: 26, 2: 24, 3: 22}[distance]
            label.setStyleSheet(
                f"font-size: {size}px; color: {color}; "
                "font-weight: 650; background: transparent;"
            )
        self.fade_verse_transition()

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
        self._queue_animation = None
        self._queue_active = False
        self._played_history = []
        self._changing_track = False

        self.setWindowTitle(
            "Karaokê v1.10"
        )
        self.resize(1100, 700)

        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #181818;
                color: #eeeeee;
            }

            QLineEdit, QListWidget, QTreeWidget, QTextEdit {
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

        self.player.media.mediaStatusChanged.connect(self.on_media_status_changed)
        self.refresh_library()

    def build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        root = QVBoxLayout(central)

        header = QHBoxLayout()

        title = QLabel("Karaokê v1.10")
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

        self.song_list = QTreeWidget()
        self.song_list.setHeaderLabels(["Código", "Artista", "Música"])
        self.song_list.setRootIsDecorated(False)
        self.song_list.setAlternatingRowColors(True)
        self.song_list.setUniformRowHeights(True)
        self.song_list.setColumnWidth(0, 75)
        self.song_list.setColumnWidth(1, 300)
        self.song_list.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.song_list.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.song_list.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.song_list.itemDoubleClicked.connect(self.play_selected)
        self.song_list.itemSelectionChanged.connect(
            self.selection_changed
        )
        library_layout.addWidget(
            self.song_list
        )

        library_buttons = QHBoxLayout()

        youtube_button = QPushButton(
            "Baixar música + separar"
        )
        youtube_button.clicked.connect(
            self.download_from_youtube
        )
        library_buttons.addWidget(youtube_button)

        edit_button = QPushButton("Renomear")
        edit_button.clicked.connect(self.edit_selected_metadata)
        library_buttons.addWidget(edit_button)

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
            self.stop_queue_playback
        )
        controls.addWidget(
            stop_button
        )

        skip_button = QPushButton("Skip")
        skip_button.clicked.connect(self.skip_song)
        controls.addWidget(skip_button)

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

        queue_buttons = QHBoxLayout()
        remove_from_queue = QPushButton("Remover selecionada")
        remove_from_queue.clicked.connect(self.remove_selected_from_queue)
        queue_buttons.addWidget(remove_from_queue)

        clear_queue = QPushButton("Limpar fila")
        clear_queue.clicked.connect(self.confirm_clear_queue)
        queue_buttons.addWidget(clear_queue)
        right_layout.addLayout(queue_buttons)

        right_layout.addStretch()

        splitter.addWidget(right)

        splitter.setSizes([600, 500])

        root.addWidget(splitter)

    def refresh_library(self, selected_id=None):
        current = self.song_list.currentItem()
        if selected_id is None and current:
            selected_id = current.data(0, Qt.ItemDataRole.UserRole)["id"]

        self.song_list.clear()
        for song in list_songs(self.search.text()):
            item = QTreeWidgetItem([
                song['code'] or '', song['artist'] or '', song['title']
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, dict(song))
            self.song_list.addTopLevelItem(item)
            if selected_id is not None and song['id'] == selected_id:
                self.song_list.setCurrentItem(item)

    def selection_changed(self):
        item = self.song_list.currentItem()

        if not item:
            return

        song = item.data(0, Qt.ItemDataRole.UserRole)

        # Selecionar na biblioteca não deve substituir a música em execução.
        # A música é incluída na fila somente por clique do usuário.

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
            str(ORIGINAL_DIR),
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
                lyrics_destination = str(LYRIC_DIR / (Path(destination).stem + ".lrc"))
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
                if source_path.resolve().parent == ORIGINAL_DIR.resolve():
                    destination = str(source_path.resolve())
                else:
                    shared_candidate = ORIGINAL_DIR / source_path.name
                    if shared_candidate.exists() and get_song_by_audio_path(str(shared_candidate)):
                        skipped += 1
                        continue
                    destination = (
                        str(shared_candidate)
                        if not shared_candidate.exists()
                        else unique_destination(str(ORIGINAL_DIR), source_path.name)
                    )
                    shutil.copy2(source_path, destination)

                if get_song_by_audio_path(destination):
                    skipped += 1
                    continue

                source_lrc = matching_lrc_path(source_path)
                lyrics_destination = ""
                if source_lrc:
                    if Path(destination).resolve().parent == ORIGINAL_DIR.resolve() and source_path.resolve().parent == ORIGINAL_DIR.resolve():
                        lyrics_destination = str(source_lrc.resolve())
                    else:
                        lyrics_destination = str(LYRIC_DIR / (Path(destination).stem + ".lrc"))
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
            ORIGINAL_DIR,
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

    def ask_song_metadata(self, title, artist):
        dialog = QDialog(self)
        dialog.setWindowTitle("Confirmar nome da música")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Defina como a música aparecerá na biblioteca (independente do nome do link):"))
        title_input = QLineEdit(title)
        artist_input = QLineEdit(artist)
        layout.addWidget(QLabel("Nome da música:"))
        layout.addWidget(title_input)
        layout.addWidget(QLabel("Artista:"))
        layout.addWidget(artist_input)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        while dialog.exec() == QDialog.DialogCode.Accepted:
            if title_input.text().strip():
                return title_input.text().strip(), artist_input.text().strip()
            QMessageBox.warning(dialog, "Título obrigatório", "Informe o nome da música.")
        return None

    def edit_selected_metadata(self):
        song = self.get_selected_song()
        if not song:
            return
        metadata = self.ask_song_metadata(song['title'], song['artist'])
        if metadata is not None:
            update_song_metadata(song['id'], *metadata)
            for i in range(self.queue_list.count()):
                item = self.queue_list.item(i)
                queued_song = item.data(Qt.ItemDataRole.UserRole)
                if queued_song and queued_song['id'] == song['id']:
                    title, artist = metadata
                    queued_song.update(title=title, artist=artist)
                    item.setData(Qt.ItemDataRole.UserRole, queued_song)
                    item.setText(f"{title} — {artist}" if artist else title)
            if self.current_song and self.current_song.get('id') == song['id']:
                self.current_song['title'], self.current_song['artist'] = metadata
                self.now_playing.setText(f"{metadata[0]} — {metadata[1]}" if metadata[1] else metadata[0])
            self.refresh_library(song['id'])

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

        metadata = self.ask_song_metadata(title or Path(audio_path).stem, artist or "")
        if metadata is None:
            QMessageBox.information(self, "Download concluído", "O áudio foi salvo na pasta original, mas não foi cadastrado.")
            return
        title, artist = metadata
        song_id = add_song(
            title,
            artist,
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
        output_path = str(LYRIC_DIR / (Path(instrumental_path).stem + ".lrc"))

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

        return item.data(0, Qt.ItemDataRole.UserRole)

    def play_selected(self, item=None):
        # Somente o duplo clique na biblioteca adiciona uma faixa à fila.
        song = item.data(0, Qt.ItemDataRole.UserRole) if isinstance(item, QTreeWidgetItem) else self.get_selected_song()
        if not song:
            return
        if not os.path.isfile(song["audio_path"]):
            QMessageBox.warning(self, "Arquivo não encontrado", f"O arquivo não existe:\n{song['audio_path']}")
            return

        queue_item = QListWidgetItem(
            f"{song['title']} — {song['artist']}" if song['artist'] else song['title']
        )
        # Guardamos o cadastro completo para reproduzir mesmo se houver busca/filtro.
        queue_item.setData(Qt.ItemDataRole.UserRole, dict(song))
        self.queue_list.addItem(queue_item)
        if not self._queue_active:
            self.start_queue_head()

    def start_queue_head(self):
        if self.queue_list.count() == 0:
            self._queue_active = False
            self.current_song = None
            self.now_playing.setText("Nenhuma música em reprodução")
            self.player.stop()
            for window in list(self.karaoke_windows):
                window.close()
            return

        item = self.queue_list.item(0)
        song = item.data(Qt.ItemDataRole.UserRole)
        if not os.path.isfile(song["audio_path"]):
            self.queue_list.takeItem(0)
            QMessageBox.warning(self, "Arquivo não encontrado", f"O arquivo não existe:\n{song['audio_path']}")
            self.start_queue_head()
            return

        self._changing_track = True
        self._queue_active = True
        self.current_song = dict(song)
        self.now_playing.setText(
            f"{song['title']} — {song['artist']}" if song['artist'] else song['title']
        )
        # Parar antes de trocar a fonte evita reproduzir simultaneamente.
        self.player.stop()
        self.player.load(song["audio_path"])
        self.player.set_position(0)
        for window in list(self.karaoke_windows):
            window.set_song(song)
        self.player.play()
        self._changing_track = False
        self.animate_queue_highlight()

    def animate_queue_highlight(self):
        # A primeira entrada tem fundo levemente mais claro e borda lateral discreta.
        if self._queue_animation is not None:
            self._queue_animation.stop()
            self._queue_animation = None
        for index in range(self.queue_list.count()):
            item = self.queue_list.item(index)
            item.setBackground(QBrush(QColor("#222222")))
            item.setForeground(QBrush(QColor("#eeeeee")))
        if self.queue_list.count() == 0 or not self._queue_active:
            return
        head = self.queue_list.item(0)
        head.setForeground(QBrush(QColor("#f3f3f3")))
        animation = QVariantAnimation(self)
        animation.setStartValue(QColor("#222222"))
        animation.setEndValue(QColor("#354338"))
        animation.setDuration(450)
        animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        animation.valueChanged.connect(lambda color, active=head: active.setBackground(QBrush(color))
                                       if self.queue_list.count() and self.queue_list.item(0) is active else None)
        self._queue_animation = animation
        animation.start()

    def previous_or_restart_song(self):
        # Ao ultrapassar 2 segundos, o botao sempre reinicia a faixa atual.
        if self.player.position() > 2000 or not self._played_history:
            self.player.set_position(0)
            return

        # Ate 2 segundos, recupera a ultima faixa tocada sem apagar a atual.
        previous = self._played_history.pop()
        if not os.path.isfile(previous["audio_path"]):
            QMessageBox.warning(self, "Arquivo nao encontrado",
                                f"O arquivo anterior nao existe:\n{previous['audio_path']}")
            self.player.set_position(0)
            return
        label = (f"{previous['title']} — {previous['artist']}"
                 if previous.get('artist') else previous['title'])
        item = QListWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, dict(previous))
        self.queue_list.insertItem(0, item)
        self.start_queue_head()

    def advance_queue(self):
        if not self._queue_active:
            return
        self._queue_active = False
        self._changing_track = True
        self.player.stop()
        if self.queue_list.count():
            finished = self.queue_list.takeItem(0)
            song = finished.data(Qt.ItemDataRole.UserRole)
            if song:
                self._played_history.append(dict(song))
        self._changing_track = False
        self.start_queue_head()

    def on_media_status_changed(self, status):
        if (status == QMediaPlayer.MediaStatus.EndOfMedia
                and self._queue_active and not self._changing_track):
            self.advance_queue()

    def skip_song(self):
        if self._queue_active:
            self.advance_queue()
        elif self.queue_list.count():
            self.queue_list.takeItem(0)
            self.start_queue_head()

    def toggle_queue_playback(self):
        if self._queue_active:
            self.player.toggle()
        elif self.queue_list.count():
            self.start_queue_head()
        else:
            self.play_selected()

    def stop_queue_playback(self):
        # Stop interrompe o áudio; Play retoma a primeira música da fila.
        self._changing_track = True
        self.player.stop()
        self._changing_track = False
        self._queue_active = False
        self.animate_queue_highlight()

    def remove_selected_from_queue(self):
        row = self.queue_list.currentRow()
        if row < 0:
            QMessageBox.information(self, "Fila", "Selecione uma música da fila para remover.")
            return
        if row == 0 and self._queue_active:
            self.advance_queue()
        else:
            self.queue_list.takeItem(row)
            if row == 0 and self.queue_list.count():
                self.start_queue_head()
            elif not self.queue_list.count():
                self.current_song = None
                self.now_playing.setText("Nenhuma música em reprodução")
            self.animate_queue_highlight()

    def confirm_clear_queue(self):
        count = self.queue_list.count()
        if count == 0:
            return
        if count > 1:
            response = QMessageBox.question(
                self, "Limpar fila", "Realmente deseja limpar toda a fila?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if response != QMessageBox.StandardButton.Yes:
                return
        self._changing_track = True
        self._queue_active = False
        self.player.stop()
        self.queue_list.clear()
        self._played_history.clear()
        for window in list(self.karaoke_windows):
            window.close()
        self.current_song = None
        self.now_playing.setText("Nenhuma música em reprodução")
        self._changing_track = False
        self.animate_queue_highlight()

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
        # A tela do karaokê sempre acompanha a fila, nunca o item selecionado
        # na biblioteca. Abrir a tela não reinicia a música em andamento.
        if self.queue_list.count() == 0:
            QMessageBox.information(
                self, "Fila vazia",
                "Adicione músicas à fila com duplo clique antes de abrir o karaokê."
            )
            return

        if not self._queue_active:
            self.start_queue_head()
        song = self.current_song
        if song is None:
            return

        if self.karaoke_windows:
            window = self.karaoke_windows[0]
            window.showFullScreen()
            window.raise_()
            window.activateWindow()
            return

        window = KaraokeWindow(song, self.player, self)
        self.karaoke_windows.append(window)

        def remove_window():
            if window in self.karaoke_windows:
                self.karaoke_windows.remove(window)

        window.destroyed.connect(remove_window)

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
