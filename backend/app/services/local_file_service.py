from pathlib import Path
from urllib.parse import urlparse

import httpx

from backend.app.models import ImportFile, SubmissionMedia
from backend.app.core.config import settings


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def upload_root() -> Path:
    configured = Path(settings.upload_dir)
    if configured.is_absolute():
        return configured
    return (PROJECT_ROOT / configured).resolve()


def upload_subdir(*parts: str) -> Path:
    path = upload_root().joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_local_file(path_value: str) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return (PROJECT_ROOT / path).resolve()


def is_remote_url(value: str | None) -> bool:
    return bool(value and value.startswith(("http://", "https://")))


def local_path_for_import_file(item: ImportFile) -> Path:
    if item.storage_path:
        return resolve_local_file(item.storage_path)
    return resolve_local_file(item.file_url)


def local_path_for_submission_media(item: SubmissionMedia) -> Path:
    if item.storage_path:
        return resolve_local_file(item.storage_path)
    return resolve_local_file(item.file_url)


def materialize_remote_file(url: str, target_dir: Path, file_name: str) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    parsed = urlparse(url)
    suffix = Path(parsed.path).suffix or Path(file_name).suffix
    target = target_dir / (file_name if Path(file_name).suffix else f"{file_name}{suffix}")
    if target.exists():
        return target
    response = httpx.get(url, timeout=60)
    response.raise_for_status()
    target.write_bytes(response.content)
    return target
