import os

from PySide6.QtCore import QObject, Signal, QUrl
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput


class Player(QObject):
    DEFAULT_VOLUME = 50

    position_changed = Signal(int)
    duration_changed = Signal(int)
    state_changed = Signal(str)
    volume_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.audio_output = QAudioOutput()
        self.audio_output.setVolume(self.DEFAULT_VOLUME / 100.0)

        self.media = QMediaPlayer()
        self.media.setAudioOutput(self.audio_output)

        self.current_path = None

        self.media.positionChanged.connect(self.position_changed.emit)
        self.media.durationChanged.connect(self.duration_changed.emit)
        self.media.playbackStateChanged.connect(self._on_state_changed)

    def load(self, path):
        normalized_path = os.path.normcase(os.path.abspath(path))

        if self.current_path == normalized_path:
            return False

        self.current_path = normalized_path
        self.set_volume(self.DEFAULT_VOLUME)
        self.media.setSource(QUrl.fromLocalFile(os.path.abspath(path)))
        return True

    def is_loaded(self, path):
        if not path or not self.current_path:
            return False

        return self.current_path == os.path.normcase(os.path.abspath(path))

    def position(self):
        return self.media.position()

    def play(self):
        self.media.play()

    def pause(self):
        self.media.pause()

    def stop(self):
        self.media.stop()
        self.media.setPosition(0)

    def toggle(self):
        if self.media.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.pause()
        else:
            self.play()

    def set_position(self, milliseconds):
        self.media.setPosition(max(0, int(milliseconds)))

    def volume(self):
        return round(self.audio_output.volume() * 100)

    def set_volume(self, value):
        value = max(0, min(100, int(value)))
        self.audio_output.setVolume(value / 100.0)
        self.volume_changed.emit(value)

    def _on_state_changed(self, state):
        names = {
            QMediaPlayer.PlaybackState.PlayingState: "playing",
            QMediaPlayer.PlaybackState.PausedState: "paused",
            QMediaPlayer.PlaybackState.StoppedState: "stopped",
        }
        self.state_changed.emit(names.get(state, "unknown"))
