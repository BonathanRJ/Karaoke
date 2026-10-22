"""Busca de letras já sincronizadas por outras pessoas em serviços gratuitos.

Fontes, em ordem:
1. LRCLIB (lrclib.net) — base aberta e gratuita, com API oficial.
2. NetEase Cloud Music — via pacote `syncedlyrics`.

O Musixmatch, o Megalobiz e o Lyricsify ficaram de fora: o Musixmatch não oferece
letra sincronizada de graça e bloqueou o acesso não oficial; os outros dois deixaram
de responder às buscas.
"""

import re
import time
import unicodedata
from difflib import SequenceMatcher

from .lrc import parse_lrc

LRCLIB_API = 'https://lrclib.net/api'
USER_AGENT = 'Karaoke-local/1.20 (uso pessoal)'
REQUEST_TIMEOUT = (5, 15)
LRCLIB_RETRIES = 3            # o LRCLIB responde 503 quando está sobrecarregado
MAX_DURATION_DIFF_S = 12      # versões com duração muito diferente são descartadas
MIN_SYNCED_LINES = 4

# Ajuste automático de atraso/adiantamento usando o vocal separado.
OFFSET_SEARCH_MS = 15_000
OFFSET_MIN_MS = 1000           # diferenças menores são ruído da estimativa
OFFSET_MIN_GAIN = 0.01
OFFSET_FRAME_MS = 20

# Créditos que o NetEase inclui no começo da letra (compositor, produtor...).
_CREDIT_LINE = re.compile(r'(作词|作曲|编曲|制作|混音|母带|和声|吉他|贝斯|鼓|监制|出品|录音|词|曲)\s*[:：]')
_TITLE_NOISE = re.compile(
    r'\s*[\(\[][^\)\]]*(official|oficial|audio|áudio|video|vídeo|clipe|clip|visualizer|'
    r'lyric|letra|legendado|hd|4k|mv)[^\)\]]*[\)\]]',
    re.IGNORECASE,
)


class LyricsNotFound(Exception):
    pass


def normalize_text(text):
    text = unicodedata.normalize('NFKD', text or '')
    text = ''.join(char for char in text if not unicodedata.combining(char))
    return re.sub(r'[^a-z0-9]+', ' ', text.lower()).strip()


def text_similarity(a, b):
    return SequenceMatcher(None, normalize_text(a), normalize_text(b)).ratio()


