import json

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.app.models import CorrectionResult, DailyTask, QuestionResult, StudySession, Submission, SubmissionMedia
from backend.app.services.correction_ai_service import build_ai_correction_payload


class MissingHomeworkMediaError(RuntimeError):
    pass


def set_processing_stage(db: Session, submission: Submission, stage: str, message: str) -> None:
    submission.processing_stage = stage
    submission.processing_message = message
    db.commit()
    db.refresh(submission)


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _create_result_from_payload(
    db: Session,
    submission: Submission,
    payload: dict,
    media_ids_by_index: dict[int, int] | None = None,
) -> CorrectionResult:
    duration = db.query(func.coalesce(func.sum(StudySession.duration_seconds), 0)).filter(
        StudySession.daily_task_id == submission.daily_task_id,
        StudySession.status == "completed",
    ).scalar()
    task = db.get(DailyTask, submission.daily_task_id)
    result = CorrectionResult(
        submission_id=submission.id,
        daily_task_id=submission.daily_task_id,
        completion_score=float(payload.get("completion_score") or 0),
        accuracy_score=payload.get("accuracy_score"),
        confidence_score=float(payload.get("confidence_score") or 0),
        study_duration_seconds=int(duration or 0),
        summary=payload.get("summary") or "",
        needs_review=bool(payload.get("needs_review")),
        review_reason=payload.get("review_reason"),
        review_status="pending" if payload.get("needs_review") else "not_required",
    )
    db.add(result)
    db.flush()
    for question in payload.get("questions") or []:
        source_image_index = _safe_int(question.get("source_image_index"))
        source_media_id = (media_ids_by_index or {}).get(source_image_index)
        annotations_json = json.dumps(question.get("annotations") or [], ensure_ascii=False)
        db.add(QuestionResult(
            correction_result_id=result.id,
            question_no=str(question.get("question_no") or ""),
            question_type=question.get("question_type") or "unknown",
            recognized_answer=question.get("recognized_answer"),
            expected_answer=question.get("expected_answer"),
            is_correct=question.get("is_correct"),
            score=question.get("score"),
            explanation=question.get("explanation"),
            confidence_score=question.get("confidence_score"),
            source_media_id=source_media_id,
            annotations_json=annotations_json,
        ))
    submission.status = "needs_review" if result.needs_review else "corrected"
    submission.processing_stage = "needs_review" if result.needs_review else "corrected"
    submission.processing_message = "等待家长确认" if result.needs_review else "批改完成"
    if task:
        task.status = submission.status
    db.commit()
    db.refresh(result)
    return result


def create_correction(db: Session, submission: Submission) -> CorrectionResult:
    has_homework_media = db.query(SubmissionMedia).filter(
        SubmissionMedia.submission_id == submission.id,
        SubmissionMedia.purpose == "homework",
    ).first() is not None
    if not has_homework_media:
        raise MissingHomeworkMediaError("Submission has no homework media")
    homework_images = db.query(SubmissionMedia).filter(
        SubmissionMedia.submission_id == submission.id,
        SubmissionMedia.purpose == "homework",
        SubmissionMedia.media_type == "image",
    ).order_by(SubmissionMedia.sort_order, SubmissionMedia.id).all()
    media_ids_by_index = {index: media.id for index, media in enumerate(homework_images, start=1)}
    set_processing_stage(db, submission, "grading", "正在按大题批改")
    payload = build_ai_correction_payload(db, submission)
    set_processing_stage(db, submission, "annotating", "正在生成卷面批注")
    if not payload:
        raise RuntimeError("Correction service returned no usable result")
    return _create_result_from_payload(db, submission, payload, media_ids_by_index)


def mark_correction_failed(
    db: Session,
    submission: Submission,
    error_code: str = "correction_failed",
    error_message: str = "批改服务暂时不可用，请稍后重试。",
) -> None:
    submission.status = "failed"
    submission.error_code = error_code
    submission.error_message = error_message
    submission.processing_stage = "failed"
    submission.processing_message = error_message
    task = db.get(DailyTask, submission.daily_task_id)
    if task:
        task.status = "failed"
    db.commit()
