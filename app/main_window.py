"""Janela principal: biblioteca, fila de reprodução, player e downloads."""
import re
import shutil
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QSignalBlocker, Qt, QVariantAnimation
from PySide6.QtGui import QBrush, QColor
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .album_art import AlbumCoverWorker
from .database import (
    INSTRUMENTAL_DIR,
    ORIGINAL_DIR,
    add_song,
    get_song,
    get_song_by_audio_path,
    update_lyrics_path,
    update_song_metadata,
)
from .karaoke_window import KaraokeWindow
from .library_panel import LibraryPanel
from .lyrics_editor import LyricsEditor
from .player import Player
from .preparation import OnlineLyricsWorker
from .tasks import run_task
from .workflows import PreparationFlow, alignment_quality, lyrics_path_for
from .youtube_download import YouTubeDownloadWorker

STYLE = """
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


def format_ms(milliseconds):
    total_seconds = max(0, int(milliseconds)) // 1000
    return f"{total_seconds // 60:02d}:{total_seconds % 60:02d}"


def song_label(song):
    return f"{song['title']} — {song['artist']}" if song.get("artist") else song["title"]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.player = Player(self)
        self.current_song = None
        self.karaoke_windows = []
        self._queue_animation = None
        self._queue_active = False
        self._changing_track = False
        self._download = {}

        self.setWindowTitle("Karaokê v1.10")
        self.resize(1100, 700)
        self.setStyleSheet(STYLE)

        self._build_ui()
        self.player.position_changed.connect(self.update_progress)
        self.player.duration_changed.connect(self.update_duration)
        self.player.state_changed.connect(self.update_play_button)
        self.player.volume_changed.connect(self.update_volume_display)
        self.player.media.mediaStatusChanged.connect(self.on_media_status_changed)
        self.library.reload()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        header = QHBoxLayout()
        title = QLabel("Karaokê v1.10")
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        header.addWidget(title)
        header.addStretch()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar título, artista ou álbum...")
        self.search.setMaximumWidth(350)
        header.addWidget(self.search)
        root.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Biblioteca
        library_widget = QWidget()
        library_layout = QVBoxLayout(library_widget)
        self.library = LibraryPanel()
        self.library.song_activated.connect(self.add_to_queue)
        self.search.textChanged.connect(self.library.set_filter)
        library_layout.addWidget(self.library)

        library_buttons = QHBoxLayout()
        for text, tooltip, slot in (
            ("Baixar música",
             "Baixa uma versão que já é instrumental (karaokê), sem separar voz e instrumental. "
             "O áudio vai para music/original e music/instrumental.",
             lambda: self.download_from_youtube(separate=False)),
            ("Baixar música + separar", "", lambda: self.download_from_youtube(separate=True)),
            ("Renomear", "", self.edit_selected_metadata),
        ):
            button = QPushButton(text)
            button.setToolTip(tooltip)
            button.clicked.connect(slot)
            library_buttons.addWidget(button)
        library_layout.addLayout(library_buttons)
        splitter.addWidget(library_widget)

        # Painel direito
        right = QWidget()
        right_layout = QVBoxLayout(right)

        self.now_playing = QLabel("Nenhuma música selecionada")
        self.now_playing.setObjectName("nowPlaying")
        self.now_playing.setWordWrap(True)
        right_layout.addWidget(self.now_playing)

        controls = QHBoxLayout()
        self.play_button = QPushButton("Play")
        self.play_button.clicked.connect(self.toggle_queue_playback)
        controls.addWidget(self.play_button)
        for text, slot in (
            ("Stop", self.stop_queue_playback),
            ("Skip", self.skip_song),
            ("Abrir karaokê", self.open_karaoke),
            ("Editar letra", self.edit_lyrics),
        ):
            button = QPushButton(text)
            button.clicked.connect(slot)
            controls.addWidget(button)
        right_layout.addLayout(controls)

        self.progress = QSlider(Qt.Orientation.Horizontal)
        self.progress.setRange(0, 0)
        self.progress.sliderMoved.connect(self.player.set_position)
        right_layout.addWidget(self.progress)

        self.time_label = QLabel("00:00 / 00:00")
        right_layout.addWidget(self.time_label)

        volume_row = QHBoxLayout()
        volume_row.addWidget(QLabel("Volume"))
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(self.player.volume())
        self.volume.valueChanged.connect(self.player.set_volume)
        volume_row.addWidget(self.volume)
        self.volume_percent = QLabel(f"{self.player.volume()}%")
        self.volume_percent.setMinimumWidth(42)
        volume_row.addWidget(self.volume_percent)
        right_layout.addLayout(volume_row)

        queue_label = QLabel("Fila")
        queue_label.setStyleSheet("font-size: 18px; font-weight: bold;")
        right_layout.addWidget(queue_label)
        self.queue_list = QListWidget()
        right_layout.addWidget(self.queue_list)

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

    # --- biblioteca e cadastro -----------------------------------------------
    def get_selected_song(self):
        song = self.library.selected_song()
        if not song:
            QMessageBox.information(self, "Selecione uma música", "Selecione uma música na biblioteca primeiro.")
        return song

    def ask_song_metadata(self, title, artist, album=""):
        dialog = QDialog(self)
        dialog.setWindowTitle("Confirmar nome da música")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Defina como a música aparecerá na biblioteca (independente do nome do link):"))
        title_input = QLineEdit(title)
        artist_input = QLineEdit(artist)
        album_input = QLineEdit(album or "")
        album_input.setPlaceholderText("Cria a pasta music/cover/<Artista> - <Álbum> e baixa a capa ao baixar a música")
        for label, field in (("Nome da música:", title_input), ("Artista:", artist_input), ("Álbum:", album_input)):
            layout.addWidget(QLabel(label))
            layout.addWidget(field)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        while dialog.exec() == QDialog.DialogCode.Accepted:
            if title_input.text().strip():
                return title_input.text().strip(), artist_input.text().strip(), album_input.text().strip()
            QMessageBox.warning(dialog, "Título obrigatório", "Informe o nome da música.")
        return None

    def edit_selected_metadata(self):
        song = self.get_selected_song()
        if not song:
            return
        metadata = self.ask_song_metadata(song["title"], song["artist"], song.get("album") or "")
        if metadata is None:
            return
        update_song_metadata(song["id"], *metadata)
        title, artist, album = metadata
        for index in range(self.queue_list.count()):
            item = self.queue_list.item(index)
            queued_song = item.data(Qt.ItemDataRole.UserRole)
            if queued_song and queued_song["id"] == song["id"]:
                queued_song.update(title=title, artist=artist, album=album)
                item.setData(Qt.ItemDataRole.UserRole, queued_song)
                item.setText(song_label(queued_song))
        if self.current_song and self.current_song.get("id") == song["id"]:
            self.current_song.update(title=title, artist=artist, album=album)
            self.now_playing.setText(song_label(self.current_song))
        # O reload também cria a pasta de capa do álbum (music/cover/<Artista> - <Álbum>).
        self.library.reload(song["id"])

    def _register_song(self, audio_path, source_path, title, artist, album):
        song_id = add_song(
            title,
            artist,
            audio_path,
            "",
            source_audio_path=source_path,
            source_url=self._download.get("url", ""),
            album=album,
        )
        self.library.reload(song_id)
        return get_song(song_id)

    # --- downloads -----------------------------------------------------------
    def download_from_youtube(self, separate=True):
        url, ok = QInputDialog.getText(
            self,
            "Baixar do YouTube" if separate else "Baixar música instrumental",
            "Cole o link do vídeo do YouTube:" if separate
            else "Cole o link do vídeo com a versão instrumental (karaokê):",
        )
        url = url.strip()
        if not ok or not url:
            return
        if not re.match(r"^https?://", url, re.IGNORECASE):
            QMessageBox.warning(self, "Link inválido", "Informe um link completo começando com http:// ou https://.")
            return

        self._download = {"url": url, "separate": separate}
        run_task(
            self,
            YouTubeDownloadWorker(url, ORIGINAL_DIR),
            self._on_downloaded,
            lambda message: QMessageBox.critical(self, "Erro no download do YouTube", message),
            title="Baixar do YouTube",
            text="Preparando download...",
        )

    def _on_downloaded(self, title, artist, audio_path):
        if get_song_by_audio_path(audio_path):
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
        _title, artist, album = metadata
        if album:
            self._download_album_cover(artist, album)
        if self._download.get("separate", True):
            self._separate_downloaded(audio_path, *metadata)
        else:
            self._register_instrumental_download(audio_path, *metadata)

    def _download_album_cover(self, artist, album):
        """Baixa a capa para music/cover/<Artista> - <Álbum>/ em segundo plano, sem diálogo."""
        self.statusBar().showMessage(f"Procurando a capa de {artist} - {album}...")
        run_task(
            self,
            AlbumCoverWorker(artist, album),
            lambda path: self._on_album_cover_downloaded(artist, album, path),
            lambda message: self.statusBar().showMessage(f"Capa não baixada: {message}", 15000),
        )

    def _on_album_cover_downloaded(self, artist, album, _path):
        self.statusBar().showMessage(f"Capa do álbum {artist} - {album} salva.", 10000)
        self.library.reload()
        for window in self.karaoke_windows:
            window.load_cover()

    def _separate_downloaded(self, audio_path, title, artist, album):
        """Música original com voz: separa, pede a letra e sincroniza com o vocal."""
        song = self._register_song(audio_path, audio_path, title, artist, album)
        flow = PreparationFlow(song, self)
        flow.stems_ready.connect(lambda *_: self.library.reload(song["id"]))
        flow.lyrics_synced.connect(self._on_downloaded_song_synced)
        flow.lyrics_skipped.connect(lambda: QMessageBox.information(
            self,
            "Música separada",
            "Download, vocal e instrumental estão prontos. A letra ficou pendente. "
            "Abra o Editor de letra quando quiser continuar.",
        ))
        flow.separation_failed.connect(lambda message: QMessageBox.critical(
            self,
            "Download concluído, mas a separação falhou",
            "A música original foi mantida, então você não precisa baixá-la novamente.\n\n"
            f"Erro durante a separação:\n{message}\n\n"
            "Abra o Editor de letra e tente preparar voz/instrumental novamente.",
        ))
        flow.sync_failed.connect(lambda message: QMessageBox.critical(
            self,
            "Não foi possível sincronizar a letra",
            f"A música, o vocal e o instrumental foram mantidos.\n\n{message}\n\n"
            "Abra o Editor de letra para tentar novamente sem baixar a música outra vez.",
        ))
        for finished in (flow.lyrics_synced, flow.lyrics_skipped, flow.separation_failed, flow.sync_failed):
            finished.connect(flow.deleteLater)
        flow.separate()

    def _on_downloaded_song_synced(self, _lyrics_path, confidence):
        self.library.reload()
        QMessageBox.information(
            self,
            "Karaokê pronto",
            "Processo concluído.\n\n"
            "1. música original mantida\n"
            "2. vocal separado\n"
            "3. instrumental gerado\n"
            "4. sua letra foi preservada\n"
            "5. os tempos foram sincronizados com o vocal\n\n"
            f"{alignment_quality(confidence)}",
        )

    def _register_instrumental_download(self, original_path, title, artist, album):
        """Versão já instrumental: cópia em music/instrumental, sem separar a voz."""
        original = Path(original_path)
        try:
            INSTRUMENTAL_DIR.mkdir(parents=True, exist_ok=True)
            instrumental = INSTRUMENTAL_DIR / f"{original.stem} [Instrumental]{original.suffix}"
            if not instrumental.exists():
                shutil.copy2(original, instrumental)
        except OSError as error:
            QMessageBox.critical(
                self,
                "Erro ao salvar o instrumental",
                f"O áudio foi baixado em music/original, mas não pôde ser copiado para music/instrumental:\n{error}",
            )
            return

        song = self._register_song(str(instrumental.resolve()), str(original.resolve()), title, artist, album)
        # Sem vocal separado não há como sincronizar uma letra colada; procura uma
        # letra já sincronizada na internet.
        run_task(
            self,
            OnlineLyricsWorker(artist, title, song["audio_path"], None),
            lambda content, source, _offset, _checked, text_note:
                self._on_instrumental_lyrics_found(song, content, source, text_note),
            lambda message: self._on_instrumental_lyrics_failed(song, message),
            title="Buscar letra sincronizada",
            text="Procurando letra sincronizada no LRCLIB e no NetEase...",
            busy=True,
        )

    def _on_instrumental_lyrics_found(self, song, content, source, text_note):
        lyrics_path = lyrics_path_for(song["audio_path"])
        try:
            lyrics_path.parent.mkdir(parents=True, exist_ok=True)
            lyrics_path.write_text(content, encoding="utf-8")
        except OSError as error:
            self._on_instrumental_lyrics_failed(song, f"A letra foi encontrada, mas não pôde ser salva:\n{error}")
            return
        update_lyrics_path(song["id"], str(lyrics_path.resolve()))
        self.library.reload(song["id"])
        QMessageBox.information(
            self,
            "Karaokê pronto",
            f"A música foi baixada e a letra sincronizada foi encontrada no {source}.\n\n"
            f"{text_note}\n\n"
            "Como a versão é instrumental, não há voz para conferir os tempos automaticamente. "
            "Se a letra estiver adiantada ou atrasada, ajuste no Editor de letra.",
        )

    def _on_instrumental_lyrics_failed(self, song, message):
        answer = QMessageBox.question(
            self,
            "Letra não encontrada",
            f"A música foi baixada em music/original e music/instrumental.\n\n{message}\n\n"
            "Ela só aparece na biblioteca depois de ter uma letra. Abrir o Editor de letra agora "
            "para colar uma letra sincronizada (.lrc)?",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.open_lyrics_editor(get_song(song["id"]) or song)

    # --- fila ----------------------------------------------------------------
    def add_to_queue(self, song):
        # Somente o duplo clique na biblioteca adiciona uma faixa à fila.
        if not song:
            return
        if not Path(song["audio_path"]).is_file():
            QMessageBox.warning(self, "Arquivo não encontrado", f"O arquivo não existe:\n{song['audio_path']}")
            return
        item = QListWidgetItem(song_label(song))
        # Guardamos o cadastro completo para reproduzir mesmo se houver busca/filtro.
        item.setData(Qt.ItemDataRole.UserRole, dict(song))
        self.queue_list.addItem(item)
        if not self._queue_active:
            self.start_queue_head()

    def _set_now_playing(self, song):
        self.current_song = dict(song) if song else None
        self.now_playing.setText(song_label(song) if song else "Nenhuma música em reprodução")

    def start_queue_head(self):
        if self.queue_list.count() == 0:
            self._queue_active = False
            self._set_now_playing(None)
            self.player.stop()
            self._close_karaoke_windows()
            return

        song = self.queue_list.item(0).data(Qt.ItemDataRole.UserRole)
        if not Path(song["audio_path"]).is_file():
            self.queue_list.takeItem(0)
            QMessageBox.warning(self, "Arquivo não encontrado", f"O arquivo não existe:\n{song['audio_path']}")
            self.start_queue_head()
            return

        self._changing_track = True
        self._queue_active = True
        self._set_now_playing(song)
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
        # A primeira entrada tem fundo levemente mais claro.
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
        animation.valueChanged.connect(
            lambda color: head.setBackground(QBrush(color))
            if self.queue_list.count() and self.queue_list.item(0) is head else None
        )
        self._queue_animation = animation
        animation.start()

    def advance_queue(self):
        if not self._queue_active:
            return
        self._queue_active = False
        self._changing_track = True
        self.player.stop()
        if self.queue_list.count():
            self.queue_list.takeItem(0)
        self._changing_track = False
        self.start_queue_head()

    def on_media_status_changed(self, status):
        if (status == QMediaPlayer.MediaStatus.EndOfMedia
                and self._queue_active and not self._changing_track):
            # Com a tela do karaokê aberta, a nota aparece antes da próxima música.
            if self.karaoke_windows:
                self.karaoke_windows[0].show_score(self.advance_queue, self._upcoming_song)
                return
            self.advance_queue()

    def _upcoming_song(self):
        """Música que vem depois da atual na fila (a atual é sempre a primeira)."""
        if self.queue_list.count() < 2:
            return None
        return self.queue_list.item(1).data(Qt.ItemDataRole.UserRole)

    def skip_song(self):
        if self._queue_active:
            self.advance_queue()
        elif self.queue_list.count():
            self.queue_list.takeItem(0)
            self.start_queue_head()

    def toggle_queue_playback(self):
        # Play/Pause; depois de um Stop, recomeça a primeira música da fila.
        if self._queue_active:
            self.player.toggle()
        elif self.queue_list.count():
            self.start_queue_head()
        else:
            self.add_to_queue(self.get_selected_song())

    def stop_queue_playback(self):
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
            return
        self.queue_list.takeItem(row)
        if row == 0 and self.queue_list.count():
            self.start_queue_head()
        elif not self.queue_list.count():
            self._set_now_playing(None)
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
        self._close_karaoke_windows()
        self._set_now_playing(None)
        self._changing_track = False
        self.animate_queue_highlight()

    # --- editor e karaokê ----------------------------------------------------
    def edit_lyrics(self):
        song = self.get_selected_song()
        if song:
            self.open_lyrics_editor(song)

    def open_lyrics_editor(self, song):
        if not Path(song["audio_path"]).exists():
            QMessageBox.warning(self, "Arquivo não encontrado", f"O arquivo não existe:\n{song['audio_path']}")
            return
        self.current_song = song
        if not self.player.is_loaded(song["audio_path"]):
            self.player.load(song["audio_path"])
        LyricsEditor(song, self.player, self).exec()
        self.library.reload()

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
        if self.current_song is None:
            return

        if self.karaoke_windows:
            window = self.karaoke_windows[0]
            window.showFullScreen()
            window.raise_()
            window.activateWindow()
            return

        window = KaraokeWindow(self.current_song, self.player, self)
        window.play_requested.connect(self.play_from_position)
        self.karaoke_windows.append(window)

        def forget_window():
            if window in self.karaoke_windows:
                self.karaoke_windows.remove(window)

        window.destroyed.connect(forget_window)

    def play_from_position(self, milliseconds):
        """Toca a música atual a partir de um ponto (clique na letra do karaokê)."""
        if self._queue_active:
            self.player.play()
        else:
            self.start_queue_head()   # depois de um Stop, retoma a fila
        self.player.set_position(milliseconds)

    def _close_karaoke_windows(self):
        for window in list(self.karaoke_windows):
            window.close()

    # --- player --------------------------------------------------------------
    def update_progress(self, milliseconds):
        with QSignalBlocker(self.progress):
            self.progress.setValue(min(milliseconds, self.progress.maximum()))
        self.time_label.setText(f"{format_ms(milliseconds)} / {format_ms(self.player.media.duration())}")

    def update_duration(self, milliseconds):
        self.progress.setRange(0, max(0, milliseconds))

    def update_play_button(self, state):
        self.play_button.setText("Pause" if state == "playing" else "Play")

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
            self.toggle_queue_playback()
            return
        super().keyPressEvent(event)
