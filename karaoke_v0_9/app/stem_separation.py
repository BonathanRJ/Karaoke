import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def _safe_stem(value):
    value = re.sub(r'[<>:"/\\|?*]+', '_', str(value)).strip().rstrip('.')
    return value or 'musica'


def _find_stem_file(root, filename):
    matches = list(Path(root).rglob(filename))
    return matches[0] if matches else None


def _convert_wav_to_mp3(wav_path, mp3_path):
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        return None
    command = [
        ffmpeg, '-y', '-hide_banner', '-loglevel', 'error',
        '-i', str(wav_path), '-codec:a', 'libmp3lame', '-b:a', '192k', str(mp3_path),
    ]
    subprocess.run(command, check=True, capture_output=True, text=True)
    return Path(mp3_path)


def separate_vocals_and_instrumental(audio_path, work_dir, final_dir=None, model_name='htdemucs'):
    """Separa uma faixa em vocal e acompanhamento usando Demucs.

    O Demucs gera WAV para evitar depender do ffmpeg. Quando ffmpeg estiver
    disponível, o acompanhamento final é convertido para MP3 para economizar
    espaço. O stem de voz permanece em WAV porque é usado somente como fonte
    limpa para a transcrição.
    """
    audio_path = Path(audio_path).resolve()
    work_dir = Path(work_dir).resolve()
    final_dir = Path(final_dir or audio_path.parent).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    final_dir.mkdir(parents=True, exist_ok=True)

    if not audio_path.exists():
        raise RuntimeError(f'Arquivo de áudio não encontrado: {audio_path}')

    try:
        import demucs  # noqa: F401
    except ImportError as error:
        raise RuntimeError(
            "A separação automática precisa do pacote 'demucs'. "
            "Instale as dependências novamente com: python -m pip install -r requirements.txt"
        ) from error

    command = [
        sys.executable,
        '-m',
        'demucs.separate',
        '--two-stems', 'vocals',
        '-n', model_name,
        '-o', str(work_dir),
        str(audio_path),
    ]

    try:
        env = os.environ.copy()
        env['PYTHONUTF8'] = '1'
        env['PYTHONIOENCODING'] = 'utf-8'
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            check=False,
            env=env,
        )
    except OSError as error:
        raise RuntimeError(f'Não foi possível iniciar o Demucs: {error}') from error

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or '').strip()
        if len(detail) > 2500:
            detail = detail[-2500:]
        raise RuntimeError(
            'O Demucs não conseguiu separar a música.'
            + (f'\n\nDetalhes:\n{detail}' if detail else '')
        )

    vocals = _find_stem_file(work_dir, 'vocals.wav')
    instrumental = _find_stem_file(work_dir, 'no_vocals.wav')
    if not vocals or not instrumental:
        raise RuntimeError(
            'A separação terminou, mas os arquivos vocals.wav/no_vocals.wav não foram localizados.'
        )

    base = _safe_stem(audio_path.stem)
    vocals_final = final_dir / f'{base} [Vocals].wav'
    instrumental_wav = final_dir / f'{base} [Instrumental].wav'

    shutil.copy2(vocals, vocals_final)
    shutil.copy2(instrumental, instrumental_wav)

    instrumental_final = instrumental_wav
    mp3_candidate = final_dir / f'{base} [Instrumental].mp3'
    try:
        converted = _convert_wav_to_mp3(instrumental_wav, mp3_candidate)
        if converted and converted.exists():
            instrumental_final = converted
            try:
                instrumental_wav.unlink()
            except OSError:
                pass
    except (OSError, subprocess.SubprocessError):
        instrumental_final = instrumental_wav

    return str(vocals_final), str(instrumental_final)
