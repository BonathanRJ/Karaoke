"""Pontuação do karaokê: escuta o microfone e compara com a voz original.

Durante a música, o microfone é analisado a cada ~50 ms (volume e nota cantada).
Quando a música tem vocal separado (music/vocal), a afinação de quem canta é
comparada com a da voz original; sem vocal, conta apenas se a pessoa cantou nos
trechos com letra. Sem microfone (ou sem dados suficientes), a nota é sorteada,
como nas máquinas de bar.
"""
import random
import threading

import numpy as np
from PySide6.QtCore import QObject, QTimer
from PySide6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices

ANALYSIS_RATE = 16000        # taxa usada na análise da voz original
FRAME_MS = 20                # um valor de afinação da voz original a cada 20 ms
WINDOW = 1024                # amostras por análise (64 ms a 16 kHz)
BUCKET_MS = 50               # resolução das medições do microfone
MIN_PITCH_HZ = 80.0
MAX_PITCH_HZ = 1000.0
VOICED_CLARITY = 0.6         # correlação mínima para considerar que há nota definida
MATCH_SLACK_MS = 150         # atraso tolerado entre o microfone e a voz original
LYRIC_LINE_MAX_MS = 6000     # sem vocal: cada linha conta como canto por até 6 s
FALLBACK_RANGE = (65, 98)    # nota sorteada quando não há como medir


def _detect_pitch(frames, rate):
    """Nota (MIDI, fracionária) e clareza de cada linha de `frames` (autocorrelação normalizada)."""
    frames = frames - frames.mean(axis=1, keepdims=True)
    count, size = frames.shape
    spectrum = np.fft.rfft(frames, n=2 * size, axis=1)
    corr = np.fft.irfft(spectrum * np.conj(spectrum), axis=1)[:, :size]
    energy = np.cumsum(frames ** 2, axis=1)
    total = energy[:, -1:]
    min_lag = max(2, int(rate / MAX_PITCH_HZ))
    max_lag = min(size - 1, int(rate / MIN_PITCH_HZ))
    lags = np.arange(min_lag, max_lag + 1)
    head = energy[:, size - 1 - lags]                    # energia de x[0:N-lag]
    tail = total - energy[:, lags - 1]                   # energia de x[lag:N]
    clarity = corr[:, lags] / np.sqrt(np.maximum(head * tail, 1e-12))
    # Evita o "pico" logo no início, onde a correlação ainda está descendo do lag 0.
    dips = np.minimum.accumulate(clarity, axis=1)
    clarity = np.where(clarity > dips + 0.05, clarity, 0.0)
    # O primeiro pico perto do máximo é o período; os seguintes são múltiplos dele.
    peak = clarity.max(axis=1, keepdims=True)
    first = np.argmax(clarity >= peak * 0.9, axis=1)
    best = np.empty_like(first)
    for row, start in enumerate(first):
        stop = start + 1
        while stop < len(lags) and clarity[row, stop] >= clarity[row, stop - 1]:
            stop += 1
        best[row] = stop - 1   # topo do pico que começa no cruzamento
    best_clarity = clarity[np.arange(count), best]
    frequency = rate / lags[best]
    midi = 69.0 + 12.0 * np.log2(frequency / 440.0)
    return np.where(best_clarity >= VOICED_CLARITY, midi, np.nan), best_clarity


def analyze_reference(vocals_path):
    """Nota da voz original a cada FRAME_MS (NaN quando não há canto)."""
    import av

    resampler = av.AudioResampler(format="flt", layout="mono", rate=ANALYSIS_RATE)
    chunks = []
    with av.open(str(vocals_path)) as container:
        for frame in container.decode(audio=0):
            for resampled in resampler.resample(frame):
                chunks.append(resampled.to_ndarray().reshape(-1))
    if not chunks:
        return None
    samples = np.concatenate(chunks).astype(np.float32)
    hop = ANALYSIS_RATE * FRAME_MS // 1000
    count = (len(samples) - WINDOW) // hop
    if count < 10:
        return None
    pitches = np.empty(count, dtype=np.float32)
    rms = np.empty(count, dtype=np.float32)
    for start in range(0, count, 512):
        stop = min(count, start + 512)
        index = np.arange(start, stop)[:, None] * hop + np.arange(WINDOW)[None, :]
        frames = samples[index]
        rms[start:stop] = np.sqrt(np.mean(frames ** 2, axis=1))
        pitches[start:stop], _ = _detect_pitch(frames, ANALYSIS_RATE)
    # Respiração e vazamento do instrumental ficam bem abaixo do volume da voz.
    loud = np.percentile(rms, 95)
    pitches[rms < loud * 0.12] = np.nan
    return pitches


def _pitch_class_distance(a, b):
    """Distância em semitons ignorando a oitava (homens e mulheres cantam a mesma música)."""
    diff = np.abs(a - b) % 12.0
    return np.minimum(diff, 12.0 - diff)


