from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.core.responses import ok
from backend.app.models import CorrectionResult, DailyTask, QuestionResult, Submission

router = APIRouter(prefix="/results", tags=["results"])


@router.get("/tasks/{task_id}")
def task_result(task_id: int, db: Session = Depends(get_db)):
    task = db.get(DailyTask, task_id)
    submission = db.query(Submission).filter(Submission.daily_task_id == task_id).order_by(Submission.id.desc()).first()
    result = db.query(CorrectionResult).filter(CorrectionResult.daily_task_id == task_id).order_by(CorrectionResult.id.desc()).first()
    questions = db.query(QuestionResult).filter(QuestionResult.correction_result_id == result.id).all() if result else []
    return ok({
        "task": {"id": task.id, "title": task.title, "status": task.status},
        "submission": {"id": submission.id, "submission_type": submission.submission_type} if submission else None,
        "result": {
            "completion_score": result.completion_score,
            "accuracy_score": result.accuracy_score,
            "confidence_score": result.confidence_score,
            "study_duration_seconds": result.study_duration_seconds,
            "summary": result.summary,
            "needs_review": result.needs_review,
        } if result else None,
        "questions": [
            {"question_no": q.question_no, "is_correct": q.is_correct, "recognized_answer": q.recognized_answer, "expected_answer": q.expected_answer, "explanation": q.explanation}
            for q in questions
        ],
    })
