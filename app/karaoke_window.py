"""Tela do karaokê: letra em tela cheia sobre a cor da capa do álbum."""
import time
from bisect import bisect_right
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QPushButton, QVBoxLayout, QWidget

from .covers import COVER_FALLBACK_BACKGROUND, cover_background_for, find_cover_path
from .lrc import parse_lrc
from .lyrics_view import LyricsView
from .score_overlay import ScoreOverlay
from .scoring import SingingScorer


class SolidBackground(QWidget):
    """Fundo da tela do karaokê: a cor tirada da capa do álbum (ou preto)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self._base_color = QColor("#000000")

    def set_base_color(self, color):
        self._base_color = QColor(color)
        self.update()

    def paintEvent(self, event):
        QPainter(self).fillRect(self.rect(), self._base_color)


class KaraokeWindow(QMainWindow):
    play_requested = Signal(int)   # posição (ms) a partir da qual tocar

    def __init__(self, song, player, parent=None):
        super().__init__(parent)

        self.song = song
        self.player = player
        self.lines = []
        self._line_times = []
        self.karaoke_timer = QTimer(self)

        self.setWindowTitle(
            f"Karaokê — {song['title']}"
        )

        self.lyric_background = COVER_FALLBACK_BACKGROUND
        self.scorer = SingingScorer(player, self)
        self._score_done = None
        self.setStyleSheet(
            """
            QMainWindow {
                background: #000000;
            }

            QWidget {
                background: transparent;
                color: #ffffff;
            }

            QPushButton {
                padding: 10px 18px;
                font-size: 15px;
            }
            """
        )

        # O widget raiz desenha o fundo na cor da capa do álbum.
        root = SolidBackground()
        self.background = root
        self.setCentralWidget(root)

        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 20, 0, 0)

        # Letra rolando linha a linha; a capa (opcional), o nome da música e o do
        # artista ficam na margem esquerda.
        self.lyrics_view = LyricsView()
        self.lyrics_view.set_song_info(song["title"], song.get("artist"))
        self.lyrics_view.line_clicked.connect(self.seek_to_line)
        self.lyrics_view.cover_clicked.connect(self.toggle_playback)
        layout.addWidget(self.lyrics_view, 1)
        self._seek_target = None
        self._seek_deadline = 0.0
        self.load_cover()

        # Rodape: somente o botao de fechar, alinhado a direita.
        control_panel = QWidget()
        control_panel.setObjectName("karaokeControlPanel")
        control_layout = QHBoxLayout(control_panel)
        control_layout.setContentsMargins(16, 19, 16, 9)
        control_layout.addStretch(1)
        close_button = QPushButton("Fechar (Esc)")
        close_button.setObjectName("karaokeClose")
        close_button.clicked.connect(self.close)
        control_layout.addWidget(close_button)
        layout.addWidget(control_panel)

        self.setStyleSheet(self.styleSheet() + """
            QPushButton#karaokeClose {
                color: #dddddd; background: #1d1d1d; border: 1px solid #353535;
                border-radius: 5px; padding: 7px 12px; font-size: 12px;
            }
            QPushButton#karaokeClose:hover { background: #303030; color: #ffffff; }
        """)

        self.load_lyrics()
        self.scorer.start(self.song, self.lines)

        # Nota revelada no fim da música, por cima da letra.
        self.score_overlay = ScoreOverlay(root)
        self.score_overlay.finished.connect(self._on_score_finished)

        self.karaoke_timer.setInterval(40)
        self.karaoke_timer.timeout.connect(
            lambda: self.refresh(self.player.position())
        )
        self.karaoke_timer.start()

        self.setAttribute(
            Qt.WidgetAttribute.WA_DeleteOnClose
        )

        self.showFullScreen()

        self.refresh(
            self.player.media.position()
        )

    def apply_lyric_background(self):
        """Cor de fundo (tirada da capa) aplicada à tela e às cores da letra."""
        self.background.set_base_color(self.lyric_background)
        self.lyrics_view.set_background(self.lyric_background)

    def load_cover(self):
        """Capa do álbum (music/cover/<Artista> - <Álbum>/), que também define as cores."""
        path = find_cover_path(self.song)
        image, self.lyric_background = cover_background_for(path) if path else (None, COVER_FALLBACK_BACKGROUND)
        self.lyrics_view.set_cover(image)
        self.apply_lyric_background()

    def set_song(self, song):
        """Atualiza a mesma tela quando a fila inicia outra faixa."""
        self.cancel_score()
        self.song = dict(song)
        self.setWindowTitle(f"Karaokê — {song['title']}")
        self.lyrics_view.set_song_info(song["title"], song.get("artist"))
        self.load_cover()
        self.load_lyrics()
        self.scorer.start(self.song, self.lines)
        self.refresh(self.player.position())

    # --- nota --------------------------------------------------------------------
    def show_score(self, on_done, upcoming=None):
        """Mostra a nota da música que terminou; `on_done` é chamado ao fechar a nota.

        `upcoming` devolve a próxima música da fila, anunciada com uma contagem.
        """
        self._score_done = on_done
        self.score_overlay.start(self.scorer.final_score(), upcoming)

    def cancel_score(self):
        self._score_done = None
        self.score_overlay.cancel()

    def _on_score_finished(self):
        callback, self._score_done = self._score_done, None
        if callback is not None:
            callback()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.score_overlay.isVisible():
            self.score_overlay.setGeometry(self.centralWidget().rect())

    def closeEvent(self, event):
        self.scorer.stop()
        self.karaoke_timer.stop()
        if self.score_overlay.active:
            self.score_overlay.cancel()
            # Fechar a tela durante a nota segue a fila normalmente.
            callback, self._score_done = self._score_done, None
            if callback is not None:
                QTimer.singleShot(0, callback)
        super().closeEvent(event)

    def load_lyrics(self):
        path = self.song["lyrics_path"]
        self.lines = []
        if path:
            try:
                self.lines = parse_lrc(Path(path).read_text(encoding="utf-8"))
            except OSError:
                pass
        self._line_times = [line["time"] for line in self.lines]

        if self.lines:
            self.lyrics_view.set_lines([line["text"] for line in self.lines])
        else:
            self.lyrics_view.set_lines(["Nenhuma letra LRC cadastrada"], clickable=False)
            self.lyrics_view.set_active(0)

    def seek_to_line(self, index):
        if not 0 <= index < len(self.lines):
            return
        target = self.lines[index]["time"]
        self.player.set_position(target)
        if not self.player.is_playing():
            # Toca a partir da linha clicada mesmo se a música estiver pausada.
            self.play_requested.emit(target)
        # O player demora alguns instantes para informar a nova posição; até lá,
        # posições antigas são ignoradas para a letra não voltar e avançar de novo.
        self._seek_target = target
        self._seek_deadline = time.perf_counter() + 1.0
        self.lyrics_view.set_active(index)

    def toggle_playback(self):
        """Clique na capa: pausa ou continua (depois de um Stop, retoma a fila)."""
        if self.player.is_playing():
            self.player.pause()
        else:
            self.play_requested.emit(self.player.position())

    def refresh(self, milliseconds):
        if not self.lines:
            return
        if self._seek_target is not None:
            if abs(milliseconds - self._seek_target) > 1500 and time.perf_counter() < self._seek_deadline:
                return
            self._seek_target = None
        # Somente o tempo de início de cada linha é usado; os tempos por palavra
        # que o LRC possa ter são ignorados na exibição.
        self.lyrics_view.set_active(bisect_right(self._line_times, milliseconds) - 1)

    def keyPressEvent(self, event):
        if self.score_overlay.active:
            self.score_overlay.keyPressEvent(event)
            return

        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return

        if event.key() == Qt.Key.Key_Space:
            self.player.toggle()
            return

        super().keyPressEvent(event)
