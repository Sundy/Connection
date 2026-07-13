import base64
import json
import mimetypes
from pathlib import Path
import shutil

import httpx
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.models import AssignmentItem, DailyTask, Submission, SubmissionMedia
from backend.app.services.ai_config import api_key_for, base_url_for, service_is_configured
from backend.app.services.asr_service import transcribe_audio_url
from backend.app.services.correction_annotation_service import group_questions
from backend.app.services.local_file_service import local_path_for_submission_media
from backend.app.services.media_processing_service import extract_video_frames, prepare_audio_url
from backend.app.services.submission_media_service import homework_images_for_annotation


SPEECH_TASK_TYPES = {"recitation", "reading", "oral", "speaking"}
VISUAL_TITLE_KEYWORDS = ("书写", "计算", "过程", "操作", "演示")
SPEECH_TITLE_KEYWORDS = ("朗读", "背诵", "口语", "跟读")


def classify_video_strategy(task: DailyTask) -> str:
    task_type = (task.task_type or "").lower()
    if task_type in SPEECH_TASK_TYPES or any(word in (task.title or "") for word in SPEECH_TITLE_KEYWORDS):
        return "speech"
    if task_type == "written" or any(word in (task.title or "") for word in VISUAL_TITLE_KEYWORDS):
        return "visual"
    return "mixed"


def _score(value, *, confidence: bool = False, nullable: bool = False):
    if value is None and nullable:
        return None
    try:
        number = float(value or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid numeric correction result") from exc
    if confidence and number > 1:
        number /= 100
    return max(0, min(1 if confidence else 100, number))


def normalize_correction_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Correction result must be an object")
    confidence = _score(payload.get("confidence_score"), confidence=True)
    questions = group_questions(
        payload.get("questions"),
        threshold=settings.annotation_confidence_threshold,
    )
    has_uncertain_question = any(question.get("is_correct") is None for question in questions)
    for question in questions:
        question["score"] = _score(question.get("score"), nullable=True)
        question["confidence_score"] = _score(
            question.get("confidence_score"),
            confidence=True,
            nullable=True,
        )
    needs_review = bool(payload.get("needs_review")) or confidence < 0.6 or has_uncertain_question
    review_reason = payload.get("review_reason")
    if needs_review and not review_reason:
        review_reason = "模型置信度较低或存在无法可靠判断的题目。"
    return {
        "completion_score": _score(payload.get("completion_score")),
        "accuracy_score": _score(payload.get("accuracy_score"), nullable=True),
        "confidence_score": confidence,
        "summary": str(payload.get("summary") or ""),
        "needs_review": needs_review,
        "review_reason": review_reason,
        "questions": questions,
    }


def parse_correction_content(content_text: str) -> dict:
    text = content_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
        text = "\n".join(lines)
    return normalize_correction_payload(json.loads(text))


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
    answer_text = assignment_item.answer_text if assignment_item and assignment_item.answer_text else ""
    homework_images = homework_images_for_annotation(db, submission.id, limit=settings.vision_max_images)
    homework_media = db.query(SubmissionMedia).filter(
        SubmissionMedia.submission_id == submission.id,
        SubmissionMedia.purpose == "homework",
    ).order_by(SubmissionMedia.sort_order, SubmissionMedia.id).all()
    homework_non_images = [item for item in homework_media if item.media_type != "image"]
    content: list[dict] = [{
        "type": "text",
        "text": (
            "请批改学生提交的作业，输出 JSON："
            "completion_score, accuracy_score, confidence_score, summary, needs_review, "
            "review_reason, questions。questions 每项包含 question_no, question_type, "
            "recognized_answer, expected_answer, is_correct, score, explanation, confidence_score。"
            "请按印刷的大题号合并批改结果，同一大题的(1)(2)(3)不得拆成多条。"
            "每道大题返回 source_image_index、question_no、question_type、recognized_answer、"
            "expected_answer、is_correct、score、explanation、confidence_score、annotations。"
            "annotations 每项包含 kind、x、y、width、height、text、confidence；"
            "坐标是相对原图宽高的 0 到 1 小数。正确位置用 correct_tick，"
            "错误位置用 error_circle、error_cross 和必要的 comment。"
            f"任务：{task.title if task else ''}；"
            f"作业原文：{assignment_text or '未提供'}；"
            f"标准答案：{answer_text or '未提供，请根据题目和学生提交内容由大模型判断，并在低置信度时标记 needs_review'}；"
            f"提交备注：{submission.student_note or ''}"
        ),
    }]

    transcripts: list[str] = []
    frame_paths: list[str] = []
    video_strategy = classify_video_strategy(task) if task and any(item.media_type == "video" for item in homework_non_images) else None
    for image_index, item in enumerate(homework_images, start=1):
        local_path = str(local_path_for_submission_media(item))
        image_part = _image_message_part(local_path)
        if image_part:
            content.append({"type": "text", "text": f"学生作业照片 {image_index}"})
            content.append(image_part)

    for item in homework_non_images:
        local_path = str(local_path_for_submission_media(item))
        if item.media_type == "audio" or (item.media_type == "video" and video_strategy in {"speech", "mixed"}):
            audio_url = prepare_audio_url(local_path, item.media_type)
            transcript = transcribe_audio_url(audio_url) if audio_url else ""
            if transcript:
                transcripts.append(transcript)
        if item.media_type == "video" and video_strategy in {"visual", "mixed"}:
            frame_paths.extend(extract_video_frames(local_path, settings.video_max_frames))

    if transcripts:
        content.append({"type": "text", "text": "音视频转写：\n" + "\n".join(transcripts)})

    if frame_paths:
        content.append({"type": "text", "text": "以下图片是视频中的关键帧："})
        for frame_path in frame_paths:
            image_part = _image_message_part(frame_path)
            if image_part:
                content.append(image_part)

    if len(content) == 1:
        return None

    payload = {
        "model": settings.vision_model,
        "temperature": settings.llm_temperature,
        "messages": [{"role": "user", "content": content}],
    }
    try:
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
        parsed = parse_correction_content(content_text)
        if video_strategy == "mixed":
            parsed["needs_review"] = True
            parsed["review_reason"] = parsed.get("review_reason") or "视频任务类型不明确，需要家长复核。"
        return parsed
    finally:
        for directory in {str(Path(path).parent) for path in frame_paths}:
            shutil.rmtree(directory, ignore_errors=True)
