from pathlib import Path

from backend.app.core.config import Settings, settings


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
    return f"https://{config.aliyun_oss_bucket}.{config.aliyun_oss_endpoint}/{object_key}"


def upload_file_to_oss(file_path: str, object_key: str | None = None, config: Settings = settings) -> str:
    if not oss_is_configured(config):
        return ""

    try:
        import oss2
    except ImportError:
        return ""

    path = Path(file_path)
    key = object_key or f"{config.aliyun_oss_prefix.strip('/')}/{path.name}"
    auth = oss2.Auth(config.aliyun_access_key_id, config.aliyun_access_key_secret)
    bucket = oss2.Bucket(auth, config.aliyun_oss_endpoint, config.aliyun_oss_bucket)
    bucket.put_object_from_file(key, str(path))
    return build_public_url(config, key)
