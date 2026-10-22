"""Revelação da nota no fim da música, como nas máquinas de karaokê.

Sequência: "Sua nota é..." → rufar de tambores enquanto os dígitos giram → a
dezena para → no prato do rufar a unidade para → reação conforme a nota
(confete e torcida, bola de feno e palmas tímidas, ou tomates e vaias) →
mensagem de parabéns → se houver outra música na fila, "Próxima música" com
uma contagem de 10 s para dar tempo de passar o microfone.
Clique, Enter, Espaço ou Esc revelam a nota na hora; depois, avançam uma etapa.
"""
import math
import random
import time
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QWidget

from .lyrics_view import lyrics_font_family

SOUNDS_DIR = Path(__file__).resolve().parent.parent / "assets" / "sounds"
SOUND_FILES = {
    "drumroll": "drumroll.wav",          # rufar de 6 s com prato no fim
    "kids_cheering": "kids_cheering.mp3",
    "crowd_cheer": "crowd_cheer.mp3",
    "crowd_applause": "crowd_applause.mp3",
    "crowd_meh": "crowd_meh.wav",        # burburinho + palmas isoladas
    "crowd_boo": "crowd_boo.mp3",
}
SOUND_VOLUME = 0.85

INTRO_S = 1.2                          # só "Sua nota é..."; depois começa o rufar
DRUMROLL_S = 6.0                       # momento do prato dentro de drumroll.wav
TENS_STOP_S = INTRO_S + 3.8            # a dezena para de girar no meio do rufar
UNITS_STOP_S = INTRO_S + DRUMROLL_S    # a unidade para no prato: nota revelada
MESSAGE_S = UNITS_STOP_S + 0.5         # aparece a mensagem de parabéns
FINISH_S = UNITS_STOP_S + 7.0          # a tela de nota se fecha sozinha
NEXT_COUNTDOWN_S = 10.0                # "Próxima música": tempo para passar o microfone
NEXT_FADE_S = 0.4                      # transição da nota para a próxima música
POP_S = 0.35                           # duração do "pulo" do dígito quando para
ROLL_INTERVAL_S = 0.06

CONFETTI_COLORS = ["#ff4d6d", "#ffd54a", "#4dd2ff", "#7ee08a", "#b388ff", "#ff9f43", "#ffffff"]
TOMATO_FLIGHT_S = 0.55
TUMBLEWEED_S = 5.0                     # tempo para a bola de feno cruzar a tela
# Rajadas dos canhões de confete: (atraso após a revelação, papéis por canhão).
CONFETTI_VOLLEYS = {
    "big_confetti": [(0.0, 130)],
    "more_confetti": [(0.0, 100), (0.6, 70)],
    "confetti": [(0.0, 55)],
}


def congratulation(score):
    if score >= 95:
        return "Perfeito! Você é uma estrela!", "#ffd54a"
    if score >= 85:
        return "Parabéns! Que apresentação incrível!", "#7ee08a"
    if score >= 70:
        return "Parabéns! Mandou muito bem!", "#7ec8ff"
    if score >= 55:
        return "Parabéns! Continue praticando!", "#ffb36b"
    return "Parabéns pela coragem! Na próxima vai ser melhor!", "#ff8a8a"


def reaction(score):
    """Efeito visual e som tocados quando a nota é revelada (ou None)."""
    if score >= 95:
        return "big_confetti", "kids_cheering"
    if score >= 85:
        return "more_confetti", "crowd_cheer"
    if score >= 70:
        return "confetti", "crowd_applause"
    if score >= 55:
        return "tumbleweed", "crowd_meh"
    return "tomatoes", "crowd_boo"


