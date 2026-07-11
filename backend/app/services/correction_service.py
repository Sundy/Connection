from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.app.models import CorrectionResult, DailyTask, QuestionResult, StudySession, Submission, SubmissionMedia
from backend.app.services.correction_ai_service import build_ai_correction_payload


class MissingHomeworkMediaError(RuntimeError):
    pass


def _create_result_from_payload(db: Session, submission: Submission, payload: dict) -> CorrectionResult:
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
    )
    db.add(result)
    db.flush()
    for question in payload.get("questions") or []:
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
        ))
    submission.status = "needs_review" if result.needs_review else "corrected"
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
    payload = build_ai_correction_payload(db, submission)
    if not payload:
        raise RuntimeError("Correction service returned no usable result")
    return _create_result_from_payload(db, submission, payload)


def mark_correction_failed(
    db: Session,
    submission: Submission,
    error_code: str = "correction_failed",
    error_message: str = "批改服务暂时不可用，请稍后重试。",
) -> None:
    submission.status = "failed"
    submission.error_code = error_code
    submission.error_message = error_message
    task = db.get(DailyTask, submission.daily_task_id)
    if task:
        task.status = "failed"
    db.commit()
