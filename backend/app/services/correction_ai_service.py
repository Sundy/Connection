import base64
import json
import mimetypes
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.models import AssignmentItem, DailyTask, Submission, SubmissionMedia
from backend.app.services.ai_config import api_key_for, base_url_for, service_is_configured
from backend.app.services.asr_service import transcribe_audio_url
from backend.app.services.document_extract_service import extract_text_from_document
from backend.app.services.local_file_service import local_path_for_submission_media
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
    assignment_item = db.get(AssignmentItem, task.assignment_item_id) if task else None
    assignment_text = assignment_item.source_text if assignment_item and assignment_item.source_text else ""
    answer_text = (
        submission.answer_text
        or (assignment_item.answer_text if assignment_item and assignment_item.answer_text else "")
        or ""
    )
    media = db.query(SubmissionMedia).filter(
        SubmissionMedia.submission_id == submission.id,
    ).order_by(SubmissionMedia.sort_order).limit(settings.vision_max_images).all()
    answer_files = [item for item in media if item.purpose == "answer"]
    homework_files = [item for item in media if item.purpose != "answer"]
    answer_file_texts = [
        extract_text_from_document(str(local_path_for_submission_media(item)), item.media_type)
        for item in answer_files
        if item.media_type not in {"image", "audio", "video"}
    ]
    answer_file_text = "\n".join(text for text in answer_file_texts if text)
    content: list[dict] = [{
        "type": "text",
        "text": (
            "请批改学生提交的作业，输出 JSON："
            "completion_score, accuracy_score, confidence_score, summary, needs_review, "
            "review_reason, questions。questions 每项包含 question_no, question_type, "
            "recognized_answer, expected_answer, is_correct, score, explanation, confidence_score。"
            f"任务：{task.title if task else ''}；"
            f"作业原文：{assignment_text or '未提供'}；"
            f"标准答案：{answer_text or answer_file_text or '未提供，请根据题目和学生提交内容由大模型判断，并在低置信度时标记 needs_review'}；"
            f"提交备注：{submission.student_note or ''}"
        ),
    }]

    transcripts: list[str] = []
    for item in homework_files:
        local_path = str(local_path_for_submission_media(item))
        if item.media_type == "image":
            image_part = _image_message_part(local_path)
            if image_part:
                content.append(image_part)
        elif item.media_type in {"audio", "video"}:
            audio_url = prepare_audio_url(local_path, item.media_type)
            transcript = transcribe_audio_url(audio_url) if audio_url else ""
            if transcript:
                transcripts.append(transcript)

    if transcripts:
        content.append({"type": "text", "text": "音视频转写：\n" + "\n".join(transcripts)})

    answer_images = [item for item in answer_files if item.media_type == "image"]
    if answer_images:
        content.append({"type": "text", "text": "以下图片是标准答案或参考答案："})
        for item in answer_images:
            image_part = _image_message_part(str(local_path_for_submission_media(item)))
            if image_part:
                content.append(image_part)

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
