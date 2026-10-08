from pathlib import Path
import sqlite3

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
MUSIC_DIR = PROJECT_ROOT / "music"
DB_PATH = DATA_DIR / "karaoke.db"

DATA_DIR.mkdir(parents=True, exist_ok=True)
MUSIC_DIR.mkdir(parents=True, exist_ok=True)


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS songs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                artist TEXT NOT NULL DEFAULT '',
                audio_path TEXT NOT NULL UNIQUE,
                lyrics_path TEXT,
                cover_path TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()

    repair_shared_paths()


def _file_name(path):
    return str(path).replace("\\", "/").rsplit("/", 1)[-1]


def repair_shared_paths():
    with connect() as conn:
        songs = conn.execute(
            "SELECT id, audio_path, lyrics_path FROM songs"
        ).fetchall()

        for song in songs:
            updates = {}

            audio_path = song["audio_path"]
            if audio_path:
                current_audio = Path(audio_path)
                if not current_audio.exists():
                    shared_audio = MUSIC_DIR / _file_name(audio_path)
                    if shared_audio.exists():
                        updates["audio_path"] = str(shared_audio)

            lyrics_path = song["lyrics_path"]
            if lyrics_path:
                current_lyrics = Path(lyrics_path)
                if not current_lyrics.exists():
                    shared_lyrics = MUSIC_DIR / _file_name(lyrics_path)
                    if shared_lyrics.exists():
                        updates["lyrics_path"] = str(shared_lyrics)

            if updates:
                fields = ", ".join(f"{field} = ?" for field in updates)
                values = list(updates.values()) + [song["id"]]
                conn.execute(
                    f"UPDATE songs SET {fields} WHERE id = ?",
                    values,
                )

        conn.commit()


def add_song(title, artist, audio_path, lyrics_path=""):
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO songs
                (title, artist, audio_path, lyrics_path)
            VALUES (?, ?, ?, ?)
            """,
            (
                title,
                artist,
                str(audio_path),
                str(lyrics_path) if lyrics_path else None,
            ),
        )
        conn.commit()
        return cur.lastrowid


def update_lyrics_path(song_id, lyrics_path):
    with connect() as conn:
        conn.execute(
            "UPDATE songs SET lyrics_path = ? WHERE id = ?",
            (str(lyrics_path), song_id),
        )
        conn.commit()


def list_songs(search=""):
    with connect() as conn:
        if search.strip():
            q = f"%{search.strip()}%"
            return conn.execute(
                """
                SELECT *
                FROM songs
                WHERE title LIKE ? OR artist LIKE ?
                ORDER BY title COLLATE NOCASE
                """,
                (q, q),
            ).fetchall()

        return conn.execute(
            """
            SELECT *
            FROM songs
            ORDER BY title COLLATE NOCASE
            """
        ).fetchall()



def get_song_by_audio_path(audio_path):
    target = str(Path(audio_path))
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM songs WHERE audio_path = ?",
            (target,),
        ).fetchone()
        return row


def delete_song(song_id):
    with connect() as conn:
        conn.execute("DELETE FROM songs WHERE id = ?", (song_id,))
        conn.commit()
