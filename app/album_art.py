"""Download automático da capa do álbum (iTunes e Deezer, sem chave de API).

A busca usa o artista e o álbum da pasta music/cover/<Artista> - <Álbum>/: primeiro
encontra o artista e percorre a discografia dele, depois tenta a busca direta.
A capa é salva como cover.jpg dentro da pasta do álbum.
"""
import re

from PySide6.QtCore import QObject, Signal

from .covers import ensure_album_cover_folder, find_cover_path, main_artist, normalize_name

USER_AGENT = "Karaoke/1.0 (capa do album)"
TIMEOUT = 15
COVER_FILE_NAME = "cover.jpg"

# Complementos que as lojas acrescentam ao nome do álbum.
_EDITION_SUFFIX = re.compile(
    r"\s*(?:[\(\[][^\)\]]*(?:edition|edição|deluxe|remaster|version|versão|expanded|bonus|anniversary)[^\)\]]*[\)\]]"
    r"|-\s*(?:single|ep)\s*$)",
    re.I,
)


class CoverNotFound(Exception):
    pass


def _clean_album(title):
    previous = None
    while previous != title:
        previous, title = title, _EDITION_SUFFIX.sub("", title).strip()
    return normalize_name(title)


def _album_match(wanted, found):
    """0 = não serve; 3 = mesmo nome; 2 = mesmo nome sem "(Deluxe Edition)" etc."""
    if normalize_name(found) == normalize_name(wanted):
        return 3
    if _clean_album(found) == _clean_album(wanted):
        return 2
    return 0


def _artist_match(wanted, found):
    wanted_names = {normalize_name(wanted), normalize_name(main_artist(wanted))}
    return normalize_name(found) in wanted_names or normalize_name(main_artist(found)) in wanted_names


def _session():
    import requests

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    return session


def _itunes_candidates(session, artist, album):
    """(artista, álbum, url da capa) do iTunes, em 1000×1000."""
    results = []
    try:
        found = session.get("https://itunes.apple.com/search", timeout=TIMEOUT, params={
            "term": artist, "entity": "musicArtist", "limit": 5,
        }).json().get("results", [])
        for entry in found:
            if _artist_match(artist, entry.get("artistName", "")):
                results += session.get("https://itunes.apple.com/lookup", timeout=TIMEOUT, params={
                    "id": entry["artistId"], "entity": "album", "limit": 200,
                }).json().get("results", [])
                break
        results += session.get("https://itunes.apple.com/search", timeout=TIMEOUT, params={
            "term": f"{artist} {album}", "entity": "album", "limit": 50,
        }).json().get("results", [])
    except Exception:
        pass
    for entry in results:
        url = entry.get("artworkUrl100")
        if entry.get("collectionName") and url:
            yield entry.get("artistName", ""), entry["collectionName"], url.replace("100x100bb", "1000x1000bb")


def _deezer_candidates(session, artist, album):
    """(artista, álbum, url da capa) do Deezer, em 1000×1000."""
    results = []
    try:
        found = session.get("https://api.deezer.com/search/artist", timeout=TIMEOUT,
                            params={"q": artist}).json().get("data", [])
        for entry in found[:5]:
            if _artist_match(artist, entry.get("name", "")):
                albums = session.get(f"https://api.deezer.com/artist/{entry['id']}/albums", timeout=TIMEOUT,
                                     params={"limit": 300}).json().get("data", [])
                results += [dict(item, artist={"name": entry["name"]}) for item in albums]
                break
        results += session.get("https://api.deezer.com/search/album", timeout=TIMEOUT,
                               params={"q": f"{artist} {album}"}).json().get("data", [])
    except Exception:
        pass
    for entry in results:
        url = entry.get("cover_xl")
        if entry.get("title") and url:
            yield (entry.get("artist") or {}).get("name", ""), entry["title"], url


def find_cover_url(artist, album):
    """Melhor capa encontrada para o álbum; levanta CoverNotFound se não houver."""
    session = _session()
    best = None
    for source in (_itunes_candidates, _deezer_candidates):
        for found_artist, found_album, url in source(session, artist, album):
            if not _artist_match(artist, found_artist):
                continue
            score = _album_match(album, found_album)
            if score and (best is None or score > best[0]):
                best = (score, url)
                if score == 3:
                    return url
    if best is None:
        raise CoverNotFound(f'Nenhuma capa encontrada para "{artist} - {album}" no iTunes ou no Deezer.')
    return best[1]


def download_album_cover(artist, album):
    """Baixa a capa para music/cover/<Artista> - <Álbum>/cover.jpg (se a pasta não tiver imagem)."""
    folder = ensure_album_cover_folder(artist, album)
    if folder is None:
        raise CoverNotFound("A música não tem álbum informado.")
    existing = find_cover_path({"artist": artist, "album": album})
    if existing:
        return existing
    url = find_cover_url(artist, album)
    response = _session().get(url, timeout=TIMEOUT)
    response.raise_for_status()
    if not response.headers.get("Content-Type", "").startswith("image/"):
        raise CoverNotFound("O servidor não devolveu uma imagem.")
    path = folder / COVER_FILE_NAME
    path.write_bytes(response.content)
    return path


class AlbumCoverWorker(QObject):
    finished = Signal(str)   # caminho da capa
    failed = Signal(str)

    def __init__(self, artist, album):
        super().__init__()
        self.artist = artist
        self.album = album

    def run(self):
        try:
            self.finished.emit(str(download_album_cover(self.artist, self.album)))
        except Exception as error:
            self.failed.emit(str(error))
