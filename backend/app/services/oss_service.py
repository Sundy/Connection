from pathlib import Path
import re
from datetime import date
from urllib.parse import unquote, urlparse
from uuid import uuid4

from backend.app.core.config import Settings, settings

try:
    import oss2
except ImportError:  # pragma: no cover - exercised in deployments without OSS extras
    oss2 = None


def oss_is_configured(config: Settings = settings) -> bool:
    return bool(
        config.aliyun_access_key_id
        and config.aliyun_access_key_secret
        and config.aliyun_oss_endpoint
        and config.aliyun_oss_bucket
    )


def build_public_url(config: Settings, object_key: str) -> str:
    if config.aliyun_oss_public_base_url:
        return f"{config.aliyun_oss_public_base_url.rstrip('/')}/{object_key}"
    return f"https://{config.aliyun_oss_bucket}.{endpoint_host(config)}/{object_key}"


def endpoint_host(config: Settings) -> str:
    return config.aliyun_oss_endpoint.removeprefix("https://").removeprefix("http://").rstrip("/")


def bucket_endpoint(config: Settings) -> str:
    if config.aliyun_oss_endpoint.startswith("http"):
        return config.aliyun_oss_endpoint.rstrip("/")
    return f"https://{endpoint_host(config)}"


def object_key_from_oss_url(url: str, config: Settings = settings) -> str:
    if not url or not url.startswith("http"):
        return ""
    parsed = urlparse(url)
    if not parsed.path:
        return ""

    if config.aliyun_oss_public_base_url and url.startswith(config.aliyun_oss_public_base_url.rstrip("/") + "/"):
        base_path = urlparse(config.aliyun_oss_public_base_url).path.strip("/")
        key_path = parsed.path.strip("/")
        if base_path and key_path.startswith(base_path + "/"):
            key_path = key_path[len(base_path) + 1:]
        return unquote(key_path)

    expected_host = f"{config.aliyun_oss_bucket}.{endpoint_host(config)}".lower()
    if parsed.netloc.lower() != expected_host:
        return ""
    return unquote(parsed.path.lstrip("/"))


def signed_download_url(url: str, config: Settings = settings) -> str:
    key = object_key_from_oss_url(url, config)
    if not key or not oss_is_configured(config) or oss2 is None:
        return url
    auth = oss2.Auth(config.aliyun_access_key_id, config.aliyun_access_key_secret)
    bucket = oss2.Bucket(auth, bucket_endpoint(config), config.aliyun_oss_bucket)
    return bucket.sign_url("GET", key, config.aliyun_oss_signed_url_expires_seconds, slash_safe=True)


def safe_object_file_name(file_name: str, fallback_suffix: str = "") -> str:
    raw_name = Path(file_name or f"upload{fallback_suffix}").name
    suffix = Path(raw_name).suffix or fallback_suffix
    stem = Path(raw_name).stem or "upload"
    safe_stem = re.sub(r"[\s/\\:]+", "_", stem).strip("._-") or "upload"
    return f"{safe_stem}-{uuid4().hex[:8]}{suffix}"


def build_import_object_key(batch_id: int, file_name: str, fallback_suffix: str = "", config: Settings = settings) -> str:
    day = date.today().isoformat()
    safe_name = safe_object_file_name(file_name, fallback_suffix)
    return f"{config.aliyun_oss_prefix.strip('/')}/imports/{day}/batch-{batch_id}/{safe_name}"


def build_submission_object_key(
    submission_id: int,
    purpose: str,
    file_name: str,
    fallback_suffix: str = "",
    config: Settings = settings,
) -> str:
    day = date.today().isoformat()
    safe_purpose = re.sub(r"[^0-9A-Za-z_-]+", "_", purpose or "homework").strip("_") or "homework"
    safe_name = safe_object_file_name(file_name, fallback_suffix)
    return f"{config.aliyun_oss_prefix.strip('/')}/submissions/{day}/submission-{submission_id}/{safe_purpose}/{safe_name}"


def upload_file_to_oss(file_path: str, object_key: str | None = None, config: Settings = settings) -> str:
    if not oss_is_configured(config):
        return ""

    if oss2 is None:
        return ""

    path = Path(file_path)
    key = object_key or f"{config.aliyun_oss_prefix.strip('/')}/{path.name}"
    auth = oss2.Auth(config.aliyun_access_key_id, config.aliyun_access_key_secret)
    bucket = oss2.Bucket(auth, bucket_endpoint(config), config.aliyun_oss_bucket)
    bucket.put_object_from_file(key, str(path))
    return build_public_url(config, key)
