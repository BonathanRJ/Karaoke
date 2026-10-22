"""Biblioteca de músicas: visão em lista e em ícones (capas dos álbuns)."""
from PySide6.QtCore import QFileSystemWatcher, QRectF, QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QHeaderView,
    QListView,
    QListWidget,
    QListWidgetItem,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .covers import COVER_DIR, cover_index, cover_thumbnail, ensure_album_cover_folder, file_mtime, find_cover_path
from .database import DATA_DIR, list_songs

LIBRARY_SETTINGS = QSettings(str(DATA_DIR / "settings.ini"), QSettings.Format.IniFormat)


def view_mode_icon(kind):
    """Ícones dos botões de visão, desenhados como no Explorador de Arquivos."""
    icon = QIcon()
    for state, color in ((QIcon.State.Off, "#9a9a9a"), (QIcon.State.On, "#ffffff")):
        pixmap = QPixmap(40, 40)
        pixmap.setDevicePixelRatio(2)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        if kind == "list":
            for row in range(3):
                y = 4 + row * 5
                painter.drawRoundedRect(QRectF(2, y, 3, 3), 0.8, 0.8)
                painter.drawRoundedRect(QRectF(7, y + 0.5, 11, 2), 0.8, 0.8)
        else:
            for row in range(2):
                for column in range(2):
                    painter.drawRoundedRect(QRectF(2.5 + column * 8, 2.5 + row * 8, 6.5, 6.5), 1.2, 1.2)
        painter.end()
        icon.addPixmap(pixmap, QIcon.Mode.Normal, state)
    return icon


class SongIconDelegate(QStyledItemDelegate):
    """Cartão da visão de ícones: capa, artista e, abaixo, o nome da música."""

    COVER = 132
    WIDTH = 156
    HEIGHT = 200

    def sizeHint(self, option, index):
        return QSize(self.WIDTH, self.HEIGHT)

    def paint(self, painter, option, index):
        song = index.data(Qt.ItemDataRole.UserRole) or {}
        rect = option.rect
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected or hovered:
            background = option.palette.highlight().color() if selected else QColor("#2c2c2c")
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(background)
            painter.drawRoundedRect(QRectF(rect).adjusted(3, 3, -3, -3), 8, 8)

        cover_rect = QRectF(rect.x() + (rect.width() - self.COVER) / 2, rect.y() + 10, self.COVER, self.COVER)
        thumbnail = (cover_thumbnail(song["cover_file"], self.COVER, song.get("cover_mtime"))
                     if song.get("cover_file") else None)
        clip = QPainterPath()
        clip.addRoundedRect(cover_rect, 6, 6)
        painter.setClipPath(clip)
        if thumbnail is not None:
            painter.drawPixmap(cover_rect.toRect(), thumbnail)
        else:
            painter.fillRect(cover_rect, QColor("#333333"))
            note_font = QFont(option.font)
            note_font.setPixelSize(48)
            painter.setFont(note_font)
            painter.setPen(QColor("#777777"))
            painter.drawText(cover_rect, Qt.AlignmentFlag.AlignCenter, "♪")
        painter.setClipping(False)

        text_width = rect.width() - 16
        text_left = rect.x() + 8
        artist_font = QFont(option.font)
        artist_font.setBold(True)
        title_font = QFont(option.font)
        artist_metrics = QFontMetrics(artist_font)
        title_metrics = QFontMetrics(title_font)
        top = cover_rect.bottom() + 8

        painter.setFont(artist_font)
        painter.setPen(QColor("#ffffff"))
        artist = artist_metrics.elidedText(song.get("artist") or "Artista desconhecido",
                                           Qt.TextElideMode.ElideRight, text_width)
        painter.drawText(QRectF(text_left, top, text_width, artist_metrics.height()),
                         Qt.AlignmentFlag.AlignHCenter, artist)

        painter.setFont(title_font)
        painter.setPen(QColor("#ffffff") if selected else QColor("#b3b3b3"))
        title = title_metrics.elidedText(song.get("title") or "", Qt.TextElideMode.ElideRight, text_width)
        painter.drawText(QRectF(text_left, top + artist_metrics.height() + 2, text_width, title_metrics.height()),
                         Qt.AlignmentFlag.AlignHCenter, title)
        painter.restore()


def song_matches(song, query):
    """Busca por título, artista, álbum ou código (query já em minúsculas)."""
    return any(query in (song.get(field) or "").casefold() for field in ("title", "artist", "album", "code"))


