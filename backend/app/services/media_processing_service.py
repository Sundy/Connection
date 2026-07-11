from pathlib import Path
import subprocess
import tempfile
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


def extract_video_frames(file_path: str, max_frames: int | None = None) -> list[str]:
    source = Path(file_path)
    if not source.exists():
        raise FileNotFoundError(file_path)
    limit = max_frames or settings.video_max_frames
    output_dir = Path(tempfile.mkdtemp(prefix="connection-video-frames-"))
    output_pattern = output_dir / "frame-%03d.jpg"
    command = [
        settings.ffmpeg_path,
        "-y",
        "-i",
        str(source),
        "-t",
        str(settings.video_max_duration_seconds),
        "-vf",
        f"fps={settings.video_frame_fps}",
        "-frames:v",
        str(limit),
        str(output_pattern),
    ]
    try:
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("Unable to extract video frames") from exc
    frames = sorted(output_dir.glob("frame-*.jpg"))[:limit]
    if not frames:
        raise RuntimeError("Video did not produce any frames")
    return [str(frame) for frame in frames]
