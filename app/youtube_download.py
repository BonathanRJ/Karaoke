import shutil
from pathlib import Path

from PySide6.QtCore import QObject, Signal


class YouTubeDownloadWorker(QObject):
    progress = Signal(int, str)
    finished = Signal(str, str, str)
    failed = Signal(str)

    def __init__(self, url, music_dir):
        super().__init__()
        self.url = url.strip()
        self.music_dir = Path(music_dir)

    def _progress_hook(self, data):
        status = data.get("status")
        if status == "downloading":
            downloaded = int(data.get("downloaded_bytes") or 0)
            total = int(data.get("total_bytes") or data.get("total_bytes_estimate") or 0)
            percent = int(downloaded * 100 / total) if total > 0 else 0

            eta = data.get("eta")
            speed = data.get("speed")
            details = []
            if speed:
                details.append(f"{speed / 1024 / 1024:.1f} MB/s")
            if eta is not None:
                details.append(f"ETA {int(eta)}s")

            text = "Baixando áudio"
            if details:
                text += " — " + " | ".join(details)
            self.progress.emit(max(0, min(100, percent)), text)
        elif status == "finished":
            self.progress.emit(100, "Download concluído. Preparando arquivo...")

    def run(self):
        try:
            if not self.url:
                raise RuntimeError("Informe um link do YouTube.")

            try:
                import yt_dlp
            except ImportError as error:
                raise RuntimeError(
                    "O recurso de download do YouTube precisa do pacote 'yt-dlp'. "
                    "Instale novamente as dependências com: "
                    "python -m pip install -r requirements.txt"
                ) from error

            self.music_dir.mkdir(parents=True, exist_ok=True)
            has_ffmpeg = shutil.which("ffmpeg") is not None

            output_template = str(
                self.music_dir / "%(title).160B [%(id)s].%(ext)s"
            )

            options = {
                "format": "bestaudio/best" if has_ffmpeg else "bestaudio[ext=m4a]/bestaudio[ext=mp4]/bestaudio",
                "outtmpl": output_template,
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
                "progress_hooks": [self._progress_hook],
                "windowsfilenames": True,
                "retries": 3,
                "fragment_retries": 3,
            }

            if has_ffmpeg:
                options["postprocessors"] = [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }
                ]

            self.progress.emit(0, "Lendo informações do YouTube...")

            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(self.url, download=True)
                if info is None:
                    raise RuntimeError("Não foi possível obter informações do vídeo.")

                prepared = Path(ydl.prepare_filename(info))

            if has_ffmpeg:
                final_path = prepared.with_suffix(".mp3")
            else:
                final_path = prepared

            if not final_path.exists():
                requested = info.get("requested_downloads") or []
                candidates = []
                for item in requested:
                    filepath = item.get("filepath")
                    if filepath:
                        candidates.append(Path(filepath))
                video_id = str(info.get("id") or "")
                if video_id:
                    candidates.extend(
                        path for path in self.music_dir.iterdir()
                        if path.is_file() and video_id in path.name
                    )
                existing = next((path for path in candidates if path.exists()), None)
                if existing:
                    final_path = existing

            if not final_path.exists():
                raise RuntimeError(
                    "O download terminou, mas o arquivo de áudio não foi localizado na pasta music."
                )

            title = (info.get("track") or info.get("title") or final_path.stem).strip()
            artist = (
                info.get("artist")
                or info.get("creator")
                or info.get("uploader")
                or info.get("channel")
                or ""
            ).strip()

            self.finished.emit(title, artist, str(final_path.resolve()))
        except Exception as error:
            self.failed.emit(str(error))
