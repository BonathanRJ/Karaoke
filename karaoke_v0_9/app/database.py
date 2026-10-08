from pathlib import Path
import sqlite3

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / 'data'
MUSIC_DIR = PROJECT_ROOT / 'music'
DB_PATH = DATA_DIR / 'karaoke.db'

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
        _ensure_column(conn, 'songs', 'source_audio_path', 'TEXT')
        _ensure_column(conn, 'songs', 'vocals_path', 'TEXT')
        _ensure_column(conn, 'songs', 'source_url', 'TEXT')
        _ensure_column(conn, 'songs', 'prepared_automatically', 'INTEGER NOT NULL DEFAULT 0')
        conn.commit()

    repair_shared_paths()


def _file_name(path):
    return str(path).replace('\\', '/').rsplit('/', 1)[-1]


def repair_shared_paths():
    with connect() as conn:
        songs = conn.execute(
            'SELECT id, audio_path, lyrics_path, source_audio_path, vocals_path FROM songs'
        ).fetchall()

        for song in songs:
            updates = {}
            for field in ('audio_path', 'lyrics_path', 'source_audio_path', 'vocals_path'):
                value = song[field]
                if not value:
                    continue
                current = Path(value)
                if current.exists():
                    continue
                shared = MUSIC_DIR / _file_name(value)
                if shared.exists():
                    updates[field] = str(shared)

            if updates:
                fields = ', '.join(f'{field} = ?' for field in updates)
                values = list(updates.values()) + [song['id']]
                conn.execute(f'UPDATE songs SET {fields} WHERE id = ?', values)

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
):
    with connect() as conn:
        cur = conn.execute(
            '''
            INSERT INTO songs
                (title, artist, audio_path, lyrics_path, source_audio_path, vocals_path, source_url, prepared_automatically)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
        )
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

def update_song_preparation(song_id, instrumental_path, vocals_path, lyrics_path, source_audio_path=None):
    with connect() as conn:
        conn.execute(
            '''
            UPDATE songs
            SET audio_path = ?, vocals_path = ?, lyrics_path = ?,
                source_audio_path = COALESCE(?, source_audio_path, audio_path),
                prepared_automatically = 1
            WHERE id = ?
            ''',
            (
                str(instrumental_path),
                str(vocals_path) if vocals_path else None,
                str(lyrics_path) if lyrics_path else None,
                str(source_audio_path) if source_audio_path else None,
                song_id,
            ),
        )
        conn.commit()


def list_songs(search=''):
    with connect() as conn:
        if search.strip():
            q = f'%{search.strip()}%'
            return conn.execute(
                '''
                SELECT * FROM songs
                WHERE title LIKE ? OR artist LIKE ?
                ORDER BY title COLLATE NOCASE
                ''',
                (q, q),
            ).fetchall()

        return conn.execute(
            'SELECT * FROM songs ORDER BY title COLLATE NOCASE'
        ).fetchall()


def get_song_by_audio_path(audio_path):
    target = str(Path(audio_path))
    with connect() as conn:
        return conn.execute(
            'SELECT * FROM songs WHERE audio_path = ? OR source_audio_path = ?',
            (target, target),
        ).fetchone()


def delete_song(song_id):
    with connect() as conn:
        conn.execute('DELETE FROM songs WHERE id = ?', (song_id,))
        conn.commit()
