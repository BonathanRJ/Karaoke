import re
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

from .lrc import format_lrc

# Modelo usado para sincronizar a letra. O "large-v3-turbo" é multilíngue e reconhece
# português cantado muito melhor que o "small"; o download (~1,6 GB) ocorre só na
# primeira vez. Se ele não puder ser carregado, o "small" é usado como reserva.
ALIGNMENT_MODEL = 'large-v3-turbo'
FALLBACK_MODEL = 'small'

# Limiares de semelhança entre a palavra da letra e a palavra reconhecida.
STRONG_MATCH = 0.8
WEAK_MATCH = 0.5

# Espaçamento usado quando não há nenhuma referência de tempo para uma palavra.
WORD_STEP_MS = 300

_MODEL_CACHE = {}

# Formas coloquiais que o Whisper costuma escrever por extenso (ou vice-versa).
_EQUIVALENTS = {
    'pra': 'para', 'pro': 'para', 'pros': 'para', 'pras': 'para',
    'ta': 'esta', 'tava': 'estava', 'tao': 'estao', 'to': 'estou', 'tamo': 'estamos',
    'ce': 'voce', 'oce': 'voce', 'vc': 'voce', 'ces': 'voces',
    'num': 'nao', 'nois': 'nos', 'mo': 'muito', 'mto': 'muito',
    'cause': 'because', 'cuz': 'because', 'coz': 'because',
    'gonna': 'goingto', 'wanna': 'wantto', 'gotta': 'gotto',
    'nothin': 'nothing', 'somethin': 'something', 'lovin': 'loving',
}

# Palavras frequentes de cada idioma. O idioma é deduzido da própria letra porque a
# detecção do Whisper olha só o começo do áudio e erra com frequência em músicas
# brasileiras (introdução instrumental, "yeah", trechos em inglês etc.).
_LANGUAGE_HINTS = {
    'pt': {'que', 'nao', 'voce', 'eu', 'com', 'pra', 'para', 'uma', 'meu', 'minha', 'mais',
           'tem', 'ta', 'esta', 'tudo', 'bem', 'quando', 'sem', 'nos', 'vai', 'sou', 'seu',
           'sua', 'isso', 'aqui', 'ela', 'ele', 'mas', 'coracao', 'amor', 'agora', 'tambem'},
    'en': {'the', 'you', 'and', 'i', 'my', 'me', 'your', 'it', 'is', 'to', 'in', 'of',
           'that', 'we', 'dont', 'im', 'love', 'what', 'all', 'just', 'know', 'baby', 'oh'},
    'es': {'que', 'el', 'la', 'y', 'yo', 'tu', 'te', 'mi', 'por', 'una', 'con', 'pero',
           'como', 'mas', 'quiero', 'eres', 'esta', 'todo', 'amor', 'corazon', 'porque'},
}


def _load_model(model_size):
    try:
        from faster_whisper import WhisperModel
    except ImportError as error:
        raise RuntimeError(
            "A sincronização automática precisa do pacote 'faster-whisper'. "
            "Instale as dependências do projeto novamente com: "
            "python -m pip install -r requirements.txt"
        ) from error
    if model_size not in _MODEL_CACHE:
        try:
            _MODEL_CACHE[model_size] = WhisperModel(model_size, device='auto', compute_type='int8')
        except Exception:
            # Sem internet para baixar o modelo maior, por exemplo.
            if model_size == FALLBACK_MODEL:
                raise
            return _load_model(FALLBACK_MODEL)
    return _MODEL_CACHE[model_size]


def _normalize_word(value):
    value = unicodedata.normalize('NFKD', value or '')
    value = ''.join(char for char in value if not unicodedata.combining(char))
    value = value.lower()
    return ''.join(char for char in value if char.isalnum())


def _canonical(value):
    normalized = _normalize_word(value)
    return _EQUIVALENTS.get(normalized, normalized)


