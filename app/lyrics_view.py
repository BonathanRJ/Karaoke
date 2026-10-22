"""Letra no estilo do Spotify Desktop.

A letra rola linha por linha: só o tempo de início de cada linha do LRC é usado, o
que torna a sincronia bem mais tolerante do que o preenchimento palavra a palavra.
"""
import math
import time
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QTransform,
    QCursor,
    QFont,
    QFontDatabase,
    QFontMetricsF,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtWidgets import QWidget

from .covers import COVER_FALLBACK_BACKGROUND

LYRICS_FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
LYRICS_FONT_FAMILIES = ("Inter", "Segoe UI")  # a primeira disponível é usada
LYRICS_FONT_PX = 64             # tamanho em telas Full HD; escala com a janela
LYRICS_LETTER_SPACING = -1.5    # px, também escalado
LYRICS_LINE_HEIGHT = 1.2
LYRICS_VERSE_GAP = 36           # px entre linhas do LRC
# As cores das linhas já cantadas e das próximas vêm da cor de fundo (lyrics_colors).
LYRICS_COLOR_CURRENT = "#FFFFFF"
LYRICS_SCROLL_MS = 500          # rolagem até a próxima linha (ease-in-out)
LYRICS_COLOR_MS = 300           # troca de cor (CSS "color 0.3s ease")
LYRICS_WHEEL_SCROLL_MS = 180    # rolagem com a roda do mouse
LYRICS_FOLLOW_RESUME_MS = 3000  # depois de rolar à mão, volta a acompanhar a música
LYRICS_LEFT_RATIO = 0.23        # início do texto em telas grandes (fração da largura)
LYRICS_COLUMN_RATIO = 0.56      # largura máxima da coluna de texto
LYRICS_FOCUS_RATIO = 0.42       # altura onde fica o centro da linha atual
LYRICS_EDGE_FADE = 0.14         # esmaecimento no topo e no rodapé da área da letra
LYRICS_COVER_SIZE = 220         # px da capa em Full HD; escala com a tela
LYRICS_COVER_RADIUS = 15        # arredondamento dos cantos da capa
LYRICS_COVER_SHADOW_BLUR = 18   # espalhamento da sombra da capa
LYRICS_COVER_SHADOW_OFFSET = 6  # deslocamento da sombra para baixo
LYRICS_COVER_SHADOW_ALPHA = 110 # intensidade da sombra (0-255)
LYRICS_COVER_TILT_DEGREES = 6.0 # inclinação máxima da capa sob o mouse
LYRICS_COVER_TILT_SPEED = 12.0  # rapidez com que a inclinação acompanha o mouse
LYRICS_TITLE_PX = 26            # nome da música abaixo da capa (Full HD)
LYRICS_ARTIST_PX = 18           # nome do artista abaixo da capa (Full HD)

_lyrics_font_family = None


def lyrics_font_family():
    global _lyrics_font_family
    if _lyrics_font_family is None:
        if LYRICS_FONT_DIR.is_dir():
            for path in sorted(LYRICS_FONT_DIR.iterdir()):
                if path.suffix.lower() in (".ttf", ".otf"):
                    QFontDatabase.addApplicationFont(str(path))
        available = set(QFontDatabase.families())
        _lyrics_font_family = next(
            (family for family in LYRICS_FONT_FAMILIES if family in available),
            QFont().family(),
        )
    return _lyrics_font_family


def _cubic_bezier(x1, y1, x2, y2):
    """Curva de tempo no formato CSS cubic-bezier(x1, y1, x2, y2)."""
    def coordinate(t, p1, p2):
        return 3 * (1 - t) ** 2 * t * p1 + 3 * (1 - t) * t * t * p2 + t ** 3

    def ease(progress):
        if progress <= 0.0:
            return 0.0
        if progress >= 1.0:
            return 1.0
        low, high = 0.0, 1.0
        for _ in range(24):          # resolve x(t) = progresso por bisseção
            middle = (low + high) / 2
            if coordinate(middle, x1, x2) < progress:
                low = middle
            else:
                high = middle
        return coordinate((low + high) / 2, y1, y2)

    return ease