def clean_title(title):
    """Remove marcações de vídeo do YouTube: "(Official Video)", "[bpOSxM0rNPM]", etc."""
    title = re.sub(r'#U([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), title or '')
    title = _TITLE_NOISE.sub('', title)
    title = re.sub(r'\s*\[[^\]]*\]', '', title)       # ids e sufixos entre colchetes
    title = re.sub(r'[？?！!]+$', '', title.strip())
    return re.sub(r'\s{2,}', ' ', title).strip(' -')


def _session():
    import requests

    session = requests.Session()
    session.headers['User-Agent'] = USER_AGENT
    return session


def _lrclib_request(session, endpoint, params):
    for attempt in range(LRCLIB_RETRIES):
        response = session.get(f'{LRCLIB_API}/{endpoint}', params=params, timeout=REQUEST_TIMEOUT)
        if response.status_code == 404:
            return None
        if response.status_code in (429, 500, 502, 503, 504) and attempt + 1 < LRCLIB_RETRIES:
            time.sleep(1.5 * (attempt + 1))
            continue
        response.raise_for_status()
        return response.json()
    return None


def _lrclib_candidates(session, artist, title, duration_s):
    params = {'artist_name': artist, 'track_name': title}
    if duration_s:
        # Busca exata pela duração: é a forma recomendada pelo próprio LRCLIB.
        track = _lrclib_request(session, 'get', {**params, 'duration': round(duration_s)})
        if track and track.get('syncedLyrics'):
            yield track['syncedLyrics'], track.get('duration')

    results = _lrclib_request(session, 'search', params) or []
    if not results:
        results = _lrclib_request(session, 'search', {'q': f'{artist} {title}'}) or []
    results = [
        track for track in results
        if track.get('syncedLyrics')
        and text_similarity(track.get('trackName'), title) >= 0.6
        and (not artist or text_similarity(track.get('artistName'), artist) >= 0.5)
    ]
    if duration_s:
        results.sort(key=lambda track: abs((track.get('duration') or 0) - duration_s))
    for track in results[:5]:
        yield track['syncedLyrics'], track.get('duration')


def _netease_candidates(artist, title):
    try:
        from syncedlyrics.providers import NetEase
    except ImportError:
        return
    provider = NetEase()
    track = provider.search_track(f'{title} {artist}'.strip())
    if not track:
        return
    lyrics = provider.get_lrc_by_id(track['id'])
    if lyrics and lyrics.synced:
        duration = track.get('duration') or track.get('dt')
        yield lyrics.synced, (duration / 1000 if duration else None)


def _apply_offset_tag(text, lines):
    # Tag [offset:+500] do formato LRC: valores positivos adiantam a letra.
    match = re.search(r'^\[offset:\s*([+-]?\d+)\]', text, re.IGNORECASE | re.MULTILINE)
    if match:
        offset = int(match.group(1))
        for line in lines:
            line['time'] = max(0, line['time'] - offset)
    return lines


def _clean_lines(text):
    lines = parse_lrc(text)
    lines = _apply_offset_tag(text, lines)
    result = []
    for line in lines:
        content = line['text'].strip()
        if _CREDIT_LINE.search(content):
            continue
        result.append({'time': line['time'], 'text': content, 'words': []})
    # Remove linhas vazias repetidas e no começo/fim (pausas sem sentido).
    while result and not result[0]['text']:
        result.pop(0)
    while result and not result[-1]['text']:
        result.pop()
    return result


def _plausible(lines, source_duration_s, duration_s):
    timed_text = [line for line in lines if line['text']]
    if len(timed_text) < MIN_SYNCED_LINES:
        return False
    if duration_s:
        if source_duration_s and abs(source_duration_s - duration_s) > MAX_DURATION_DIFF_S:
            return False
        if lines[-1]['time'] / 1000 > duration_s + MAX_DURATION_DIFF_S:
            return False
    return True


def find_synced_lyrics(artist, title, duration_s=None):
    """Retorna (linhas, nome da fonte). Lança LyricsNotFound se nada servir."""
    title = clean_title(title)
    artist = (artist or '').strip()
    if not title:
        raise LyricsNotFound('A música não tem título para buscar.')

    errors = []
    session = _session()
    sources = (
        ('LRCLIB', lambda: _lrclib_candidates(session, artist, title, duration_s)),
        ('NetEase', lambda: _netease_candidates(artist, title)),
    )
    for name, candidates in sources:
        try:
            for text, source_duration in candidates():
                lines = _clean_lines(text)
                if _plausible(lines, source_duration, duration_s):
                    return lines, name
        except Exception as error:  # rede fora do ar, resposta inesperada etc.
            errors.append(f'{name}: {error}')

    detail = ('\n\nFalhas de conexão:\n' + '\n'.join(errors)) if errors else ''
    raise LyricsNotFound(
        f'Nenhuma letra sincronizada compatível foi encontrada para "{artist} - {title}".{detail}'
    )


def _vocal_envelope(vocals_path):
    """Intensidade da voz a cada 20 ms, normalizada entre 0 e 1."""
    import av
    import numpy as np

    rate = 8000
    hop = rate * OFFSET_FRAME_MS // 1000
    resampler = av.AudioResampler(format='flt', layout='mono', rate=rate)
    chunks = []
    with av.open(str(vocals_path)) as container:
        for frame in container.decode(audio=0):
            for resampled in resampler.resample(frame):
                chunks.append(resampled.to_ndarray().reshape(-1))
    if not chunks:
        return None
    samples = np.concatenate(chunks)
    frames = len(samples) // hop
    if frames < 10:
        return None
    rms = np.sqrt(np.mean(samples[:frames * hop].reshape(frames, hop) ** 2, axis=1))
    level = np.log10(rms + 1e-4)
    low, high = np.percentile(level, 10), np.percentile(level, 95)
    return np.clip((level - low) / max(1e-6, high - low), 0.0, 1.0)


def _predicted_activity(lines, length, shift_frames):
    """1 onde o LRC indica voz: do início de cada linha até o fim estimado dela."""
    import numpy as np

    activity = np.zeros(length)
    for index, line in enumerate(lines):
        if not line['text']:
            continue
        start = line['time']
        following = lines[index + 1]['time'] if index + 1 < len(lines) else start + 4000
        # Linhas seguidas de pausa longa não duram a pausa inteira.
        duration = min(following - start, max(1500, len(line['text']) * 90))
        first = max(0, start // OFFSET_FRAME_MS + shift_frames)
        last = min(length, (start + duration) // OFFSET_FRAME_MS + shift_frames)
        if last > first:
            activity[first:last] = 1
    return activity


def estimate_offset(vocals_path, lines):
    """Descobre quanto o LRC está adiantado/atrasado em relação ao vocal separado.

    Compara o padrão "voz/sem voz" previsto pelo LRC (incluindo as pausas
    instrumentais) com a intensidade real do vocal, testando deslocamentos de até
    ±15 s. Retorna os ms a somar aos tempos, ou 0 quando o ganho não é claro.
    """
    import numpy as np

    envelope = _vocal_envelope(vocals_path)
    if envelope is None or sum(1 for line in lines if line['text']) < MIN_SYNCED_LINES:
        return 0

    span = OFFSET_SEARCH_MS // OFFSET_FRAME_MS
    signal = np.concatenate([np.zeros(span), envelope, np.zeros(span)])
    predicted = _predicted_activity(lines, len(signal), span)
    signal = signal - signal.mean()
    predicted = predicted - predicted.mean()
    norm = np.linalg.norm(signal) * np.linalg.norm(predicted)
    if norm == 0:
        return 0

    lags = np.arange(-span, span + 1)
    scores = np.array([np.dot(np.roll(predicted, lag), signal) for lag in lags]) / norm
    best = int(np.argmax(scores))
    gain = scores[best] - scores[span]          # comparação com "sem ajuste"
    offset_ms = int(lags[best]) * OFFSET_FRAME_MS
    if abs(offset_ms) < OFFSET_MIN_MS or gain < OFFSET_MIN_GAIN:
        return 0
    return offset_ms


def shift_to_vocals(lines, vocals_path):
    """Corrige o atraso/adiantamento das linhas comparando com o vocal separado.

    Devolve o deslocamento aplicado em ms (0 quando não houve ajuste).
    """
    try:
        offset_ms = estimate_offset(vocals_path, lines)
    except Exception:
        return 0
    for line in lines:
        line['time'] = max(0, line['time'] + offset_ms)
    return offset_ms


def audio_duration_seconds(path):
    try:
        import av

        with av.open(str(path)) as container:
            if container.duration:
                return container.duration / 1_000_000
    except Exception:
        pass
    return None
