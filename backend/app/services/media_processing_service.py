from pathlib import Path
import subprocess
from uuid import uuid4

from backend.app.core.config import settings
from backend.app.services.oss_service import upload_file_to_oss


def prepare_audio_url(file_path: str, media_type: str) -> str:
    if file_path.startswith(("http://", "https://")):
        return file_path

    source = Path(file_path)
    if media_type == "audio":
        return upload_file_to_oss(str(source), f"{settings.aliyun_oss_prefix.strip('/')}/asr/{source.name}")
    if media_type != "video":
        return ""

    output = source.with_name(f"{source.stem}-{uuid4().hex}.mp3")
    command = [
        settings.ffmpeg_path,
        "-y",
        "-i",
        str(source),
        "-vn",
        "-t",
        str(settings.video_max_duration_seconds),
        "-acodec",
        "libmp3lame",
        str(output),
    ]
    try:
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return ""

    return upload_file_to_oss(str(output), f"{settings.aliyun_oss_prefix.strip('/')}/asr/{output.name}")