def _to_mono_float(data, fmt):
    sample_format = fmt.sampleFormat()
    if sample_format == QAudioFormat.SampleFormat.Int16:
        samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
    elif sample_format == QAudioFormat.SampleFormat.Int32:
        samples = np.frombuffer(data, dtype=np.int32).astype(np.float32) / 2147483648.0
    elif sample_format == QAudioFormat.SampleFormat.Float:
        samples = np.frombuffer(data, dtype=np.float32)
    elif sample_format == QAudioFormat.SampleFormat.UInt8:
        samples = (np.frombuffer(data, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        return np.zeros(0, dtype=np.float32)
    channels = max(1, fmt.channelCount())
    if channels > 1:
        samples = samples[: len(samples) // channels * channels].reshape(-1, channels).mean(axis=1)
    return samples


class SingingScorer(QObject):
    """Grava o canto de uma música e calcula a nota de 0 a 100 no final."""

    def __init__(self, player, parent=None):
        super().__init__(parent)
        self.player = player
        self._source = None
        self._device = None
        self._format = None
        self._buffer = np.zeros(0, dtype=np.float32)
        self._buckets = {}          # posição (ms // BUCKET_MS) -> (volume, nota MIDI ou NaN)
        self._reference = None
        self._reference_token = 0
        self._lines = []
        self._timer = QTimer(self)
        self._timer.setInterval(BUCKET_MS)
        self._timer.timeout.connect(self._capture)

    @property
    def has_microphone(self):
        return self._source is not None

    def start(self, song, lines):
        """Recomeça a avaliação para uma nova música."""
        self._buckets = {}
        self._lines = list(lines)
        self._reference = None
        self._reference_token += 1
        vocals = song.get("vocals_path") if hasattr(song, "get") else None
        if vocals:
            token = self._reference_token
            threading.Thread(target=self._load_reference, args=(vocals, token), daemon=True).start()
        self._open_microphone()

    def stop(self):
        self._timer.stop()
        if self._source is not None:
            self._source.stop()
            self._source = None
            self._device = None

    def _load_reference(self, vocals_path, token):
        try:
            reference = analyze_reference(vocals_path)
        except Exception:
            reference = None
        if token == self._reference_token:
            self._reference = reference

    def _open_microphone(self):
        if self._source is not None:
            self._timer.start()
            return
        device = QMediaDevices.defaultAudioInput()
        if device.isNull():
            return
        fmt = QAudioFormat()
        fmt.setSampleRate(ANALYSIS_RATE)
        fmt.setChannelCount(1)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        if not device.isFormatSupported(fmt):
            fmt = device.preferredFormat()
        source = QAudioSource(device, fmt, self)
        stream = source.start()
        if stream is None:
            return
        self._source, self._device, self._format = source, stream, fmt
        self._buffer = np.zeros(0, dtype=np.float32)
        self._timer.start()

    def _capture(self):
        if self._device is None:
            return
        data = bytes(self._device.readAll())
        if not data:
            return
        samples = _to_mono_float(data, self._format)
        rate = self._format.sampleRate()
        window = max(256, rate * WINDOW // ANALYSIS_RATE)
        self._buffer = np.concatenate([self._buffer, samples])[-window:]
        if not self.player.is_playing() or len(self._buffer) < window:
            return
        frame = self._buffer[None, :]
        volume = float(np.sqrt(np.mean(frame ** 2)))
        pitch, _ = _detect_pitch(frame.astype(np.float64), rate)
        self._buckets[self.player.position() // BUCKET_MS] = (volume, float(pitch[0]))

    # --- nota ------------------------------------------------------------------
    def _lyric_mask(self, times):
        mask = np.zeros(len(times), dtype=bool)
        starts = [line["time"] for line in self._lines]
        for index, line in enumerate(self._lines):
            if not line["text"].strip():
                continue
            end = starts[index + 1] if index + 1 < len(starts) else line["time"] + LYRIC_LINE_MAX_MS
            end = min(end, line["time"] + LYRIC_LINE_MAX_MS)
            mask |= (times >= line["time"]) & (times < end)
        return mask

    def final_score(self):
        """Nota de 0 a 100 da música que acabou de tocar."""
        if not self._buckets:
            return random.randint(*FALLBACK_RANGE)
        keys = np.array(sorted(self._buckets))
        times = keys * BUCKET_MS
        volume = np.array([self._buckets[k][0] for k in keys])
        sung = np.array([self._buckets[k][1] for k in keys])

        reference = self._reference
        ref_near = None
        if reference is not None:
            # Notas da voz original perto de cada medição, tolerando o atraso do microfone.
            slack = MATCH_SLACK_MS // FRAME_MS
            centers = times // FRAME_MS
            offsets = np.arange(-slack, slack + 1)
            index = np.clip(centers[:, None] + offsets[None, :], 0, len(reference) - 1)
            ref_near = reference[index]
            singing = ~np.all(np.isnan(ref_near), axis=1)
        else:
            singing = self._lyric_mask(times)

        if singing.sum() < 20:
            return random.randint(*FALLBACK_RANGE)

        # O microfone também capta o instrumental: o limite de "está cantando" é
        # medido nos trechos sem voz da própria música.
        quiet = volume[~singing]
        floor = np.median(quiet) if len(quiet) >= 20 else np.percentile(volume, 20)
        threshold = max(floor * 1.8, 0.004)
        active = volume > threshold

        presence = float(np.mean(active[singing]))
        raw = presence * 0.85            # sem vocal: só dá para saber se cantou
        if ref_near is not None:
            voiced = singing & active & ~np.isnan(sung)
            if voiced.sum() >= 10:
                distance = _pitch_class_distance(ref_near[voiced], sung[voiced][:, None])
                distance = np.nanmin(distance, axis=1)
                credit = np.clip((3.0 - distance) / 2.0, 0.0, 1.0)   # até 1 semitom vale tudo
                raw = presence * (0.4 + 0.6 * float(np.mean(credit)))

        score = 35 + 65 * min(1.0, raw / 0.9) ** 0.8
        return int(max(0, min(100, round(score))))
