import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

from .lrc import format_lrc


def _load_model(model_size):
    try:
        from faster_whisper import WhisperModel
    except ImportError as error:
        raise RuntimeError(
            "A sincronização automática precisa do pacote 'faster-whisper'. "
            "Instale as dependências do projeto novamente com: "
            "python -m pip install -r requirements.txt"
        ) from error
    return WhisperModel(model_size, device='auto', compute_type='int8')


def _normalize_word(value):
    value = unicodedata.normalize('NFKD', value or '')
    value = ''.join(char for char in value if not unicodedata.combining(char))
    value = value.lower()
    return ''.join(char for char in value if char.isalnum())


def _manual_tokens(lyrics_text):
    lines = []
    flat = []
    global_index = 0
    for raw_line in lyrics_text.splitlines():
        # Linhas vazias são separadores visuais e não precisam de timestamp no LRC.
        if not raw_line.strip():
            continue
        tokens = re.findall(r'\S+\s*', raw_line)
        line_tokens = []
        for token in tokens:
            normalized = _normalize_word(token)
            item = {
                'index': global_index,
                'text': token,
                'normalized': normalized,
            }
            line_tokens.append(item)
            flat.append(item)
            global_index += 1
        if line_tokens:
            lines.append({'text': raw_line, 'tokens': line_tokens})
    return lines, flat


def _recognized_words(audio_path, model_size='small', language=None):
    model = _load_model(model_size)
    segments, info = model.transcribe(
        str(audio_path),
        language=language,
        word_timestamps=True,
        vad_filter=False,
        beam_size=5,
        condition_on_previous_text=False,
    )

    words = []
    for segment in segments:
        for word in segment.words or []:
            text = word.word or ''
            normalized = _normalize_word(text)
            if not normalized:
                continue
            start = float(word.start if word.start is not None else segment.start or 0.0)
            end = float(word.end if word.end is not None else start + 0.25)
            words.append({
                'normalized': normalized,
                'start': max(0, int(start * 1000)),
                'end': max(0, int(end * 1000)),
            })

    if not words:
        raise RuntimeError(
            'Nenhuma voz utilizável foi reconhecida no stem vocal. '
            'Confirme se a separação gerou um arquivo de voz audível.'
        )
    return words, getattr(info, 'language', None)


def _build_anchor_map(manual_words, recognized_words):
    manual_norm = [item['normalized'] for item in manual_words]
    recognized_norm = [item['normalized'] for item in recognized_words]

    matcher = SequenceMatcher(None, manual_norm, recognized_norm, autojunk=False)
    anchors = {}
    matched = 0
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            manual_index = block.a + offset
            recognized_index = block.b + offset
            anchors[manual_index] = recognized_words[recognized_index]['start']
            matched += 1
    return anchors, matched


def _fill_times(total_words, anchors, recognized_words):
    if not anchors:
        # Se nenhuma palavra bateu exatamente, ainda distribuímos o texto pelo período
        # reconhecido para permitir revisão manual, mas sinalizamos baixa confiança.
        start = recognized_words[0]['start']
        end = max(start + 500, recognized_words[-1]['end'])
        if total_words == 1:
            return [start]
        step = (end - start) / max(1, total_words - 1)
        return [int(start + index * step) for index in range(total_words)]

    times = [None] * total_words
    for index, value in anchors.items():
        if 0 <= index < total_words:
            times[index] = int(value)

    anchor_indices = sorted(anchors)
    first = anchor_indices[0]
    first_time = times[first]
    for index in range(first - 1, -1, -1):
        times[index] = max(0, first_time - (first - index) * 350)

    for left, right in zip(anchor_indices, anchor_indices[1:]):
        left_time = times[left]
        right_time = times[right]
        gap = right - left
        if gap <= 1:
            continue
        step = max(80.0, (right_time - left_time) / gap)
        for offset in range(1, gap):
            times[left + offset] = int(left_time + step * offset)

    last = anchor_indices[-1]
    for index in range(last + 1, total_words):
        times[index] = times[index - 1] + 350

    # Garante ordem crescente mesmo quando o reconhecimento devolve anchors muito próximos.
    for index in range(1, total_words):
        if times[index] is None:
            times[index] = times[index - 1] + 250
        elif times[index] <= times[index - 1]:
            times[index] = times[index - 1] + 80
    return times


def align_manual_lyrics_to_lrc(audio_path, lyrics_text, model_size='small', language=None):
    if not lyrics_text or not lyrics_text.strip():
        raise RuntimeError('Cole a letra completa antes de iniciar a sincronização.')

    manual_lines, manual_words = _manual_tokens(lyrics_text)
    if not manual_lines or not manual_words:
        raise RuntimeError('A letra informada não possui palavras para sincronizar.')

    recognized_words, detected_language = _recognized_words(
        audio_path,
        model_size=model_size,
        language=language,
    )
    anchors, matched = _build_anchor_map(manual_words, recognized_words)
    word_times = _fill_times(len(manual_words), anchors, recognized_words)

    result = []
    for line in manual_lines:
        words = []
        for token in line['tokens']:
            words.append({
                'time': word_times[token['index']],
                'text': token['text'],
            })
        result.append({
            'time': words[0]['time'],
            'text': line['text'],
            'words': words,
        })

    confidence = round((matched / max(1, len(manual_words))) * 100)
    return format_lrc(result), detected_language, confidence


def write_aligned_lyrics(audio_path, lyrics_text, output_path=None, model_size='small', language=None):
    audio_path = Path(audio_path)
    if output_path is None:
        output_path = audio_path.with_suffix('.lrc')
    output_path = Path(output_path)

    content, detected_language, confidence = align_manual_lyrics_to_lrc(
        audio_path,
        lyrics_text,
        model_size=model_size,
        language=language,
    )
    output_path.write_text(content, encoding='utf-8')
    return output_path, detected_language, confidence


# Mantido para compatibilidade com versões anteriores/editor. O texto gerado por
# esta função NÃO é usado no novo fluxo principal da v0.9.
def transcribe_to_lrc(audio_path, model_size='small', language=None):
    model = _load_model(model_size)
    segments, info = model.transcribe(
        str(audio_path),
        language=language,
        word_timestamps=True,
        vad_filter=False,
        beam_size=5,
        condition_on_previous_text=False,
    )

    lines = []
    for segment in segments:
        text = (segment.text or '').strip()
        if not text:
            continue
        start_ms = int(max(0.0, segment.start or 0.0) * 1000)
        lines.append({'time': start_ms, 'text': text, 'words': []})

    if not lines:
        raise RuntimeError('Nenhuma voz foi reconhecida no áudio.')
    return format_lrc(lines), getattr(info, 'language', None)


def write_transcription(audio_path, output_path=None, model_size='small', language=None):
    audio_path = Path(audio_path)
    if output_path is None:
        output_path = audio_path.with_suffix('.lrc')
    output_path = Path(output_path)
    content, detected_language = transcribe_to_lrc(audio_path, model_size=model_size, language=language)
    output_path.write_text(content, encoding='utf-8')
    return output_path, detected_language