def detect_lyrics_language(lyrics_text):
    words = [_normalize_word(token) for token in (lyrics_text or '').split()]
    words = [word for word in words if word]
    if not words:
        return None
    scores = {
        language: sum(1 for word in words if word in hints)
        for language, hints in _LANGUAGE_HINTS.items()
    }
    # Português e espanhol compartilham várias palavras; letras típicas desempatam.
    lowered = lyrics_text.lower()
    if re.search(r'[ãõç]', lowered):
        scores['pt'] += len(words) * 0.05
    if re.search(r'[ñ¿¡]', lowered):
        scores['es'] += len(words) * 0.05
    language, score = max(scores.items(), key=lambda item: item[1])
    if score < max(3, len(words) * 0.08):
        return None
    return language


def _manual_tokens(lyrics_text):
    lines = []
    flat = []
    global_index = 0
    for raw_line in lyrics_text.splitlines():
        # Linhas vazias são separadores visuais e não precisam de timestamp no LRC.
        if not raw_line.strip():
            continue
        line_index = len(lines)
        tokens = re.findall(r'\S+\s*', raw_line)
        line_tokens = []
        for token in tokens:
            item = {
                'index': global_index,
                'text': token,
                'canonical': _canonical(token),
                'line': line_index,
            }
            line_tokens.append(item)
            flat.append(item)
            global_index += 1
        if line_tokens:
            lines.append({'text': raw_line, 'tokens': line_tokens})
    return lines, flat


def _hotwords(manual_words):
    # Vocabulário da letra, sem repetições, para orientar o reconhecimento em cada
    # janela de 30 s (gírias, nomes próprios e palavras pouco comuns).
    seen = set()
    result = []
    for item in manual_words:
        word = re.sub(r"[^\w'-]+", '', item['text'].lower())
        if word and word not in seen:
            seen.add(word)
            result.append(word)
    return ' '.join(result) or None


def _transcribe_words(model, audio_path, language, hotwords, use_vad, progress):
    options = {
        'language': language,
        'word_timestamps': True,
        'beam_size': 5,
        'condition_on_previous_text': False,
        'hotwords': hotwords,
        # Descarta trechos "inventados" em silêncio (ex.: "Legendas pela comunidade Amara.org").
        'hallucination_silence_threshold': 2.0,
        'language_detection_segments': 4,
        'vad_filter': use_vad,
    }
    if use_vad:
        options['vad_parameters'] = {
            'threshold': 0.35,
            'min_silence_duration_ms': 700,
            'speech_pad_ms': 300,
        }
    segments, info = model.transcribe(str(audio_path), **options)

    duration = float(getattr(info, 'duration', 0) or 0)
    words = []
    for segment in segments:
        for word in segment.words or []:
            text = word.word or ''
            canonical = _canonical(text)
            if not canonical:
                continue
            start = float(word.start if word.start is not None else segment.start or 0.0)
            end = float(word.end if word.end is not None else start + 0.25)
            words.append({
                'canonical': canonical,
                'start': max(0, int(start * 1000)),
                'end': max(0, int(end * 1000)),
            })
        if progress and duration:
            progress(min(1.0, float(segment.end or 0) / duration))
    return words, getattr(info, 'language', None)


def _recognized_words(audio_path, model_size=None, language=None, hotwords=None, progress=None):
    model = _load_model(model_size or ALIGNMENT_MODEL)
    words, detected = _transcribe_words(model, audio_path, language, hotwords, True, progress)
    if not words:
        # O VAD pode descartar voz cantada muito baixa ou com muito efeito; tenta sem ele.
        words, detected = _transcribe_words(model, audio_path, language, hotwords, False, progress)

    if not words:
        raise RuntimeError(
            'Nenhuma voz utilizável foi reconhecida no stem vocal. '
            'Confirme se a separação gerou um arquivo de voz audível.'
        )
    return words, detected


@lru_cache(maxsize=None)
def _similarity(a, b):
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def _pair_score(similarity):
    if similarity >= 0.999:
        return 2.0
    if similarity >= STRONG_MATCH:
        return 1.5
    if similarity >= WEAK_MATCH:
        return 0.4
    return -0.9


