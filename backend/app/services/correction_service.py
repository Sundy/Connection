import json

from sqlalchemy.orm import Session

from backend.app.models import CorrectionResult, DailyTask, QuestionResult, StudySession, Submission, SubmissionMedia
from backend.app.services.correction_annotation_service import remove_conclusion_annotations
from backend.app.services.correction_ai_service import build_ai_correction_payload
from backend.app.services.submission_media_service import homework_images_for_annotation


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


def _mark_missing_pages_for_review(payload: dict, page_count: int) -> dict:
    covered_pages = {
        _safe_int(question.get("source_image_index"))
        for question in payload.get("questions") or []
        if isinstance(question, dict)
    }
    missing_pages = [
        page_number
        for page_number in range(1, page_count + 1)
        if page_number not in covered_pages
    ]
    if not missing_pages:
        return payload

    missing_page_text = "、".join(str(page) for page in missing_pages)
    missing_reason = f"第 {missing_page_text} 页未生成批改结果"
    existing_reason = str(payload.get("review_reason") or "").strip()
    updated_payload = dict(payload)
    updated_payload["needs_review"] = True
    updated_payload["review_reason"] = "；".join(
        reason for reason in (existing_reason, missing_reason) if reason
    )
    return updated_payload


def _question_result_from_payload(
    correction_result_id: int,
    question: dict,
    media_ids_by_index: dict[int, int] | None = None,
) -> QuestionResult:
    source_image_index = _safe_int(question.get("source_image_index"))
    source_media_id = (media_ids_by_index or {}).get(source_image_index)
    annotations = question.get("annotations") or []
    if question.get("is_correct") is None and isinstance(annotations, list):
        annotations = remove_conclusion_annotations(annotations)
    return QuestionResult(
        correction_result_id=correction_result_id,
        section_no=question.get("section_no"),
        question_no=str(question.get("question_no") or ""),
        subquestion_no=question.get("subquestion_no"),
        question_type=question.get("question_type") or "unknown",
        recognized_answer=question.get("recognized_answer"),
        expected_answer=question.get("expected_answer"),
        is_correct=question.get("is_correct"),
        score=question.get("score"),
        explanation=question.get("explanation"),
        confidence_score=question.get("confidence_score"),
        source_media_id=source_media_id,
        annotations_json=json.dumps(annotations, ensure_ascii=False),
    )


def _create_result_from_payload(
    db: Session,
    submission: Submission,
    payload: dict,
    media_ids_by_index: dict[int, int] | None = None,
) -> CorrectionResult:
    duration = 0
    if submission.linked_study_session_id is not None:
        duration = db.query(StudySession.duration_seconds).filter(
            StudySession.id == submission.linked_study_session_id,
            StudySession.daily_task_id == submission.daily_task_id,
            StudySession.student_id == submission.student_id,
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
        db.add(_question_result_from_payload(
            result.id,
            question,
            media_ids_by_index,
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
    homework_images = homework_images_for_annotation(db, submission.id)
    media_ids_by_index = {index: media.id for index, media in enumerate(homework_images, start=1)}
    set_processing_stage(db, submission, "grading", "正在按大题批改")
    payload = build_ai_correction_payload(db, submission)
    set_processing_stage(db, submission, "annotating", "正在生成卷面批注")
    if not payload:
        raise RuntimeError("Correction service returned no usable result")
    payload = _mark_missing_pages_for_review(payload, len(homework_images))
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
