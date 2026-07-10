from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.app.models import AssignmentItem, CorrectionResult, DailyTask, QuestionResult, StudySession, Submission
from backend.app.services.correction_ai_service import build_ai_correction_payload


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
    submission.status = "corrected"
    if task:
        task.status = "corrected"
    db.commit()
    db.refresh(result)
    return result


def create_mock_correction(db: Session, submission: Submission) -> CorrectionResult:
    try:
        payload = build_ai_correction_payload(db, submission)
    except Exception:
        payload = None
    if payload:
        return _create_result_from_payload(db, submission, payload)

    duration = db.query(func.coalesce(func.sum(StudySession.duration_seconds), 0)).filter(
        StudySession.daily_task_id == submission.daily_task_id,
        StudySession.status == "completed",
    ).scalar()
    task = db.get(DailyTask, submission.daily_task_id)
    assignment_item = db.get(AssignmentItem, task.assignment_item_id) if task else None
    expected_answer = assignment_item.answer_text if assignment_item and assignment_item.answer_text else "标准答案未提供，需结合题目判断。"
    is_video = submission.submission_type == "video"
    result = CorrectionResult(
        submission_id=submission.id,
        daily_task_id=submission.daily_task_id,
        completion_score=92 if not is_video else 88,
        accuracy_score=None if is_video else 82,
        confidence_score=78 if is_video else 86,
        study_duration_seconds=int(duration or 0),
        summary="视频已提交，已生成基础完成度评估。" if is_video else "整体完成较好，部分题目建议复习。",
        needs_review=is_video,
        review_reason="视频类作业首版建议家长复核。" if is_video else None,
    )
    db.add(result)
    db.flush()
    if not is_video:
        db.add(QuestionResult(
            correction_result_id=result.id,
            question_no="3",
            question_type="calculation",
            recognized_answer="36",
            expected_answer=expected_answer,
            is_correct=False,
            score=0,
            explanation="计算过程可能存在错误，建议结合标准答案或题目要求复核。",
            confidence_score=0.82,
        ))
    submission.status = "corrected"
    if task:
        task.status = "corrected"
    db.commit()
    db.refresh(result)
    return result