class LibraryPanel(QWidget):
    """Biblioteca com duas visões (lista e ícones), busca e capas dos álbuns.

    Os dados vêm do banco só em reload(); a busca apenas esconde/mostra itens.
    """

    song_activated = Signal(dict)   # duplo clique

    def __init__(self, parent=None):
        super().__init__(parent)
        self._filter = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Alternância entre as visões, como no Explorador de Arquivos.
        view_row = QHBoxLayout()
        view_row.setContentsMargins(0, 0, 0, 0)
        view_row.setSpacing(2)
        view_row.addStretch()
        self._view_buttons = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        for mode, tooltip in (("list", "Lista"), ("icons", "Ícones")):
            button = QToolButton()
            button.setObjectName("viewToggle")
            button.setIcon(view_mode_icon(mode))
            button.setIconSize(QSize(20, 20))
            button.setCheckable(True)
            button.setToolTip(tooltip)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked, mode=mode: self.set_view(mode))
            group.addButton(button)
            view_row.addWidget(button)
            self._view_buttons[mode] = button
        layout.addLayout(view_row)

        self.list_view = QTreeWidget()
        self.list_view.setHeaderLabels(["Código", "Artista", "Música", "Álbum"])
        self.list_view.setRootIsDecorated(False)
        self.list_view.setAlternatingRowColors(True)
        self.list_view.setUniformRowHeights(True)
        self.list_view.setColumnWidth(0, 75)
        self.list_view.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        for column in (1, 2, 3):
            self.list_view.header().setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        self.list_view.itemDoubleClicked.connect(lambda item: self.song_activated.emit(self._song(item)))

        self.icon_view = QListWidget()
        self.icon_view.setViewMode(QListView.ViewMode.IconMode)
        self.icon_view.setResizeMode(QListView.ResizeMode.Adjust)
        self.icon_view.setMovement(QListView.Movement.Static)
        self.icon_view.setUniformItemSizes(True)
        self.icon_view.setSpacing(6)
        self.icon_view.setMouseTracking(True)
        self.icon_view.setItemDelegate(SongIconDelegate(self.icon_view))
        self.icon_view.itemDoubleClicked.connect(lambda item: self.song_activated.emit(self._song(item)))

        self.stack = QStackedWidget()
        self.stack.addWidget(self.list_view)
        self.stack.addWidget(self.icon_view)
        layout.addWidget(self.stack)

        self.setStyleSheet("""
            QToolButton#viewToggle {
                background: transparent; border: 1px solid transparent;
                border-radius: 4px; padding: 3px;
            }
            QToolButton#viewToggle:hover { background: #2a2a2a; }
            QToolButton#viewToggle:checked { background: #333333; border-color: #555555; }
        """)

        # Ao colocar/trocar a imagem numa pasta de álbum, os ícones se atualizam sozinhos.
        self._cover_watcher = QFileSystemWatcher(self)
        self._reload_timer = QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.setInterval(400)
        self._reload_timer.timeout.connect(self.reload)
        self._cover_watcher.directoryChanged.connect(lambda _path: self._reload_timer.start())

        self.set_view(LIBRARY_SETTINGS.value("library/view", "list"))

    # --- dados ---------------------------------------------------------------
    def reload(self, selected_id=None):
        if selected_id is None:
            current = self.selected_song()
            selected_id = current["id"] if current else None

        songs = [dict(row) for row in list_songs()]
        index = cover_index()
        for song in songs:
            if song["album"]:
                # Álbuns cadastrados antes de as pastas existirem também ganham a sua.
                try:
                    ensure_album_cover_folder(song["artist"], song["album"], index)
                except OSError:
                    pass
            cover = find_cover_path(song, index)
            song["cover_file"] = str(cover) if cover else ""
            song["cover_mtime"] = file_mtime(cover) if cover else None
        self._watch_cover_folders(index)

        self.list_view.clear()
        self.icon_view.clear()
        for song in songs:
            item = QTreeWidgetItem([song["code"] or "", song["artist"] or "", song["title"], song["album"] or ""])
            item.setData(0, Qt.ItemDataRole.UserRole, song)
            self.list_view.addTopLevelItem(item)

            icon_item = QListWidgetItem()
            icon_item.setData(Qt.ItemDataRole.UserRole, song)
            icon_item.setToolTip(" — ".join(part for part in (song["title"], song["artist"], song["album"]) if part))
            self.icon_view.addItem(icon_item)

        self.set_filter(self._filter)
        if selected_id is not None:
            self.select(selected_id)

    def _watch_cover_folders(self, index):
        COVER_DIR.mkdir(parents=True, exist_ok=True)
        folders = {str(COVER_DIR), *(str(path) for path in index.values())}
        missing = folders - set(self._cover_watcher.directories())
        if missing:
            self._cover_watcher.addPaths(sorted(missing))

    def set_filter(self, text):
        self._filter = text
        query = text.strip().casefold()
        for item in self._items():
            item.setHidden(bool(query) and not song_matches(self._song(item), query))

    # --- seleção e visões ----------------------------------------------------
    def _items(self):
        for index in range(self.list_view.topLevelItemCount()):
            yield self.list_view.topLevelItem(index)
        for index in range(self.icon_view.count()):
            yield self.icon_view.item(index)

    @staticmethod
    def _song(item):
        if isinstance(item, QTreeWidgetItem):
            return item.data(0, Qt.ItemDataRole.UserRole)
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def selected_song(self):
        item = self.stack.currentWidget().currentItem()
        if item is None or item.isHidden():
            return None
        return self._song(item)

    def select(self, song_id):
        """Seleciona a música nas duas visões."""
        for index in range(self.list_view.topLevelItemCount()):
            item = self.list_view.topLevelItem(index)
            if self._song(item)["id"] == song_id:
                self.list_view.setCurrentItem(item)
                break
        for index in range(self.icon_view.count()):
            item = self.icon_view.item(index)
            if self._song(item)["id"] == song_id:
                self.icon_view.setCurrentItem(item)
                break

    def set_view(self, mode):
        mode = "icons" if mode == "icons" else "list"
        selected = self.selected_song()
        self.stack.setCurrentWidget(self.icon_view if mode == "icons" else self.list_view)
        self._view_buttons[mode].setChecked(True)
        if selected:
            self.select(selected["id"])
        LIBRARY_SETTINGS.setValue("library/view", mode)