class _Confetti:
    __slots__ = ("x", "y", "vx", "vy", "angle", "spin", "flip", "flip_speed", "w", "h", "color", "sway")

    def __init__(self, x, y, vx, vy, scale):
        self.x, self.y, self.vx, self.vy = x, y, vx, vy
        self.angle = random.uniform(0, 360)
        self.spin = random.uniform(-400, 400)
        self.flip = random.uniform(0, math.tau)
        self.flip_speed = random.uniform(6, 14)
        self.w = random.uniform(10, 18) * scale
        self.h = random.uniform(6, 10) * scale
        self.color = QColor(random.choice(CONFETTI_COLORS))
        self.sway = random.uniform(0, math.tau)


class _Tomato:
    """Tomate em voo da parte de baixo da tela até o alvo, onde vira mancha."""
    __slots__ = ("start", "x0", "y0", "x1", "y1", "arc", "spin", "landed")

    def __init__(self, start, x0, y0, x1, y1, arc):
        self.start, self.x0, self.y0, self.x1, self.y1, self.arc = start, x0, y0, x1, y1, arc
        self.spin = random.uniform(-540, 540)
        self.landed = False


class _Splat:
    __slots__ = ("x", "y", "born", "radius", "outline", "drops", "drips", "seeds")

    def __init__(self, x, y, born, radius):
        self.x, self.y, self.born, self.radius = x, y, born, radius
        points = 18
        self.outline = [
            (math.tau * i / points, radius * random.uniform(0.7, 1.25)) for i in range(points)
        ]
        self.drops = [
            (random.uniform(0, math.tau), radius * random.uniform(1.25, 2.1), radius * random.uniform(0.08, 0.22))
            for _ in range(random.randint(6, 11))
        ]
        self.drips = [
            (random.uniform(-0.6, 0.6) * radius, radius * random.uniform(0.12, 0.22), radius * random.uniform(0.6, 1.6))
            for _ in range(random.randint(2, 4))
        ]
        self.seeds = [
            (random.uniform(-0.5, 0.5) * radius, random.uniform(-0.5, 0.5) * radius) for _ in range(7)
        ]