def _align_words(manual_words, recognized_words):
    """Alinhamento global (Needleman-Wunsch) com comparação aproximada.

    Diferente de exigir palavras idênticas, aceita grafias parecidas ("tá"/"esta",
    "ce"/"você", erros leves do reconhecimento) e mantém a ordem da letra mesmo com
    refrões repetidos. Palavras reconhecidas sobrando no início/fim não custam nada.
    """
    manual = [item['canonical'] for item in manual_words]
    recognized = [item['canonical'] for item in recognized_words]
    n, m = len(manual), len(recognized)
    gap_recognized = -0.5

    # 0 = diagonal, 1 = pula palavra da letra, 2 = pula palavra reconhecida
    back = [bytearray(m + 1) for _ in range(n + 1)]
    previous = [0.0] * (m + 1)
    for j in range(1, m + 1):
        back[0][j] = 2
    for i in range(1, n + 1):
        word = manual[i - 1]
        # Tokens sem letras (ex.: "-", "...") podem ser pulados sem custo.
        gap_manual = 0.0 if not word else -0.6
        current = [previous[0] + gap_manual] + [0.0] * m
        back[i][0] = 1
        row = back[i]
        for j in range(1, m + 1):
            diagonal = previous[j - 1] + (_pair_score(_similarity(word, recognized[j - 1])) if word else -5.0)
            skip_manual = previous[j] + gap_manual
            skip_recognized = current[j - 1] + (0.0 if i == n else gap_recognized)
            if diagonal >= skip_manual and diagonal >= skip_recognized:
                current[j] = diagonal
            elif skip_manual >= skip_recognized:
                current[j] = skip_manual
                row[j] = 1
            else:
                current[j] = skip_recognized
                row[j] = 2
        previous = current

    pairs = []
    i, j = n, m
    while i > 0 and j > 0:
        move = back[i][j]
        if move == 0:
            pairs.append((i - 1, j - 1, _similarity(manual[i - 1], recognized[j - 1])))
            i, j = i - 1, j - 1
        elif move == 1:
            i -= 1
        else:
            j -= 1
    pairs.reverse()
    return pairs


def _build_anchor_map(manual_words, recognized_words):
    anchors = {}
    strong = 0
    last_start = -1
    for manual_index, recognized_index, similarity in _align_words(manual_words, recognized_words):
        if similarity < WEAK_MATCH:
            continue
        start = recognized_words[recognized_index]['start']
        # Ignora timestamps fora de ordem devolvidos pelo reconhecimento.
        if start < last_start:
            continue
        last_start = start
        anchors[manual_index] = recognized_index
        if similarity >= STRONG_MATCH:
            strong += 1
    return anchors, strong


def _fill_run(run, left, right, manual_words, recognized_words, times):
    """Define tempos para palavras da letra que não tiveram par no reconhecimento.

    `left`/`right` são os índices (letra, reconhecido) das âncoras vizinhas, ou None.
    """
    left_rec = left[1] if left else -1
    right_rec = right[1] if right else len(recognized_words)
    spare = recognized_words[left_rec + 1:right_rec]

    # O Whisper ouviu algo nesse trecho, mas escreveu diferente: aproveita esses tempos.
    if spare and 0.5 <= len(spare) / len(run) <= 2.5:
        for k, index in enumerate(run):
            times[index] = spare[int(k * len(spare) / len(run))]['start']
        return

    left_time = recognized_words[left[1]]['end'] if left else None
    right_time = recognized_words[right[1]]['start'] if right else None
    line_of = lambda index: manual_words[index]['line']

    if left and right:
        span = right_time - left_time
        same_line = line_of(left[0]) == line_of(right[0])
        if same_line or span / len(run) <= 700:
            step = max(0, span) / len(run)
            for k, index in enumerate(run):
                times[index] = int(left_time + step * k)
            return

    # Pausa longa (ou trecho sem âncora de um dos lados): as palavras da mesma linha
    # da âncora ficam coladas a ela, e as linhas inteiras sem âncora são espalhadas.
    with_left = [i for i in run if left and line_of(i) == line_of(left[0])]
    with_right = [i for i in run if right and line_of(i) == line_of(right[0])]
    middle = [i for i in run if i not in with_left and i not in with_right]

    for k, index in enumerate(with_left):
        times[index] = left_time + WORD_STEP_MS * k
    for k, index in enumerate(with_right):
        times[index] = right_time - WORD_STEP_MS * (len(with_right) - k)
    if not middle:
        return

    if left and right:
        low = left_time + WORD_STEP_MS * len(with_left)
        high = right_time - WORD_STEP_MS * (len(with_right) + 1)
        lines = []
        for index in middle:
            if not lines or line_of(lines[-1][-1]) != line_of(index):
                lines.append([])
            lines[-1].append(index)
        slot = max(0, high - low) / len(lines)
        for line_number, line_words in enumerate(lines):
            step = min(WORD_STEP_MS, slot / len(line_words))
            for k, index in enumerate(line_words):
                times[index] = int(low + slot * line_number + step * k)
    elif right:
        anchor = right_time - WORD_STEP_MS * len(with_right)
        for k, index in enumerate(middle):
            times[index] = anchor - WORD_STEP_MS * (len(middle) - k)
    else:
        anchor = left_time + WORD_STEP_MS * len(with_left)
        for k, index in enumerate(middle):
            times[index] = anchor + WORD_STEP_MS * k


