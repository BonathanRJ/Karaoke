import re
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / 'data'
MUSIC_DIR = PROJECT_ROOT / 'music'
DB_PATH = DATA_DIR / 'karaoke.db'
ORIGINAL_DIR = MUSIC_DIR / 'original'
INSTRUMENTAL_DIR = MUSIC_DIR / 'instrumental'
VOCAL_DIR = MUSIC_DIR / 'vocal'
LYRIC_DIR = MUSIC_DIR / 'lyric'
for folder in (ORIGINAL_DIR, INSTRUMENTAL_DIR, VOCAL_DIR, LYRIC_DIR):
    folder.mkdir(parents=True, exist_ok=True)

DATA_DIR.mkdir(parents=True, exist_ok=True)
MUSIC_DIR.mkdir(parents=True, exist_ok=True)


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_column(conn, table, column, definition):
    columns = {row['name'] for row in conn.execute(f'PRAGMA table_info({table})')}
    if column not in columns:
        conn.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')


def _allocate_song_codes(conn):
    """Allocate permanent 4-digit codes without changing previously assigned codes."""
    used = {r['code'] for r in conn.execute('SELECT code FROM songs WHERE code IS NOT NULL')}
    next_number = 1
    for row in conn.execute('SELECT id FROM songs WHERE code IS NULL ORDER BY id').fetchall():
        while next_number <= 9999 and f'{next_number:04d}' in used:
            next_number += 1
        if next_number > 9999:
            raise ValueError('Limite de 9999 músicas atingido: não há códigos disponíveis.')
        code = f'{next_number:04d}'
        conn.execute('UPDATE songs SET code=? WHERE id=?', (code, row['id']))
        used.add(code)
        next_number += 1


def init_db():
    with connect() as conn:
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS songs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                artist TEXT NOT NULL DEFAULT '',
                audio_path TEXT NOT NULL UNIQUE,
                lyrics_path TEXT,
                cover_path TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            '''
        )
        _ensure_column(conn, 'songs', 'code', 'TEXT')
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_songs_code ON songs(code)')
        _allocate_song_codes(conn)
        _ensure_column(conn, 'songs', 'source_audio_path', 'TEXT')
        _ensure_column(conn, 'songs', 'vocals_path', 'TEXT')
        _ensure_column(conn, 'songs', 'source_url', 'TEXT')
        _ensure_column(conn, 'songs', 'prepared_automatically', 'INTEGER NOT NULL DEFAULT 0')
        _ensure_column(conn, 'songs', 'album', "TEXT NOT NULL DEFAULT ''")
        conn.commit()

    synchronize_library()


def update_song_metadata(song_id, title, artist, album=''):
    with connect() as conn:
        conn.execute(
            'UPDATE songs SET title = ?, artist = ?, album = ? WHERE id = ?',
            (title.strip(), artist.strip(), (album or '').strip(), song_id),
        )
        conn.commit()


def add_song(
    title,
    artist,
    audio_path,
    lyrics_path='',
    source_audio_path='',
    vocals_path='',
    source_url='',
    prepared_automatically=False,
    album='',
):
    with connect() as conn:
        cur = conn.execute(
            '''
            INSERT INTO songs
                (title, artist, audio_path, lyrics_path, source_audio_path, vocals_path, source_url,
                 prepared_automatically, album)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                title,
                artist,
                str(audio_path),
                str(lyrics_path) if lyrics_path else None,
                str(source_audio_path) if source_audio_path else None,
                str(vocals_path) if vocals_path else None,
                str(source_url) if source_url else None,
                1 if prepared_automatically else 0,
                (album or '').strip(),
            ),
        )
        _allocate_song_codes(conn)
        conn.commit()
        return cur.lastrowid


def update_lyrics_path(song_id, lyrics_path):
    with connect() as conn:
        conn.execute(
            'UPDATE songs SET lyrics_path = ? WHERE id = ?',
            (str(lyrics_path), song_id),
        )
        conn.commit()



def update_song_stems(song_id, instrumental_path, vocals_path, source_audio_path=None):
    with connect() as conn:
        conn.execute(
            """
            UPDATE songs
            SET audio_path = ?, vocals_path = ?,
                source_audio_path = COALESCE(?, source_audio_path, audio_path),
                prepared_automatically = 0
            WHERE id = ?
            """,
            (
                str(instrumental_path),
                str(vocals_path) if vocals_path else None,
                str(source_audio_path) if source_audio_path else None,
                song_id,
            ),
        )
        conn.commit()


