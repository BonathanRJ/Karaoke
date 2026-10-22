"""Capas dos álbuns e as cores extraídas delas.

Cada álbum tem uma pasta music/cover/<Artista> - <Álbum>/, criada ao cadastrar o
álbum; a imagem colocada nela é a capa de todas as faixas do álbum e define as
cores da tela do karaokê.
"""
import re
import unicodedata
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap

from .database import MUSIC_DIR

COVER_DIR = MUSIC_DIR / "cover"
COVER_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
ALBUM_COLOR_FILE = "cor.txt"   # cor de fundo fixa do álbum, no lugar da calculada pela capa


# --- Nomes -------------------------------------------------------------------
def normalize_name(text):
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "", text.casefold())


def main_artist(artist):
    return re.split(
        r"\s+(?:feat\.?|ft\.?|featuring|part\.?|with|&|x)\s+|,|/",
        artist or "",
        maxsplit=1,
        flags=re.I,
    )[0].strip()


# --- Pastas de capa -----------------------------------------------------------
def album_folder_name(artist, album):
    """Nome da pasta do álbum, sem caracteres proibidos no Windows."""
    artist, album = (artist or "").strip(), (album or "").strip()
    name = f"{artist} - {album}" if artist else album
    name = re.sub(r'[<>:"/\\|?*]+', " ", name)
    return re.sub(r"\s{2,}", " ", name).strip().rstrip(". ")


def cover_index():
    """Pastas de álbum de music/cover, pelo nome normalizado."""
    folders = {}
    if COVER_DIR.is_dir():
        for path in sorted(COVER_DIR.iterdir(), key=lambda p: p.name.casefold()):
            if path.is_dir():
                folders.setdefault(normalize_name(path.name), path)
    return folders


def ensure_album_cover_folder(artist, album, index=None):
    """Cria music/cover/<Artista> - <Álbum>/ se ainda não existir uma equivalente.

    `index` (de cover_index) evita reler a pasta de capas e é atualizado.
    """
    if not (album or "").strip():
        return None
    index = cover_index() if index is None else index
    name = album_folder_name(artist, album)
    key = normalize_name(name)
    if key not in index:
        folder = COVER_DIR / name
        folder.mkdir(parents=True, exist_ok=True)
        index[key] = folder
    return index[key]


def _first_image(folder):
    images = sorted(
        (path for path in folder.iterdir()
         if path.is_file() and path.suffix.lower() in COVER_EXTENSIONS),
        key=lambda path: path.name.casefold(),
    )
    return images[0] if images else None


def find_cover_path(song, index=None):
    """Capa da música: a imagem dentro da pasta music/cover/<Artista> - <Álbum>/.

    Acentos, maiúsculas e pontuação são ignorados. "Artista feat. Outro" também
    procura a pasta do artista principal.
    """
    album = song.get("album") or ""
    if not album:
        return None
    index = cover_index() if index is None else index
    artist = song.get("artist") or ""
    for name in dict.fromkeys((artist, main_artist(artist))):
        folder = index.get(normalize_name(album_folder_name(name, album)))
        image = _first_image(folder) if folder else None
        if image:
            return image
    return None


_thumbnail_cache = {}


def file_mtime(path):
    try:
        return Path(path).stat().st_mtime
    except OSError:
        return None


