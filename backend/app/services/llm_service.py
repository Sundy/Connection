import json

import httpx

from backend.app.core.config import settings
from backend.app.services.ai_config import api_key_for, base_url_for, service_is_configured


class LLMResponseError(RuntimeError):
    """The provider returned JSON that does not match the requested object shape."""


def llm_is_configured() -> bool:
    return service_is_configured(settings, "llm")


def analyze_import_file_with_llm(text: str, document_role: str) -> dict:
    if not llm_is_configured():
        return {}

    prompt = (
        "只根据正文分析，不参考文件名。输出 JSON 对象：subject, grade_hint, material, "
        "chapter, exercise_type, question_start, question_end, question_count, keywords, "
        "is_answer, recommended_title, confidence_score, content_summary。"
        "recommended_title 必须是简洁中文语义名称，不得包含 tmp、UUID、扩展名或时间戳。"
    )
    payload = {
        "model": settings.llm_model,
        "temperature": settings.llm_temperature,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": json.dumps(
                    {"document_role": document_role, "content": text},
                    ensure_ascii=False,
                ),
            },
        ],
    }

    response = httpx.post(
        f"{base_url_for(settings, 'llm').rstrip('/')}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key_for(settings, 'llm')}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=settings.llm_timeout_seconds,
    )
    response.raise_for_status()
    try:
        response_payload = response.json()
    except json.JSONDecodeError as exc:
        raise LLMResponseError("LLM response body is not valid JSON") from exc

    try:
        choices = response_payload["choices"]
        if not isinstance(choices, list) or not choices:
            raise LLMResponseError("LLM response choices must be a non-empty list")
        content = choices[0]["message"]["content"]
        if not isinstance(content, str):
            raise LLMResponseError("LLM response message content must be text")
    except (KeyError, TypeError, IndexError) as exc:
        raise LLMResponseError("LLM response has an invalid object structure") from exc

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMResponseError("LLM message content is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise LLMResponseError("LLM message content must be a JSON object")
    return parsed


def extract_assignment_items_with_llm(text: str) -> list[dict]:
    if not llm_is_configured():
        return []

    prompt = (
        "请把家长导入的作业资料拆成 JSON 数组。"
        "不要求原文显式写出学科，请根据内容判断 subject。"
        "只输出 JSON，不要 Markdown。每项字段："
        "subject,title,task_type,submit_type,source_text,total_quantity,unit,"
        "estimated_minutes_total,need_confirmation,confidence_score。"
        "subject 用中文学科名；task_type 可用 written/recitation/mixed；"
        "submit_type 可用 photo/video/mixed。"
    )
    payload = {
        "model": settings.llm_model,
        "temperature": settings.llm_temperature,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": text},
        ],
    }

    response = httpx.post(
        f"{base_url_for(settings, 'llm').rstrip('/')}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key_for(settings, 'llm')}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=settings.llm_timeout_seconds,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    return parsed if isinstance(parsed, list) else []
