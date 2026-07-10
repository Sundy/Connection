import base64
import json
import mimetypes
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.models import DailyTask, Submission, SubmissionMedia
from backend.app.services.ai_config import api_key_for, base_url_for, service_is_configured
from backend.app.services.asr_service import transcribe_audio_url
from backend.app.services.media_processing_service import prepare_audio_url


def _image_message_part(file_path: str) -> dict | None:
    path = Path(file_path)
    mime_type, _ = mimetypes.guess_type(path.name)
    if not mime_type or not mime_type.startswith("image/"):
        return None
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded}"}}


def build_ai_correction_payload(db: Session, submission: Submission) -> dict | None:
    if not service_is_configured(settings, "vision"):
        return None

    task = db.get(DailyTask, submission.daily_task_id)
    media = db.query(SubmissionMedia).filter(
        SubmissionMedia.submission_id == submission.id,
    ).order_by(SubmissionMedia.sort_order).limit(settings.vision_max_images).all()
    content: list[dict] = [{
        "type": "text",
        "text": (
            "请批改学生提交的作业，输出 JSON："
            "completion_score, accuracy_score, confidence_score, summary, needs_review, "
            "review_reason, questions。questions 每项包含 question_no, question_type, "
            "recognized_answer, expected_answer, is_correct, score, explanation, confidence_score。"
            f"任务：{task.title if task else ''}；提交备注：{submission.student_note or ''}"
        ),
    }]

    transcripts: list[str] = []
    for item in media:
        if item.media_type == "image":
            image_part = _image_message_part(item.file_url)
            if image_part:
                content.append(image_part)
        elif item.media_type in {"audio", "video"}:
            audio_url = prepare_audio_url(item.file_url, item.media_type)
            transcript = transcribe_audio_url(audio_url) if audio_url else ""
            if transcript:
                transcripts.append(transcript)

    if transcripts:
        content.append({"type": "text", "text": "音视频转写：\n" + "\n".join(transcripts)})

    if len(content) == 1:
        return None

    payload = {
        "model": settings.vision_model,
        "temperature": settings.llm_temperature,
        "messages": [{"role": "user", "content": content}],
    }
    response = httpx.post(
        f"{base_url_for(settings, 'vision').rstrip('/')}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key_for(settings, 'vision')}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=settings.vision_timeout_seconds,
    )
    response.raise_for_status()
    content_text = response.json()["choices"][0]["message"]["content"]
    parsed = json.loads(content_text)
    return parsed if isinstance(parsed, dict) else None