def _fill_times(manual_words, anchors, recognized_words):
    total_words = len(manual_words)
    if not anchors:
        # Se nenhuma palavra bateu, ainda distribuímos o texto pelo período
        # reconhecido para permitir revisão manual, mas sinalizamos baixa confiança.
        start = recognized_words[0]['start']
        end = max(start + 500, recognized_words[-1]['end'])
        if total_words == 1:
            return [start]
        step = (end - start) / max(1, total_words - 1)
        return [int(start + index * step) for index in range(total_words)]

    times = [None] * total_words
    for manual_index, recognized_index in anchors.items():
        times[manual_index] = recognized_words[recognized_index]['start']

    anchor_points = [(index, anchors[index]) for index in sorted(anchors)]
    boundaries = [None] + anchor_points + [None]
    for left, right in zip(boundaries, boundaries[1:]):
        first = left[0] + 1 if left else 0
        last = right[0] if right else total_words
        run = list(range(first, last))
        if run:
            _fill_run(run, left, right, manual_words, recognized_words, times)

    # Garante ordem crescente mesmo quando o reconhecimento devolve âncoras muito próximas.
    times[0] = max(0, int(times[0] if times[0] is not None else 0))
    for index in range(1, total_words):
        if times[index] is None:
            times[index] = times[index - 1] + 250
        elif times[index] <= times[index - 1]:
            times[index] = times[index - 1] + 80
        times[index] = int(times[index])
    return times


def align_manual_lyrics_to_lrc(audio_path, lyrics_text, model_size=None, language=None, progress=None):
    if not lyrics_text or not lyrics_text.strip():
        raise RuntimeError('Cole a letra completa antes de iniciar a sincronização.')

    manual_lines, manual_words = _manual_tokens(lyrics_text)
    if not manual_lines or not manual_words:
        raise RuntimeError('A letra informada não possui palavras para sincronizar.')

    recognized_words, detected_language = _recognized_words(
        audio_path,
        model_size=model_size,
        language=language or detect_lyrics_language(lyrics_text),
        hotwords=_hotwords(manual_words),
        progress=progress,
    )
    anchors, matched = _build_anchor_map(manual_words, recognized_words)
    word_times = _fill_times(manual_words, anchors, recognized_words)

    # A tela do karaokê usa só o início de cada linha (como o Spotify), então o LRC
    # é gravado sem tempos por palavra. Os tempos das palavras servem apenas para
    # localizar o início da linha.
    result = []
    for line in manual_lines:
        result.append({
            'time': word_times[line['tokens'][0]['index']],
            'text': line['text'].strip(),
            'words': [],
        })

    meaningful = sum(1 for item in manual_words if item['canonical'])
    confidence = round((matched / max(1, meaningful)) * 100)
    return format_lrc(result), detected_language, confidence


def write_aligned_lyrics(audio_path, lyrics_text, output_path=None, model_size=None, language=None, progress=None):
    audio_path = Path(audio_path)
    if output_path is None:
        output_path = audio_path.with_suffix('.lrc')
    output_path = Path(output_path)

    content, detected_language, confidence = align_manual_lyrics_to_lrc(
        audio_path,
        lyrics_text,
        model_size=model_size,
        language=language,
        progress=progress,
    )
    output_path.write_text(content, encoding='utf-8')
    return output_path, detected_language, confidence
