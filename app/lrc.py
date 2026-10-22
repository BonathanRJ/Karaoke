import re

TIME_RE = re.compile(r"^\[(\d+):(\d{2})(?:\.(\d{1,3}))?\](.*)$")
WORD_TIME_RE = re.compile(r"<(\d+):(\d{2})(?:\.(\d{1,3}))?>")


def _fraction_to_ms(value):
    if not value:
        return 0
    return int(value.ljust(3, "0")[:3])


def _parts_to_ms(minutes, seconds, fraction):
    return int(minutes) * 60000 + int(seconds) * 1000 + _fraction_to_ms(fraction)


def format_word_timestamp(milliseconds):
    milliseconds = max(0, int(milliseconds))
    total_seconds, ms = divmod(milliseconds, 1000)
    minutes, seconds = divmod(total_seconds, 60)
    return f"<{minutes:02d}:{seconds:02d}.{ms // 10:02d}>"


def parse_word_timed_text(text):
    """Retorna palavras/trechos de um LRC estendido.

    Exemplo:
        <00:10.00>Eu <00:10.50>sei

    Resultado:
        [{"time": 10000, "text": "Eu "}, {"time": 10500, "text": "sei"}]
    """
    matches = list(WORD_TIME_RE.finditer(text))
    if not matches:
        return []

    result = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        result.append({
            "time": _parts_to_ms(match.group(1), match.group(2), match.group(3)),
            "text": text[start:end],
        })
    return result


def strip_word_timestamps(text):
    return WORD_TIME_RE.sub("", text)


def parse_lrc(text):
    result = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        match = TIME_RE.match(line)
        if not match:
            continue

        timestamp = _parts_to_ms(match.group(1), match.group(2), match.group(3))
        content = match.group(4)
        words = parse_word_timed_text(content)

        result.append({
            "time": timestamp,
            "text": strip_word_timestamps(content),
            "words": words,
        })

    result.sort(key=lambda item: item["time"])
    return result


def format_timestamp(milliseconds):
    milliseconds = max(0, int(milliseconds))
    total_seconds, ms = divmod(milliseconds, 1000)
    minutes, seconds = divmod(total_seconds, 60)
    return f"[{minutes:02d}:{seconds:02d}.{ms // 10:02d}]"


def format_lrc(lines):
    output = []
    for item in lines:
        timestamp = item.get("time")
        words = item.get("words") or []
        text = item.get("text", "")

        if words:
            content = "".join(format_word_timestamp(word["time"]) + word["text"] for word in words)
        else:
            content = text

        if timestamp is None:
            output.append(content)
        else:
            output.append(format_timestamp(timestamp) + content)

    return "\n".join(output)


def parse_editor_lines(text):
    result = []
    for raw_line in text.splitlines():
        match = TIME_RE.match(raw_line.strip())
        if match:
            timestamp = _parts_to_ms(match.group(1), match.group(2), match.group(3))
            content = match.group(4)
            words = parse_word_timed_text(content)
            result.append({
                "time": timestamp,
                "text": strip_word_timestamps(content),
                "words": words,
            })
        else:
            words = parse_word_timed_text(raw_line)
            result.append({
                "time": words[0]["time"] if words else None,
                "text": strip_word_timestamps(raw_line),
                "words": words,
            })
    return result