def cover_thumbnail(path, size, mtime=None):
    """Capa recortada em quadrado, guardada em cache para a visão de ícones.

    Passar `mtime` (lido uma vez ao carregar a biblioteca) evita consultar o disco
    a cada vez que o ícone é redesenhado.
    """
    mtime = file_mtime(path) if mtime is None else mtime
    if mtime is None:
        return None
    key = (str(path), mtime, size)
    pixmap = _thumbnail_cache.get(key)
    if pixmap is None:
        image = QPixmap(str(path))
        if image.isNull():
            return None
        image = image.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                             Qt.TransformationMode.SmoothTransformation)
        pixmap = image.copy((image.width() - size) // 2, (image.height() - size) // 2, size, size)
        _thumbnail_cache[key] = pixmap
    return pixmap


# --- Cor de fundo a partir da capa, como no Spotify ---------------------------
# O Spotify extrai uma paleta da capa (no estilo da Palette do Android), escolhe a
# cor "vibrante" e a escurece para servir de fundo, com a letra em tons da mesma cor.
COVER_BACKGROUND_LIGHTNESS = 0.22   # luminosidade do fundo (0-1)
COVER_FALLBACK_BACKGROUND = "#000000"

# Alvos da Palette: (nome, luminosidade mín/ideal/máx, saturação mín/ideal/máx).
_PALETTE_TARGETS = (
    ("vibrant", 0.30, 0.50, 0.70, 0.35, 1.00, 1.00),
    ("dark_vibrant", 0.00, 0.26, 0.45, 0.35, 1.00, 1.00),
    ("light_vibrant", 0.55, 0.74, 1.00, 0.35, 1.00, 1.00),
    ("muted", 0.30, 0.50, 0.70, 0.00, 0.30, 0.40),
    ("dark_muted", 0.00, 0.26, 0.45, 0.00, 0.30, 0.40),
)


COVER_PALETTE_SIZE = 16
# Tons amarelados/alaranjados pouco saturados (bege, marrom, pele, oliva) ficam
# com fundo "barrento" quando escurecidos; laranja e amarelo vivos continuam valendo.
COVER_BEIGE_HUES = (15, 70)          # graus
COVER_BEIGE_MAX_SATURATION = 0.65


def _box_volume(box):
    volume = 1
    for axis in range(3):
        volume *= max(c[axis] for c in box) - min(c[axis] for c in box) + 1
    return volume


def _cover_swatches(image):
    """Reduz a capa a até 16 cores por median cut (como a Palette) e conta a população."""
    counts = {}
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if color.alpha() < 128:
                continue
            key = (color.red() >> 3, color.green() >> 3, color.blue() >> 3)
            counts[key] = counts.get(key, 0) + 1

    # Divide sempre a caixa de maior volume no eixo mais longo, pela mediana da população.
    boxes = [[(*key, count) for key, count in counts.items()]]
    while len(boxes) < COVER_PALETTE_SIZE:
        splittable = [box for box in boxes if len(box) > 1]
        if not splittable:
            break
        box = max(splittable, key=_box_volume)
        boxes.remove(box)
        ranges = [max(c[axis] for c in box) - min(c[axis] for c in box) for axis in range(3)]
        axis = ranges.index(max(ranges))
        box.sort(key=lambda c: c[axis])
        half = sum(c[3] for c in box) / 2
        accumulated = 0
        split = 1
        for index, c in enumerate(box):
            accumulated += c[3]
            if accumulated >= half:
                split = index + 1
                break
        split = min(max(1, split), len(box) - 1)
        boxes += [box[:split], box[split:]]

    swatches = []
    for box in boxes:
        population = sum(c[3] for c in box)
        if not population:
            continue
        color = QColor(*(
            min(255, round(sum(c[axis] * c[3] for c in box) / population * 255 / 31))
            for axis in range(3)
        ))
        lightness = color.lightnessF()
        # Como a Palette, ignora quase-preto e quase-branco.
        if lightness < 0.05 or lightness > 0.95:
            continue
        swatches.append((color, population))
    return swatches


def cover_palette_color(pixmap):
    """Cor de destaque da capa, escolhida como a Palette faz (vibrante primeiro)."""
    image = pixmap.toImage()
    if image.isNull():
        return None
    image = image.scaled(96, 96, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    swatches = _cover_swatches(image)
    if not swatches:
        return None
    max_population = max(population for _, population in swatches)
    # Uma cor presente em pouquíssimos pixels (um detalhe) não representa a capa.
    min_population = sum(population for _, population in swatches) * 0.01

    # Bege, marrom, pele e oliva só entram se a capa não tiver outra opção.
    for allow_beige in (False, True):
        best = _best_palette_swatch(swatches, max_population, min_population, allow_beige)
        if best is not None:
            return best
    return max(swatches, key=lambda item: item[1])[0]


def _is_beige(color):
    hue = color.hslHueF() * 360
    return COVER_BEIGE_HUES[0] <= hue <= COVER_BEIGE_HUES[1] and color.hslSaturationF() < COVER_BEIGE_MAX_SATURATION


def _best_palette_swatch(swatches, max_population, min_population, allow_beige):
    for _, l_min, l_ideal, l_max, s_min, s_ideal, s_max in _PALETTE_TARGETS:
        best, best_score = None, -1.0
        for color, population in swatches:
            if population < min_population:
                continue
            if not allow_beige and _is_beige(color):
                continue
            saturation = max(0.0, color.hslSaturationF())
            lightness = color.lightnessF()
            if not (l_min <= lightness <= l_max and s_min <= saturation <= s_max):
                continue
            # Pesos da Palette: luminosidade 0,52; saturação e população 0,24 cada.
            score = (
                0.24 * (1 - abs(saturation - s_ideal))
                + 0.52 * (1 - abs(lightness - l_ideal))
                + 0.24 * (population / max_population)
            )
            if score > best_score:
                best, best_score = color, score
        if best is not None:
            return best
    return None


def cover_background_color(pixmap):
    """Fundo escuro na matiz da cor de destaque da capa, mantendo contraste com o branco."""
    accent = cover_palette_color(pixmap)
    if accent is None:
        return COVER_FALLBACK_BACKGROUND
    hue = max(0.0, accent.hslHueF())
    saturation = max(0.0, accent.hslSaturationF())
    return QColor.fromHslF(hue, saturation, COVER_BACKGROUND_LIGHTNESS).name()


_background_cache = {}


def album_color_override(folder):
    """Cor escolhida à mão para o álbum: arquivo cor.txt na pasta do álbum (ex.: #5F0048)."""
    try:
        text = (Path(folder) / ALBUM_COLOR_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    color = QColor(text.split()[0] if text else "")
    return color.name() if color.isValid() else None


def cover_background_for(path):
    """(capa, cor de fundo) da imagem, com a cor guardada em cache por arquivo.

    Um cor.txt na pasta do álbum tem prioridade sobre a cor calculada da capa.
    """
    image = QPixmap(str(path))
    if image.isNull():
        return None, COVER_FALLBACK_BACKGROUND
    override = album_color_override(Path(path).parent)
    if override:
        return image, override
    key = (str(path), file_mtime(path))
    color = _background_cache.get(key)
    if color is None:
        color = cover_background_color(image)
        _background_cache[key] = color
    return image, color
