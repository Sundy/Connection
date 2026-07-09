from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.app.models import CorrectionResult, DailyTask, QuestionResult, StudySession, Submission


def create_mock_correction(db: Session, submission: Submission) -> CorrectionResult:
    duration = db.query(func.coalesce(func.sum(StudySession.duration_seconds), 0)).filter(
        StudySession.daily_task_id == submission.daily_task_id,
        StudySession.status == "completed",
    ).scalar()
    task = db.get(DailyTask, submission.daily_task_id)
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
            expected_answer="32",
            is_correct=False,
            score=0,
            explanation="计算过程可能存在进位错误。",
            confidence_score=0.82,
        ))
    submission.status = "corrected"
    if task:
        task.status = "corrected"
    db.commit()
    db.refresh(result)
    return result
