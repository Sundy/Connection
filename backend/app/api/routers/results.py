from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.core.responses import ok
from backend.app.models import CorrectionResult, DailyTask, QuestionResult, Submission
from backend.app.services.task_payload_service import task_payload

router = APIRouter(prefix="/results", tags=["results"])


@router.get("/tasks/{task_id}")
def task_result(task_id: int, db: Session = Depends(get_db)):
    task = db.get(DailyTask, task_id)
    submission = db.query(Submission).filter(Submission.daily_task_id == task_id).order_by(Submission.id.desc()).first()
    result = db.query(CorrectionResult).filter(
        CorrectionResult.submission_id == submission.id,
    ).order_by(CorrectionResult.id.desc()).first() if submission else None
    questions = db.query(QuestionResult).filter(QuestionResult.correction_result_id == result.id).all() if result else []
    return ok({
        "task": task_payload(db, task),
        "submission": {
            "id": submission.id,
            "submission_type": submission.submission_type,
            "status": submission.status,
            "error_code": submission.error_code,
            "error_message": submission.error_message,
        } if submission else None,
        "result": {
            "completion_score": result.completion_score,
            "accuracy_score": result.accuracy_score,
            "confidence_score": result.confidence_score,
            "study_duration_seconds": result.study_duration_seconds,
            "summary": result.summary,
            "needs_review": result.needs_review,
            "review_reason": result.review_reason,
        } if result else None,
        "questions": [
            {"question_no": q.question_no, "is_correct": q.is_correct, "recognized_answer": q.recognized_answer, "expected_answer": q.expected_answer, "explanation": q.explanation, "confidence_score": q.confidence_score}
            for q in questions
        ],
    })
