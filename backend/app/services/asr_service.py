import httpx

from backend.app.core.config import Settings, settings
from backend.app.services.ai_config import api_key_for, base_url_for, service_is_configured


def transcribe_audio_url(audio_url: str, settings: Settings = settings) -> str:
    if not audio_url.startswith(("http://", "https://")):
        return ""
    if not service_is_configured(settings, "asr"):
        return ""

    payload = {
        "model": settings.asr_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "input_audio", "input_audio": {"url": audio_url}},
                    {"type": "text", "text": "请转写这段音频，保留中文标点。"},
                ],
            }
        ],
    }
    response = httpx.post(
        f"{base_url_for(settings, 'asr').rstrip('/')}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key_for(settings, 'asr')}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=settings.asr_timeout_seconds,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]