# Mesmas curvas do navegador usado pelo app do Spotify: rolagem suave "ease-in-out"
# e transição de cor CSS "ease".
_EASE_IN_OUT = _cubic_bezier(0.42, 0.0, 0.58, 1.0)
_EASE = _cubic_bezier(0.25, 0.1, 0.25, 1.0)


def _mix(start, end, amount):
    return QColor(
        round(start.red() + (end.red() - start.red()) * amount),
        round(start.green() + (end.green() - start.green()) * amount),
        round(start.blue() + (end.blue() - start.blue()) * amount),
    )


class LyricsView(QWidget):
    """Coluna de letra no estilo do Spotify: rolagem suave, destaque da linha
    atual, sublinhado ao passar o mouse e clique para ir àquele trecho."""

    line_clicked = Signal(int)
    cover_clicked = Signal()   # play/pause

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self._texts = []
        self._clickable = False
        self._version = 0
        self._layout = []          # (topo, altura, linhas quebradas) de cada linha do LRC
        self._layout_key = None
        self._font = None
        self._underline_font = None
        self._metrics = None
        self._row_height = 0.0
        self._active = -1
        self._hover = -1
        self._scroll = 0.0
        self._scroll_from = 0.0
        self._scroll_to = 0.0
        self._scroll_start = 0.0
        self._scroll_duration = LYRICS_SCROLL_MS
        self._following = True     # False enquanto o usuário rola a letra com o mouse
        self._color_tweens = {}    # índice -> (cor inicial, cor final, início)
        self._cover = None
        self._cover_scaled = None
        self._cover_rect = None    # onde a capa está (sem inclinação), para o mouse
        self._title = ""
        self._artist = ""
        self._tilt = [0.0, 0.0]          # graus nos eixos X e Y
        self._tilt_target = (0.0, 0.0)
        self._last_frame = 0.0
        self._canvas = None
        self._colors = lyrics_colors(COVER_FALLBACK_BACKGROUND)

        # Um único relógio anima rolagem e cores (~60 fps), só enquanto há movimento.
        self._frame_timer = QTimer(self)
        self._frame_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._frame_timer.setInterval(16)
        self._frame_timer.timeout.connect(self._animate)

        self._resume_timer = QTimer(self)
        self._resume_timer.setSingleShot(True)
        self._resume_timer.setInterval(LYRICS_FOLLOW_RESUME_MS)
        self._resume_timer.timeout.connect(self.follow_song)

    # --- dados ---------------------------------------------------------------
    def set_lines(self, texts, clickable=True):
        self._texts = [(text or "").strip() for text in texts]
        self._clickable = clickable
        self._version += 1
        self._active = -1
        self._hover = -1
        self._color_tweens = {}
        self._frame_timer.stop()
        self._following = True
        self._resume_timer.stop()
        self.unsetCursor()
        self._ensure_layout()
        self._jump_to_target()
        self.update()

    def set_cover(self, pixmap):
        self._cover = pixmap if pixmap and not pixmap.isNull() else None
        self._cover_scaled = None
        self.update()

    def set_song_info(self, title, artist):
        """Nome da música e do artista, mostrados abaixo da capa."""
        self._title = title or ""
        self._artist = artist or ""
        self.update()

    def set_background(self, color):
        self._colors = lyrics_colors(color)
        self._color_tweens = {}
        self.update()

    def set_active(self, index):
        if index == self._active:
            return
        now = time.perf_counter()
        previous = self._active

        # Como uma transição CSS: cada linha que muda de estado parte da cor que
        # está exibindo agora, mesmo que a transição anterior não tenha terminado.
        changed = range(max(0, min(previous, index)), min(len(self._texts), max(previous, index) + 1))
        starting = {line: self._current_color(line, now) for line in changed}
        self._active = index
        for line, start in starting.items():
            end = self._target_color(line)
            if start != end:
                self._color_tweens[line] = (start, end, now)

        self._ensure_layout()
        if self._following:
            self._scroll_towards(self._target_scroll(), LYRICS_SCROLL_MS)
        else:
            self._start_frames()

    def follow_song(self):
        """Volta a acompanhar a linha atual (depois de rolar a letra com o mouse)."""
        self._resume_timer.stop()
        self._following = True
        self._ensure_layout()
        self._scroll_towards(self._target_scroll(), LYRICS_SCROLL_MS)

    # --- animação ------------------------------------------------------------
    def _scroll_towards(self, target, duration):
        now = time.perf_counter()
        self._scroll_from = self._scroll_value(now)
        self._scroll_to = target
        self._scroll_start = now
        self._scroll_duration = duration
        self._start_frames()

    def _start_frames(self):
        if not self._frame_timer.isActive():
            self._last_frame = time.perf_counter()
            self._frame_timer.start()

    def _jump_to_target(self):
        self._scroll = self._scroll_from = self._scroll_to = self._target_scroll()
        self._scroll_start = 0.0

    def _scroll_value(self, now):
        progress = (now - self._scroll_start) * 1000 / self._scroll_duration
        return self._scroll_from + (self._scroll_to - self._scroll_from) * _EASE_IN_OUT(progress)

    def _target_color(self, index):
        if index == self._active:
            return self._colors["current"]
        return self._colors["past"] if index < self._active else self._colors["next"]

    def _current_color(self, index, now):
        tween = self._color_tweens.get(index)
        if tween is None:
            return self._target_color(index)
        start, end, began = tween
        progress = (now - began) * 1000 / LYRICS_COLOR_MS
        if progress >= 1.0:
            return end
        return _mix(start, end, _EASE(progress))

    def _animate(self):
        now = time.perf_counter()
        elapsed = min(0.05, now - self._last_frame)
        self._last_frame = now
        self._scroll = self._scroll_value(now)
        scrolling = (now - self._scroll_start) * 1000 < self._scroll_duration
        if scrolling and not self._following and self.underMouse():
            # O texto passa por baixo do mouse parado: o sublinhado acompanha.
            self._update_hover(self.mapFromGlobal(QCursor.pos()))
        self._color_tweens = {
            line: tween for line, tween in self._color_tweens.items()
            if (now - tween[2]) * 1000 < LYRICS_COLOR_MS
        }
        # A inclinação da capa segue o mouse com suavidade.
        follow = 1.0 - math.exp(-elapsed * LYRICS_COVER_TILT_SPEED)
        tilting = False
        for axis in (0, 1):
            self._tilt[axis] += (self._tilt_target[axis] - self._tilt[axis]) * follow
            if abs(self._tilt_target[axis] - self._tilt[axis]) < 0.02:
                self._tilt[axis] = self._tilt_target[axis]
            else:
                tilting = True
        if not scrolling and not self._color_tweens and not tilting:
            self._frame_timer.stop()
        self.update()

    # --- geometria -----------------------------------------------------------
    def _scale(self):
        window = self.window()
        return max(0.45, min(window.width() / 1920, window.height() / 1080))

    def _column(self):
        width = self.width()
        if width >= 1200:
            left = width * LYRICS_LEFT_RATIO
            column = min(width - left - 24, width * LYRICS_COLUMN_RATIO)
        else:
            left = max(16.0, width * 0.06)
            column = width - 2 * left
        return left, max(120.0, column)

    @staticmethod
    def _wrap(text, metrics, width):
        rows = []
        current = ""
        for word in text.split():
            candidate = f"{current} {word}" if current else word
            if current and metrics.horizontalAdvance(candidate) > width:
                rows.append(current)
                current = word
            else:
                current = candidate
        rows.append(current)
        return rows

    def _ensure_layout(self):
        window = self.window()
        key = (self.width(), self.height(), window.width(), window.height(), self._version)
        if key == self._layout_key:
            return
        self._layout_key = key

        scale = self._scale()
        font = QFont(lyrics_font_family())
        font.setPixelSize(max(22, round(LYRICS_FONT_PX * scale)))
        font.setWeight(QFont.Weight.Bold)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, LYRICS_LETTER_SPACING * scale)
        underline_font = QFont(font)
        underline_font.setUnderline(True)
        metrics = QFontMetricsF(font)
        row_height = font.pixelSize() * LYRICS_LINE_HEIGHT
        gap = LYRICS_VERSE_GAP * scale
        _, column = self._column()

        layout = []
        top = 0.0
        for text in self._texts:
            # Linha sem texto no LRC indica pausa instrumental.
            rows = self._wrap(text or "♪", metrics, column)
            height = len(rows) * row_height
            layout.append((top, height, rows))
            top += height + gap

        self._font = font
        self._underline_font = underline_font
        self._metrics = metrics
        self._row_height = row_height
        self._layout = layout

    def _scroll_limits(self):
        """Rolagem possível: da primeira à última linha no centro da tela."""
        if not self._layout:
            return 0.0, 0.0
        focus = self.height() * LYRICS_FOCUS_RATIO
        first_top, first_height, _ = self._layout[0]
        last_top, last_height, _ = self._layout[-1]
        return first_top + first_height / 2 - focus, last_top + last_height / 2 - focus

    def _target_scroll(self):
        if not self._layout:
            return 0.0
        index = min(max(0, self._active), len(self._layout) - 1)
        top, height, _ = self._layout[index]
        return top + height / 2 - self.height() * LYRICS_FOCUS_RATIO

    def _line_at(self, point):
        """Linha do LRC sob o mouse (somente sobre o texto, como um link)."""
        if not self._clickable or self._metrics is None:
            return -1
        left, _ = self._column()
        for index, (top, height, rows) in enumerate(self._layout):
            y = top - self._scroll
            if y > point.y():
                break
            for row_number, row in enumerate(rows):
                row_top = y + row_number * self._row_height
                if (row_top <= point.y() < row_top + self._row_height
                        and left <= point.x() <= left + self._metrics.horizontalAdvance(row)):
                    return index
        return -1

    # --- mouse ---------------------------------------------------------------
    def _on_cover(self, point):
        return self._cover_rect is not None and self._cover_rect.contains(point)

    def _update_hover(self, point):
        on_cover = self._on_cover(point)
        hover = -1 if on_cover else self._line_at(point)
        if hover != self._hover:
            self._hover = hover
            self.update()
        if on_cover or hover >= 0:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.unsetCursor()
        self._set_tilt_towards(point if on_cover else None)

    def _set_tilt_towards(self, point):
        """Inclina a capa como um tabuleiro: o canto sob o mouse afunda e o oposto levanta."""
        if point is None:
            target = (0.0, 0.0)
        else:
            rect = self._cover_rect
            dx = max(-1.0, min(1.0, (point.x() - rect.center().x()) / (rect.width() / 2)))
            dy = max(-1.0, min(1.0, (point.y() - rect.center().y()) / (rect.height() / 2)))
            # No Qt, girar +X afunda a borda de cima e girar +Y afunda a borda esquerda.
            target = (-dy * LYRICS_COVER_TILT_DEGREES, -dx * LYRICS_COVER_TILT_DEGREES)
        if target != self._tilt_target:
            self._tilt_target = target
            self._start_frames()

    def mouseMoveEvent(self, event):
        self._update_hover(event.position())
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._hover = -1
        self.unsetCursor()
        self._set_tilt_towards(None)
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._on_cover(event.position()):
            self.cover_clicked.emit()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            index = self._line_at(event.position())
            if index >= 0:
                self.line_clicked.emit(index)
                self.follow_song()
                return
        super().mousePressEvent(event)

    def wheelEvent(self, event):
        if not self._layout:
            return
        steps = event.angleDelta().y() / 120 if not event.pixelDelta().y() else event.pixelDelta().y() / self._row_height
        low, high = self._scroll_limits()
        # Soma ao destino atual para que vários "cliques" da roda se acumulem.
        target = min(high, max(low, self._scroll_to - steps * self._row_height * 1.5))
        self._following = False
        self._resume_timer.start()
        self._scroll_towards(target, LYRICS_WHEEL_SCROLL_MS)
        event.accept()

    # --- desenho -------------------------------------------------------------
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._ensure_layout()
        self._canvas = None
        self._cover_scaled = None
        self._following = True
        self._resume_timer.stop()
        self._jump_to_target()

    def paintEvent(self, event):
        if self.width() <= 0 or self.height() <= 0:
            return
        self._ensure_layout()
        now = time.perf_counter()

        # A letra é desenhada numa camada própria para aplicar o esmaecimento das
        # bordas sem afetar o fundo (que pode ser uma imagem do artista).
        ratio = self.devicePixelRatioF()
        size = QSize(round(self.width() * ratio), round(self.height() * ratio))
        if self._canvas is None or self._canvas.size() != size:
            self._canvas = QPixmap(size)
            self._canvas.setDevicePixelRatio(ratio)
        canvas = self._canvas
        canvas.fill(Qt.GlobalColor.transparent)

        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        left, _ = self._column()
        metrics = self._metrics
        text_offset = (self._row_height - metrics.height()) / 2 + metrics.ascent()
        for index, (top, height, rows) in enumerate(self._layout):
            y = top - self._scroll
            if y + height < 0:
                continue
            if y > self.height():
                break
            painter.setFont(self._underline_font if index == self._hover else self._font)
            painter.setPen(self._current_color(index, now))
            for row_number, row in enumerate(rows):
                painter.drawText(QPointF(left, y + row_number * self._row_height + text_offset), row)

        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0.0, QColor(0, 0, 0, 0))
        gradient.setColorAt(LYRICS_EDGE_FADE, QColor(0, 0, 0, 255))
        gradient.setColorAt(1.0 - LYRICS_EDGE_FADE, QColor(0, 0, 0, 255))
        gradient.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        painter.fillRect(QRectF(0, 0, self.width(), self.height()), gradient)
        painter.end()

        painter = QPainter(self)
        self._paint_cover_and_title(painter, left)
        painter.drawPixmap(0, 0, canvas)

    def _paint_cover_and_title(self, painter, left):
        """Capa (inclinada pelo mouse) com o nome da música e do artista logo abaixo,
        centralizados na margem esquerda, quando houver espaço."""
        self._cover_rect = None
        scale = self._scale()
        size = round(LYRICS_COVER_SIZE * scale)
        if left - size < 48:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        center_x = left / 2
        text_width = left - 48 * scale
        title_font = QFont(lyrics_font_family())
        title_font.setPixelSize(max(14, round(LYRICS_TITLE_PX * scale)))
        title_font.setWeight(QFont.Weight.Bold)
        artist_font = QFont(title_font)
        artist_font.setPixelSize(max(11, round(LYRICS_ARTIST_PX * scale)))
        wrap = Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap
        title_height = QFontMetricsF(title_font).boundingRect(QRectF(0, 0, text_width, 10_000), wrap, self._title).height() if self._title else 0
        artist_height = QFontMetricsF(artist_font).boundingRect(QRectF(0, 0, text_width, 10_000), wrap, self._artist).height() if self._artist else 0

        cover = self._cover_pixmap(size) if self._cover else None
        cover_height = cover[3] if cover else 0
        gap = 20 * scale if cover and (self._title or self._artist) else 0
        top = (self.height() - (cover_height + gap + title_height + 4 * scale + artist_height)) / 2

        if cover:
            pixmap, margin, width, height = cover
            self._cover_rect = QRectF(center_x - width / 2, top, width, height)
            center = self._cover_rect.center()
            tilt = QTransform()
            tilt.translate(center.x(), center.y())
            tilt.rotate(self._tilt[0], Qt.Axis.XAxis)
            tilt.rotate(self._tilt[1], Qt.Axis.YAxis)
            tilt.translate(-center.x(), -center.y())
            painter.setTransform(tilt)
            painter.drawPixmap(QPointF(self._cover_rect.left() - margin, top - margin), pixmap)
            painter.resetTransform()
            top += height + gap

        if self._title:
            painter.setFont(title_font)
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(QRectF(center_x - text_width / 2, top, text_width, title_height), wrap, self._title)
            top += title_height + 4 * scale
        if self._artist:
            painter.setFont(artist_font)
            painter.setPen(QColor(255, 255, 255, 170))
            painter.drawText(QRectF(center_x - text_width / 2, top, text_width, artist_height), wrap, self._artist)

    def _cover_pixmap(self, size):
        """Capa com cantos arredondados e sombra suave, desenhada uma vez por tamanho.

        Devolve (imagem com a sombra, margem da sombra, largura e altura da capa).
        """
        if self._cover_scaled is not None and self._cover_scaled[0] == size:
            return self._cover_scaled[1]

        scale = self._scale()
        cover = self._cover.scaled(
            size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
        width, height = cover.width(), cover.height()
        radius = LYRICS_COVER_RADIUS * scale
        blur = max(1, round(LYRICS_COVER_SHADOW_BLUR * scale))
        offset = round(LYRICS_COVER_SHADOW_OFFSET * scale)
        margin = blur + offset
        full_width, full_height = width + 2 * margin, height + 2 * margin

        ratio = self.devicePixelRatioF()
        result = QPixmap(round(full_width * ratio), round(full_height * ratio))
        result.setDevicePixelRatio(ratio)
        result.fill(Qt.GlobalColor.transparent)

        # Sombra: a forma desenhada bem pequena e ampliada com suavização vira um
        # desfoque barato, sem efeitos gráficos a cada quadro.
        shrink = 8
        small = QImage(max(1, full_width // shrink), max(1, full_height // shrink), QImage.Format.Format_ARGB32_Premultiplied)
        small.fill(Qt.GlobalColor.transparent)
        shadow_painter = QPainter(small)
        shadow_painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        shadow_painter.setPen(Qt.PenStyle.NoPen)
        shadow_painter.setBrush(QColor(0, 0, 0, LYRICS_COVER_SHADOW_ALPHA))
        shadow_painter.drawRoundedRect(
            QRectF(margin / shrink, (margin + offset) / shrink, width / shrink, height / shrink),
            radius / shrink, radius / shrink,
        )
        shadow_painter.end()

        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawImage(QRectF(0, 0, full_width, full_height), small)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(margin, margin, width, height), radius, radius)
        painter.setClipPath(clip)
        painter.drawPixmap(margin, margin, cover)
        painter.end()

        self._cover_scaled = (size, (result, margin, width, height))
        return self._cover_scaled[1]


def lyrics_colors(background):
    """Cores da letra derivadas do fundo: próximas linhas claras, já cantadas em meio-tom.

    Com o fundo de referência #003071 elas resultam em ~#A1D3FF e ~#5182B8.
    """
    background = QColor(background)
    hue = max(0.0, background.hslHueF())
    saturation = max(0.0, background.hslSaturationF())
    return {
        "current": QColor(LYRICS_COLOR_CURRENT),
        "past": QColor.fromHslF(hue, saturation * 0.43, 0.52),
        "next": QColor.fromHslF(hue, saturation, 0.82),
    }
