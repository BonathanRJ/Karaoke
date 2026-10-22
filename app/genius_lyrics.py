"""Texto da letra conferido no Genius (genius.com), via pacote LyricsGenius.

O Genius tem transcrições revisadas pela comunidade, mas sem tempos. Por isso ele
é usado para corrigir o texto de letras já sincronizadas (mantendo os tempos de
cada linha) e para preencher a letra que será sincronizada com o vocal.

Só as rotas públicas do site são usadas (as mesmas da busca do genius.com); a API
oficial, que exige uma chave de acesso, não é necessária.
"""
import re
from difflib import SequenceMatcher

from .online_lyrics import LyricsNotFound, clean_title, normalize_text, text_similarity

GENIUS_TIMEOUT = 10
MIN_LINE_SIMILARITY = 0.5    # abaixo disso, duas linhas não são consideradas a mesma
MIN_MATCHED_SHARE = 0.5      # parcela mínima de linhas em comum para aceitar a correção
_TRANSLATION_PAGE = re.compile(r"translation|tradu[cç]|übersetzung|genius (english|brasil)", re.IGNORECASE)
_PAGE_NOISE = re.compile(r"^(you might also like|\d*\s*embed|see .* live)$", re.IGNORECASE)


# O Genius troca algumas letras latinas por sósias cirílicas como marca d'água.
_HOMOGLYPHS = str.maketrans("аеорсухіјѕԁАЕОРСХІЈЅВНКМТ", "aeopcyxijsdAEOPCXIJSBHKMT")
_LATIN = re.compile(r"[A-Za-z]")
_CYRILLIC = re.compile(r"[Ѐ-ԯ]")
_INVISIBLE = re.compile(r"[\u200B-\u200F\u2060\uFEFF]")


def _clean_line(line):
    line = _INVISIBLE.sub("", line).replace("\u00A0", " ")
    # Só corrige palavras que misturam alfabetos, para não estragar letras em russo etc.
    line = " ".join(
        word.translate(_HOMOGLYPHS) if _LATIN.search(word) and _CYRILLIC.search(word) else word
        for word in line.split()
    )
    return re.sub(r"\(\s+", "(", re.sub(r"\s+\)", ")", line)).strip()


def _client():
    from lyricsgenius import Genius

    # O construtor exige um token, mas a busca pública e a página da letra não o
    # usam; sem o cabeçalho de autorização o Genius responde normalmente.
    genius = Genius("public", timeout=GENIUS_TIMEOUT, retries=1)
    genius.authorization_header = {}
    return genius


def _best_hit(response, artist, title):
    for section in response.get("sections", []):
        if section.get("type") not in ("top_hit", "song"):
            continue
        for hit in section.get("hits", []):
            song = hit.get("result") or {}
            if hit.get("type") != "song" or _TRANSLATION_PAGE.search(song.get("full_title") or ""):
                continue
            if text_similarity(song.get("title"), title) < 0.6:
                continue
            artists = song.get("artist_names") or ""
            if artist and text_similarity(artists, artist) < 0.5 and normalize_text(artist) not in normalize_text(artists):
                continue
            return song
    return None


def fetch_genius_lyrics(artist, title):
    """Linhas da letra no Genius, sem cabeçalhos como [Refrão]. Lança LyricsNotFound."""
    title = clean_title(title)
    artist = (artist or "").strip()
    genius = _client()
    song = _best_hit(genius.search_all(f"{title} {artist}".strip()), artist, title)
    if song is None:
        raise LyricsNotFound(f'A letra de "{artist} - {title}" não foi encontrada no Genius.')
    text = genius.lyrics(song_url=song["url"], remove_section_headers=True) or ""
    lines = [_clean_line(line) for line in text.splitlines()]
    lines = [line for line in lines if line and not _PAGE_NOISE.match(line)]
    if not lines:
        raise LyricsNotFound(f'A página do Genius para "{artist} - {title}" não tem a letra.')
    return lines


def _line_similarity(a, b):
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def _align(synced, genius):
    """Alinhamento das linhas na ordem da música (programação dinâmica).

    Só viram par linhas com semelhança >= MIN_LINE_SIMILARITY; o alinhamento
    escolhe os pares que somam mais semelhança sem inverter a ordem.
    Devolve pares (posição sincronizada ou None, posição Genius ou None).
    """
    n, m = len(synced), len(genius)
    score = [[0.0] * (m + 1) for _ in range(n + 1)]
    back = [[0] * (m + 1) for _ in range(n + 1)]   # 0 = par, 1 = só sincronizada, 2 = só Genius
    for i in range(1, n + 1):
        back[i][0] = 1
    for j in range(1, m + 1):
        back[0][j] = 2
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            similarity = _line_similarity(synced[i - 1], genius[j - 1])
            pair = score[i - 1][j - 1] + similarity if similarity >= MIN_LINE_SIMILARITY else -1.0
            only_synced = score[i - 1][j]
            only_genius = score[i][j - 1]
            best = max(pair, only_synced, only_genius)
            score[i][j] = best
            back[i][j] = 0 if best == pair else (1 if best == only_synced else 2)

    pairs = []
    i, j = n, m
    while i > 0 or j > 0:
        move = back[i][j]
        if move == 0:
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif move == 1:
            pairs.append((i - 1, None))
            i -= 1
        else:
            pairs.append((None, j - 1))
            j -= 1
    pairs.reverse()
    return pairs


def _contained(part, whole):
    """`part` aparece em `whole` (ambos normalizados), mesmo com pequenas diferenças."""
    if part in whole:
        return True
    part_words, whole_words = part.split(), whole.split()
    for size in {max(1, len(part_words) - 1), len(part_words), len(part_words) + 1}:
        for start in range(0, max(1, len(whole_words) - size + 1)):
            if _line_similarity(part, " ".join(whole_words[start:start + size])) >= 0.75:
                return True
    return False