class ScoreOverlay(QWidget):
    finished = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.hide()
        self._score = 0
        self._started = 0.0
        self._last_tick = 0.0
        self._rolling = ("0", "0")
        self._last_roll = 0.0
        self._drum_started = False
        self._revealed = False
        self._effect = None
        self._rain_until = 0.0
        self._volleys = []
        self._tumbleweed_start = None
        self._tumbleweed_arcs = []
        self._confetti = []
        self._tomatoes = []
        self._splats = []
        self._shake_until = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._sounds = {}
        for name, filename in SOUND_FILES.items():
            path = SOUNDS_DIR / filename
            if path.is_file():
                output = QAudioOutput(self)
                output.setVolume(SOUND_VOLUME)
                player = QMediaPlayer(self)
                player.setAudioOutput(output)
                player.setSource(QUrl.fromLocalFile(str(path)))   # já carrega para tocar sem atraso
                self._sounds[name] = (player, output)

    @property
    def active(self):
        return self._timer.isActive()

    def start(self, score, upcoming=None):
        """`upcoming` devolve a próxima música da fila (ou None) quando a nota termina."""
        self._score = max(0, min(100, int(score)))
        self._upcoming = upcoming
        self._next_song = None
        self._next_started = None
        self._started = self._last_tick = time.perf_counter()
        self._last_roll = 0.0
        self._drum_started = False
        self._revealed = False
        self._effect = None
        self._rain_until = 0.0
        self._shake_until = 0.0
        self._volleys = []
        self._tumbleweed_start = None
        self._confetti, self._tomatoes, self._splats = [], [], []
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        self.setFocus()
        self._timer.start()

    def cancel(self):
        """Fecha sem avisar (a fila já trocou de música por outro motivo)."""
        self._timer.stop()
        self._stop_sounds()
        self.hide()

    def skip(self):
        if not self._revealed:
            self._stop_sounds()
            self._started = time.perf_counter() - UNITS_STOP_S   # revela a nota na hora
            self._drum_started = True
            self._tick()
        elif self._next_started is None:
            self._show_next_or_finish()
        else:
            self._finish()   # começa a próxima música sem esperar a contagem

    def _show_next_or_finish(self):
        song = self._upcoming() if self._upcoming is not None else None
        if not song:
            self._finish()
            return
        self._stop_sounds()
        self._next_song = song
        self._next_started = time.perf_counter()
        self.update()

    def _finish(self):
        if not self._timer.isActive():
            return
        self._timer.stop()
        self._stop_sounds()
        self.hide()
        self.finished.emit()

    # --- sons --------------------------------------------------------------------
    def _play(self, name):
        sound = self._sounds.get(name)
        if sound is not None:
            sound[0].setPosition(0)
            sound[0].play()

    def _stop_sounds(self):
        for player, _output in self._sounds.values():
            player.stop()

    # --- animação ----------------------------------------------------------------
    def _tick(self):
        now = time.perf_counter()
        dt = min(0.05, now - self._last_tick)
        self._last_tick = now
        elapsed = now - self._started

        if not self._drum_started and elapsed >= INTRO_S:
            self._drum_started = True
            self._play("drumroll")
        self._sync_with_drumroll()
        elapsed = now - self._started

        if self._next_started is not None:
            if now - self._next_started >= NEXT_COUNTDOWN_S:
                self._finish()
                return
        elif elapsed >= FINISH_S:
            self._show_next_or_finish()
            if not self.active:
                return
        if not self._revealed and elapsed >= UNITS_STOP_S:
            self._reveal(elapsed)
        if now - self._last_roll >= ROLL_INTERVAL_S:
            self._last_roll = now
            self._rolling = (str(random.randint(0, 9)), str(random.randint(0, 9)))
        self._update_effects(elapsed, dt)
        self.update()

    def _sync_with_drumroll(self):
        """Acerta o relógio pelo áudio, para o prato cair junto com a revelação."""
        sound = self._sounds.get("drumroll")
        if sound is None or self._revealed or not sound[0].isPlaying():
            return
        position = sound[0].position() / 1000.0
        if position <= 0:
            return
        expected = time.perf_counter() - (INTRO_S + position)
        if abs(expected - self._started) > 0.08:
            self._started = expected

    def _reveal(self, elapsed):
        self._revealed = True
        self._effect, sound = reaction(self._score)
        if sound:
            self._play(sound)
        scale = self._scale()
        width, height = self.width(), self.height()
        if self._effect in CONFETTI_VOLLEYS:
            self._volleys = [(elapsed + delay, count) for delay, count in CONFETTI_VOLLEYS[self._effect]]
            # Na nota máxima, também chove confete do alto por alguns segundos.
            self._rain_until = elapsed + 3.5 if self._effect == "big_confetti" else 0.0
        elif self._effect == "tumbleweed":
            # Nota mediana: uma bola de feno atravessa a tela e meia dúzia de
            # papéis de confete caem murchos, sem nenhuma animação.
            self._tumbleweed_start = elapsed
            self._tumbleweed_arcs = [
                (random.uniform(-0.35, 0.35), random.uniform(-0.35, 0.35), random.uniform(0.45, 0.95),
                 random.uniform(0.35, 0.9), random.uniform(0, 360), random.uniform(90, 300),
                 QColor(random.choice(("#8a6a3a", "#a5814c", "#6f5430", "#c09a5e"))))
                for _ in range(34)
            ]
            for _ in range(7):
                piece = _Confetti(width / 2 + random.uniform(-90, 90) * scale, -20 * scale,
                                  random.uniform(-15, 15) * scale, random.uniform(0, 40) * scale, scale)
                piece.color = piece.color.darker(140)
                self._confetti.append(piece)
        elif self._effect == "tomatoes":
            for index in range(6):
                start = elapsed + 0.15 + index * 0.38 + random.uniform(-0.08, 0.08)
                x1 = random.uniform(0.15, 0.85) * width
                y1 = random.uniform(0.12, 0.6) * height
                x0 = x1 + random.uniform(-0.25, 0.25) * width
                self._tomatoes.append(_Tomato(start, x0, height + 120 * scale, x1, y1, random.uniform(120, 260) * scale))

    def _fire_cannons(self, count):
        """Canhões de confete nos dois cantos de baixo, atirando para dentro."""
        scale = self._scale()
        for side in (-1, 1):
            origin_x = self.width() * (0.04 if side < 0 else 0.96)
            for _ in range(count):
                angle = math.radians(random.uniform(58, 82))
                speed = random.uniform(1700, 2700) * scale
                vx = -side * math.cos(angle) * speed
                self._confetti.append(_Confetti(origin_x, self.height() + 10 * scale, vx, -math.sin(angle) * speed, scale))

    def _update_effects(self, elapsed, dt):
        scale = self._scale()
        while self._volleys and elapsed >= self._volleys[0][0]:
            self._fire_cannons(self._volleys.pop(0)[1])
        if elapsed < self._rain_until:
            amount = 170 * dt                    # ~170 papéis por segundo
            for _ in range(int(amount) + (random.random() < amount % 1)):
                self._confetti.append(_Confetti(
                    random.uniform(0, self.width()), -20 * scale,
                    random.uniform(-60, 60) * scale, random.uniform(50, 200) * scale, scale,
                ))
        gravity, drag = 900 * scale, math.exp(-2.4 * dt)
        alive = []
        for piece in self._confetti:
            piece.vy += gravity * dt
            piece.vx *= drag
            piece.vy *= drag
            piece.sway += dt * 3.0
            piece.x += (piece.vx + math.sin(piece.sway) * 40 * scale) * dt
            piece.y += piece.vy * dt
            piece.angle += piece.spin * dt
            piece.flip += piece.flip_speed * dt
            if piece.y < self.height() + 40 * scale:
                alive.append(piece)
        self._confetti = alive

        for tomato in self._tomatoes:
            if not tomato.landed and elapsed >= tomato.start + TOMATO_FLIGHT_S:
                tomato.landed = True
                self._splats.append(_Splat(tomato.x1, tomato.y1, elapsed, random.uniform(70, 105) * scale))
                self._shake_until = time.perf_counter() + 0.18

    # --- desenho -----------------------------------------------------------------
    def _scale(self):
        return min(self.width() / 1920.0, self.height() / 1080.0)

    @staticmethod
    def _fade(elapsed, start, length=0.4):
        return max(0.0, min(1.0, (elapsed - start) / length))

    @staticmethod
    def _pop(elapsed, stop):
        """Escala do dígito: cresce um pouco quando para e volta ao normal."""
        t = elapsed - stop
        if t < 0 or t > POP_S:
            return 1.0
        return 1.0 + 0.25 * (1.0 - abs(2.0 * t / POP_S - 1.0))

    def _font(self, pixels, weight=QFont.Weight.Bold):
        font = QFont(lyrics_font_family())
        font.setPixelSize(max(12, round(pixels)))
        font.setWeight(weight)
        return font

    def _set_opacity(self, painter, value):
        painter.setOpacity(value * self._scene_alpha)

    def paintEvent(self, event):
        now = time.perf_counter()
        elapsed = now - self._started
        next_elapsed = None if self._next_started is None else now - self._next_started
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        dim = 210 * self._fade(elapsed, 0.0, 0.5)
        if next_elapsed is not None:
            dim += 30 * self._fade(next_elapsed, 0.0, NEXT_FADE_S)
        painter.fillRect(self.rect(), QColor(0, 0, 0, round(dim)))

        scale = self._scale()
        self._scene_alpha = 1.0 if next_elapsed is None else 1.0 - self._fade(next_elapsed, 0.0, NEXT_FADE_S)
        if self._scene_alpha > 0:
            painter.save()
            self._paint_score_scene(painter, elapsed, scale)
            painter.restore()
        if next_elapsed is not None:
            painter.setOpacity(1.0)
            self._paint_confetti(painter)   # o confete que ainda está no ar continua caindo
            self._paint_next_song(painter, next_elapsed, scale)

    def _paint_score_scene(self, painter, elapsed, scale):
        if time.perf_counter() < self._shake_until:
            # Tremida rápida da tela a cada tomate que acerta.
            painter.translate(random.uniform(-9, 9) * scale, random.uniform(-9, 9) * scale)
        center_x = self.width() / 2.0
        center_y = self.height() / 2.0

        # "Sua nota é..."
        self._set_opacity(painter, self._fade(elapsed, 0.2))
        painter.setFont(self._font(64 * scale, QFont.Weight.DemiBold))
        painter.setPen(QColor("#ffffff"))
        title_rect = QRectF(0, center_y - 330 * scale, self.width(), 100 * scale)
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignCenter, "Sua nota é...")

        # Dígitos: a dezena (que pode ser "10" quando a nota é 100) e a unidade.
        if elapsed >= INTRO_S:
            self._set_opacity(painter, self._fade(elapsed, INTRO_S, 0.25))
            tens_value, units_value = str(self._score // 10), str(self._score % 10)
            tens_done, units_done = elapsed >= TENS_STOP_S, elapsed >= UNITS_STOP_S
            slots = [
                (tens_value if tens_done else self._rolling[0], tens_done, TENS_STOP_S, -1),
                (units_value if units_done else self._rolling[1], units_done, UNITS_STOP_S, 1),
            ]
            digit_px = 300 * scale
            slot_width = 230 * scale * (1.5 if len(tens_value) > 1 and tens_done else 1.0)
            shift = (slot_width - 230 * scale) / 2.0   # mantém "10" + "0" centralizados
            for text, done, stop, side in slots:
                width = slot_width if side < 0 else 230 * scale
                x = (center_x - width - 12 * scale if side < 0 else center_x + 12 * scale) + shift
                rect = QRectF(x, center_y - 190 * scale, width, 340 * scale)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(255, 255, 255, 28 if done else 16))
                painter.drawRoundedRect(rect, 28 * scale, 28 * scale)
                pop = self._pop(elapsed, stop) if done else 1.0
                painter.setFont(self._font(digit_px * (0.78 if len(text) > 1 else 1.0) * pop, QFont.Weight.Black))
                painter.setPen(QColor("#ffffff") if done else QColor(255, 255, 255, 150))
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

        self._set_opacity(painter, 1.0)
        if self._next_started is None:
            self._paint_confetti(painter)

        # Parabéns
        if elapsed >= MESSAGE_S:
            message, color = congratulation(self._score)
            self._set_opacity(painter, self._fade(elapsed, MESSAGE_S, 0.5))
            painter.setFont(self._font(58 * scale))
            painter.setPen(QColor(color))
            painter.drawText(
                QRectF(0, center_y + 200 * scale, self.width(), 100 * scale),
                Qt.AlignmentFlag.AlignCenter, message,
            )

        self._set_opacity(painter, 1.0)
        self._paint_tumbleweed(painter, elapsed, scale)
        self._paint_tomatoes(painter, elapsed, scale)

    def _paint_next_song(self, painter, next_elapsed, scale):
        """Cartão "Próxima música" com a contagem até ela começar."""
        song = self._next_song
        center_y = self.height() / 2.0
        width = self.width()
        painter.setOpacity(self._fade(next_elapsed, NEXT_FADE_S * 0.5, NEXT_FADE_S))

        painter.setPen(QColor(255, 255, 255, 150))
        label_font = self._font(30 * scale, QFont.Weight.DemiBold)
        label_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 6 * scale)
        painter.setFont(label_font)
        painter.drawText(QRectF(0, center_y - 300 * scale, width, 50 * scale),
                         Qt.AlignmentFlag.AlignCenter, "PRÓXIMA MÚSICA")

        title_font = self._font(76 * scale, QFont.Weight.Black)
        title = QFontMetricsF(title_font).elidedText(song.get("title") or "", Qt.TextElideMode.ElideRight, width * 0.9)
        painter.setFont(title_font)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(QRectF(0, center_y - 245 * scale, width, 110 * scale), Qt.AlignmentFlag.AlignCenter, title)

        artist = song.get("artist") or ""
        if artist:
            artist_font = self._font(40 * scale, QFont.Weight.Medium)
            artist = QFontMetricsF(artist_font).elidedText(artist, Qt.TextElideMode.ElideRight, width * 0.9)
            painter.setFont(artist_font)
            painter.setPen(QColor(255, 255, 255, 190))
            painter.drawText(QRectF(0, center_y - 140 * scale, width, 60 * scale), Qt.AlignmentFlag.AlignCenter, artist)

        # Anel que esvazia junto com a contagem.
        remaining = max(0.0, NEXT_COUNTDOWN_S - next_elapsed)
        radius = 105 * scale
        ring = QRectF(width / 2.0 - radius, center_y + 120 * scale - radius, 2 * radius, 2 * radius)
        pen = QPen(QColor(255, 255, 255, 40))
        pen.setWidthF(12 * scale)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(pen)
        painter.drawEllipse(ring)
        pen.setColor(QColor("#ffffff"))
        painter.setPen(pen)
        painter.drawArc(ring, 90 * 16, round(360 * 16 * remaining / NEXT_COUNTDOWN_S))
        second = math.ceil(remaining) or 1
        pulse = 1.0 + 0.12 * max(0.0, 1.0 - (second - remaining) / 0.25)   # pulsa a cada segundo
        painter.setFont(self._font(120 * scale * pulse, QFont.Weight.Black))
        painter.drawText(ring, Qt.AlignmentFlag.AlignCenter, str(second))

        painter.setFont(self._font(32 * scale, QFont.Weight.Medium))
        painter.setPen(QColor(255, 255, 255, 170))
        painter.drawText(QRectF(0, center_y + 260 * scale, width, 60 * scale),
                         Qt.AlignmentFlag.AlignCenter, "Hora de passar o microfone!")

    def _paint_tumbleweed(self, painter, elapsed, scale):
        if self._tumbleweed_start is None:
            return
        progress = (elapsed - self._tumbleweed_start) / TUMBLEWEED_S
        if not 0 <= progress <= 1:
            return
        radius = 55 * scale
        ground = self.height() * 0.97
        x = -radius + (self.width() + 2 * radius) * progress
        hop = abs(math.sin(progress * math.pi * 6)) * 30 * scale    # quica pelo chão
        painter.save()
        # Sombra no chão, menor quando a bola está no alto.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 90))
        shadow = radius * (1.0 - hop / (160 * scale))
        painter.drawEllipse(QPointF(x, ground), shadow, shadow * 0.18)
        painter.translate(x, ground - radius - hop)
        painter.rotate(math.degrees(x / radius))                    # rola conforme anda
        painter.setBrush(Qt.BrushStyle.NoBrush)
        pen = QPen()
        pen.setWidthF(max(1.5, 3.2 * scale))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        for offset_x, offset_y, size_x, size_y, start, span, color in self._tumbleweed_arcs:
            pen.setColor(color)
            painter.setPen(pen)
            rect = QRectF((offset_x - size_x / 2) * radius, (offset_y - size_y / 2) * radius,
                          size_x * radius, size_y * radius)
            painter.drawArc(rect, round(start * 16), round(span * 16))
        painter.restore()

    def _paint_confetti(self, painter):
        painter.setPen(Qt.PenStyle.NoPen)
        for piece in self._confetti:
            painter.save()
            painter.translate(piece.x, piece.y)
            painter.rotate(piece.angle)
            painter.scale(1.0, max(0.08, abs(math.cos(piece.flip))))   # papel virando no ar
            painter.setBrush(piece.color)
            painter.drawRect(QRectF(-piece.w / 2, -piece.h / 2, piece.w, piece.h))
            painter.restore()

    def _paint_tomatoes(self, painter, elapsed, scale):
        painter.setPen(Qt.PenStyle.NoPen)
        for splat in self._splats:
            self._paint_splat(painter, splat, elapsed)
        for tomato in self._tomatoes:
            progress = (elapsed - tomato.start) / TOMATO_FLIGHT_S
            if tomato.landed or progress < 0:
                continue
            x = tomato.x0 + (tomato.x1 - tomato.x0) * progress
            y = tomato.y0 + (tomato.y1 - tomato.y0) * progress - tomato.arc * 4 * progress * (1 - progress)
            radius = (22 + 38 * progress) * scale   # cresce: vem na direção da tela
            painter.save()
            painter.translate(x, y)
            painter.rotate(tomato.spin * progress)
            gradient = QRadialGradient(QPointF(-radius * 0.35, -radius * 0.35), radius * 1.4)
            gradient.setColorAt(0.0, QColor("#ff7a5c"))
            gradient.setColorAt(0.5, QColor("#e0261b"))
            gradient.setColorAt(1.0, QColor("#8f0f0a"))
            painter.setBrush(gradient)
            painter.drawEllipse(QPointF(0, 0), radius, radius * 0.9)
            painter.setBrush(QColor("#2f8f2f"))   # cabinho verde em estrela, no topo
            painter.translate(0, -radius * 0.72)
            for leaf in range(5):
                painter.save()
                painter.rotate(leaf * 72)
                painter.drawEllipse(QRectF(-radius * 0.08, -radius * 0.36, radius * 0.16, radius * 0.36))
                painter.restore()
            painter.restore()

    def _paint_splat(self, painter, splat, elapsed):
        age = elapsed - splat.born
        grow = min(1.0, age / 0.08)              # espalha em poucos quadros
        opacity = 1.0 if age < 4.0 else max(0.0, 1.0 - (age - 4.0) / 1.5)
        if opacity <= 0:
            return
        painter.save()
        painter.setOpacity(opacity)
        painter.translate(splat.x, splat.y)
        painter.scale(grow, grow)

        # Escorridos descendo devagar.
        painter.setBrush(QColor("#b3170f"))
        for offset_x, width, length in splat.drips:
            drip = length * min(1.0, age / 2.5)
            painter.drawRoundedRect(QRectF(offset_x - width / 2, 0, width, splat.radius * 0.6 + drip), width / 2, width / 2)
            painter.drawEllipse(QPointF(offset_x, splat.radius * 0.6 + drip), width * 0.7, width * 0.7)

        # Mancha principal com contorno irregular e gotas em volta.
        path = QPainterPath()
        points = [QPointF(math.cos(a) * r, math.sin(a) * r) for a, r in splat.outline]
        path.moveTo((points[-1] + points[0]) / 2)
        for index, point in enumerate(points):
            following = points[(index + 1) % len(points)]
            path.quadTo(point, (point + following) / 2)
        painter.setBrush(QColor("#c41e14"))
        painter.drawPath(path)
        for angle, distance, size in splat.drops:
            painter.drawEllipse(QPointF(math.cos(angle) * distance, math.sin(angle) * distance), size, size)
        painter.setBrush(QColor("#e8432f"))
        painter.drawEllipse(QPointF(-splat.radius * 0.15, -splat.radius * 0.15), splat.radius * 0.45, splat.radius * 0.38)
        painter.setBrush(QColor("#f7d070"))   # sementes
        for seed_x, seed_y in splat.seeds:
            painter.drawEllipse(QRectF(seed_x, seed_y, splat.radius * 0.07, splat.radius * 0.11))
        painter.restore()