def mark_song_synced(song_id, lyrics_path):
    with connect() as conn:
        conn.execute(
            'UPDATE songs SET lyrics_path = ?, prepared_automatically = 1 WHERE id = ?',
            (str(lyrics_path), song_id),
        )
        conn.commit()


AUDIO_EXTENSIONS = {'.mp3', '.wav', '.ogg', '.flac', '.m4a', '.aac'}


def _identity(path):
    """Associates stems without changing on-disk filenames."""
    name = Path(str(path).replace('\\', '/')).stem
    name = re.sub(r'\s*\[(?:instrumental|vocals|vocal)\]\s*$', '', name, flags=re.I)
    return name.strip().casefold()


def _companion(folder, identity, suffixes):
    for path in sorted(folder.iterdir(), key=lambda p: p.name.casefold()):
        if path.is_file() and path.suffix.lower() in suffixes and _identity(path) == identity:
            return path.resolve()
    return None


def synchronize_library():
    """Discover instrumental+LRC pairs on each start; preserve edited metadata."""
    instrumentals = sorted(
        (p.resolve() for p in INSTRUMENTAL_DIR.iterdir()
         if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS),
        key=lambda p: p.name.casefold(),
    )
    with connect() as conn:
        existing = conn.execute('SELECT * FROM songs').fetchall()
        by_name = {}
        for row in existing:
            if row['audio_path']:
                key = _identity(row['audio_path'])
                previous = by_name.get(key)
                if previous is None or ('instrumental' in str(row['audio_path']).lower() and
                                        'instrumental' not in str(previous['audio_path']).lower()):
                    by_name[key] = row
        # Consolidate legacy duplicate registrations without touching media files.
        for row in existing:
            if row['audio_path'] and by_name.get(_identity(row['audio_path']))['id'] != row['id']:
                conn.execute('DELETE FROM songs WHERE id=?', (row['id'],))
        for audio in instrumentals:
            identity = _identity(audio)
            lyric = _companion(LYRIC_DIR, identity, {'.lrc'})
            if not lyric:
                continue
            original = _companion(ORIGINAL_DIR, identity, AUDIO_EXTENSIONS)
            vocal = _companion(VOCAL_DIR, identity, AUDIO_EXTENSIONS)
            row = by_name.get(identity)
            if row:
                conn.execute("""UPDATE songs SET audio_path=?, lyrics_path=?,
                    source_audio_path=COALESCE(?, source_audio_path),
                    vocals_path=COALESCE(?, vocals_path) WHERE id=?""",
                    (str(audio), str(lyric), str(original) if original else None,
                     str(vocal) if vocal else None, row['id']))
            else:
                # Preserve readable filename as initial display title. Renames persist in DB.
                title = Path(audio).stem
                if title.casefold().endswith(' [instrumental]'):
                    title = title[:-len(' [Instrumental]')]
                conn.execute("""INSERT OR IGNORE INTO songs
                    (title,artist,audio_path,lyrics_path,source_audio_path,vocals_path)
                    VALUES (?,?,?,?,?,?)""",
                    (title, '', str(audio), str(lyric),
                     str(original) if original else None, str(vocal) if vocal else None))
        _allocate_song_codes(conn)
        conn.commit()


def list_songs():
    """Somente músicas com instrumental e letra nas pastas próprias aparecem."""
    with connect() as conn:
        songs = conn.execute('SELECT * FROM songs ORDER BY artist COLLATE NOCASE, title COLLATE NOCASE, id').fetchall()
    instrumental_root = INSTRUMENTAL_DIR.resolve()
    lyric_root = LYRIC_DIR.resolve()
    result = []
    for song in songs:
        audio = Path(song['audio_path']).resolve() if song['audio_path'] else None
        lyric = Path(song['lyrics_path']).resolve() if song['lyrics_path'] else None
        if not audio or not lyric or not audio.is_file() or not lyric.is_file():
            continue
        if audio.parent != instrumental_root or lyric.parent != lyric_root:
            continue
        result.append(song)
    return result


def get_song(song_id):
    with connect() as conn:
        row = conn.execute('SELECT * FROM songs WHERE id = ?', (song_id,)).fetchone()
    return dict(row) if row else None


def get_song_by_audio_path(audio_path):
    target = str(Path(audio_path))
    with connect() as conn:
        return conn.execute(
            'SELECT * FROM songs WHERE audio_path = ? OR source_audio_path = ?',
            (target, target),
        ).fetchone()