def _split_line(text, parts):
    """Divide uma linha do Genius entre várias linhas sincronizadas (`parts`,
    normalizadas) que ela junta, escolhendo os cortes que mais se parecem com elas.

    Devolve um texto por parte, ou None quando não há palavras suficientes.
    """
    if len(parts) == 1:
        return [text]
    words = text.split()
    if len(words) < len(parts):
        return None
    pieces = []
    start = 0
    for index, part in enumerate(parts[:-1]):
        last_cut = len(words) - (len(parts) - index - 1)
        cut = max(
            range(start + 1, last_cut + 1),
            key=lambda end: _line_similarity(part, normalize_text(" ".join(words[start:end]))),
        )
        pieces.append(" ".join(words[start:cut]))
        start = cut
    pieces.append(" ".join(words[start:]))
    # Cada pedaço vira uma linha: sem vírgula pendurada e começando em maiúscula.
    pieces = [piece.rstrip(",") if number < len(pieces) - 1 else piece for number, piece in enumerate(pieces)]
    return [piece[:1].upper() + piece[1:] for piece in pieces]


def correct_with_genius(lines, genius_lines):
    """Troca o texto das linhas sincronizadas pelo do Genius, mantendo os tempos.

    - Linha presente nas duas: fica o tempo da sincronizada e o texto do Genius.
    - Linha do Genius que junta várias sincronizadas: o texto é dividido entre
      elas, para não perder nenhum tempo.
    - Linha só do Genius: entra com tempo estimado entre as vizinhas.
    - Linha só da sincronizada: é mantida (o Genius às vezes não repete o refrão).

    Devolve (linhas, corrigidas, incluídas) ou None se as letras forem de versões
    diferentes demais para uma correção segura.
    """
    sung = [index for index, line in enumerate(lines) if line["text"]]
    synced_norm = [normalize_text(lines[index]["text"]) for index in sung]
    genius_norm = [normalize_text(text) for text in genius_lines]
    pairs = _align(synced_norm, genius_norm)
    owner = {position: genius_index for position, genius_index in pairs
             if position is not None and genius_index is not None}
    if not sung or len(owner) < MIN_MATCHED_SHARE * len(sung):
        return None

    # Linhas sincronizadas sem par cujo texto está na linha do Genius vizinha (o
    # Genius juntou as duas): passam a dividir essa linha. Posições referem-se a `sung`.
    for order in (range(len(sung)), range(len(sung) - 1, -1, -1)):
        for position in order:
            if position in owner:
                continue
            for neighbour in (position - 1, position + 1):
                genius_index = owner.get(neighbour)
                if genius_index is not None and _contained(synced_norm[position], genius_norm[genius_index]):
                    owner[position] = genius_index
                    break

    new_text = {}
    covered = {}
    for position in sorted(owner):
        covered.setdefault(owner[position], []).append(position)
    for genius_index, positions in covered.items():
        pieces = _split_line(genius_lines[genius_index], [synced_norm[p] for p in positions])
        if pieces is None:   # poucas palavras para dividir: a primeira linha fica com tudo
            pieces = [genius_lines[genius_index]] + [None] * (len(positions) - 1)
        new_text.update(zip(positions, pieces))

    inserted_after = {}   # posição (-1 = antes da primeira) -> linhas só do Genius
    last_position = -1
    for position, genius_index in pairs:
        if position is not None:
            last_position = position
        elif genius_index not in covered:
            inserted_after.setdefault(last_position, []).append(genius_lines[genius_index])

    result = []
    corrected = 0
    inserted = 0
    sung_position = {index: position for position, index in enumerate(sung)}

    def add_inserted(texts, start, end):
        nonlocal inserted
        step = (end - start) / (len(texts) + 1)
        for number, text in enumerate(texts, 1):
            result.append({"time": int(start + step * number), "text": text, "words": []})
            inserted += 1

    first_time = lines[sung[0]]["time"]
    if -1 in inserted_after:
        add_inserted(inserted_after[-1], max(0, first_time - 4000), first_time)
    for index, line in enumerate(lines):
        position = sung_position.get(index)
        if position is None:
            result.append(dict(line, words=[]))          # pausa instrumental
            continue
        text = new_text.get(position, line["text"])
        if text is None:
            continue
        if text != line["text"]:
            corrected += 1
        result.append({"time": line["time"], "text": text, "words": []})
        if position in inserted_after:
            following = lines[index + 1]["time"] if index + 1 < len(lines) else line["time"] + 4000
            add_inserted(inserted_after[position], line["time"], following)
    result.sort(key=lambda item: item["time"])
    return result, corrected, inserted


def genius_corrected_lines(lines, artist, title):
    """Aplica a correção do Genius e explica o que aconteceu (para mostrar ao usuário)."""
    try:
        genius_lines = fetch_genius_lyrics(artist, title)
    except LyricsNotFound:
        return lines, "A letra não foi encontrada no Genius; o texto original foi mantido."
    except Exception:
        return lines, "Não foi possível consultar o Genius agora; o texto original foi mantido."

    corrected = correct_with_genius(lines, genius_lines)
    if corrected is None:
        return lines, "A letra do Genius parece ser de outra versão; o texto original foi mantido."
    lines, changed, inserted = corrected
    return lines, correction_summary(changed, inserted)


def correction_summary(changed, inserted):
    if not changed and not inserted:
        return "O texto já coincide com o do Genius."
    details = [f"{changed} linha(s) corrigida(s)"]
    if inserted:
        details.append(f"{inserted} linha(s) incluída(s) com tempo estimado")
    return "Texto conferido com o Genius: " + " e ".join(details) + "."
