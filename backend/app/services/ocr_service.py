import base64
import mimetypes
from pathlib import Path

import httpx

from backend.app.core.config import settings
from backend.app.services.ai_config import api_key_for, base_url_for, service_is_configured


def _image_content(file_path: str) -> dict | None:
    path = Path(file_path)
    mime_type, _ = mimetypes.guess_type(path.name)
    if not mime_type or not mime_type.startswith("image/"):
        return None
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded}"}}


def extract_text_from_file(file_path: str, file_type: str) -> str:
    if not service_is_configured(settings, "ocr"):
        return ""

    content = _image_content(file_path)
    if not content:
        return ""

    payload = {
        "model": settings.ocr_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    content,
                    {"type": "text", "text": "请提取这份作业资料中的全部文字，保留学科、数量、单位和日期信息。"},
                ],
            }
        ],
    }
    response = httpx.post(
        f"{base_url_for(settings, 'ocr').rstrip('/')}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key_for(settings, 'ocr')}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=settings.ocr_timeout_seconds,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]
