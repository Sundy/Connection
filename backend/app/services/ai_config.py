from backend.app.core.config import Settings, settings


DISABLED_API_KEY_VALUES = {"", "请替换为你的DashScope API Key", "your-dashscope-api-key"}


def api_key_for(config: Settings, service: str) -> str:
    specific = getattr(config, f"{service}_api_key", "")
    return specific or config.dashscope_api_key


def base_url_for(config: Settings, service: str) -> str:
    specific = getattr(config, f"{service}_base_url", "")
    return specific or config.dashscope_base_url


def service_is_configured(config: Settings, service: str) -> bool:
    provider = getattr(config, f"{service}_provider", "")
    return provider == "qwen" and api_key_for(config, service) not in DISABLED_API_KEY_VALUES


def configured(service: str) -> bool:
    return service_is_configured(settings, service)
