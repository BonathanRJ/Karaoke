from pathlib import Path

from .lrc import format_lrc


def transcribe_to_lrc(audio_path, model_size="small", language=None):
    try:
        from faster_whisper import WhisperModel
    except ImportError as error:
        raise RuntimeError(
            "A transcrição automática precisa do pacote 'faster-whisper'. "
            "Instale as dependências do projeto novamente com: "
            "python -m pip install -r requirements.txt"
        ) from error

    model = WhisperModel(model_size, device="auto", compute_type="int8")
    segments, info = model.transcribe(
        str(audio_path),
        language=language,
        word_timestamps=True,
        vad_filter=True,
    )

    lines = []
    for segment in segments:
        text = (segment.text or "").strip()
        if not text:
            continue

        words = []
        for word in segment.words or []:
            word_text = word.word or ""
            if not word_text:
                continue
            start = int(max(0.0, word.start or segment.start or 0.0) * 1000)
            words.append({"time": start, "text": word_text})

        start_ms = int(max(0.0, segment.start or 0.0) * 1000)
        if words:
            # O faster-whisper normalmente inclui o espaço no início das palavras.
            # Reorganizamos para o formato visual usado pelo player do projeto.
            normalized = []
            for index, item in enumerate(words):
                token = item["text"]
                if index == 0:
                    token = token.lstrip()
                if index < len(words) - 1:
                    token = token.rstrip() + " "
                else:
                    token = token.strip()
                normalized.append({"time": item["time"], "text": token})
            words = normalized
            visible_text = "".join(word["text"] for word in words).strip()
        else:
            visible_text = text

        lines.append({
            "time": start_ms,
            "text": visible_text,
            "words": words,
        })

    if not lines:
        raise RuntimeError("Nenhuma fala/canto foi reconhecido no áudio.")

    return format_lrc(lines), getattr(info, "language", None)


def write_transcription(audio_path, output_path=None, model_size="small", language=None):
    audio_path = Path(audio_path)
    if output_path is None:
        output_path = audio_path.with_suffix(".lrc")
    output_path = Path(output_path)

    content, detected_language = transcribe_to_lrc(
        audio_path,
        model_size=model_size,
        language=language,
    )
    output_path.write_text(content, encoding="utf-8")
    return output_path, detected_language
