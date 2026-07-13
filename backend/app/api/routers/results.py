from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.core.responses import ok
from backend.app.api.deps import get_current_user
from backend.app.models import CorrectionResult, DailyTask, FamilyMember, QuestionResult, Student, Submission, User
from backend.app.schemas.requests import CorrectionReviewIn
from backend.app.services.access_service import can_access_student
from backend.app.services.result_page_service import build_result_pages
from backend.app.services.task_payload_service import task_payload

router = APIRouter(prefix="/results", tags=["results"])


@router.get("/tasks/{task_id}")
def task_result(
    task_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = db.get(DailyTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    student = db.get(Student, task.student_id)
    if not student or not can_access_student(db, user, student):
        raise HTTPException(status_code=403, detail="Task does not belong to current user")
    submission = db.query(Submission).filter(Submission.daily_task_id == task_id).order_by(Submission.id.desc()).first()
    result = db.query(CorrectionResult).filter(
        CorrectionResult.submission_id == submission.id,
    ).order_by(CorrectionResult.id.desc()).first() if submission else None
    questions = db.query(QuestionResult).filter(
        QuestionResult.correction_result_id == result.id,
    ).order_by(QuestionResult.id).all() if result else []
    return ok({
        "task": task_payload(db, task),
        "submission": {
            "id": submission.id,
            "submission_type": submission.submission_type,
            "status": submission.status,
            "error_code": submission.error_code,
            "error_message": submission.error_message,
            "processing_stage": submission.processing_stage,
            "processing_message": submission.processing_message,
        } if submission else None,
        "result": {
            "completion_score": result.completion_score,
            "accuracy_score": result.accuracy_score,
            "confidence_score": result.confidence_score,
            "study_duration_seconds": result.study_duration_seconds,
            "summary": result.summary,
            "needs_review": result.needs_review,
            "review_reason": result.review_reason,
            "review_status": result.review_status,
            "review_note": result.review_note,
        } if result else None,
        "questions": [
            {"question_no": q.question_no, "is_correct": q.is_correct, "recognized_answer": q.recognized_answer, "expected_answer": q.expected_answer, "explanation": q.explanation, "confidence_score": q.confidence_score}
            for q in questions
        ],
        "pages": build_result_pages(db, submission, questions) if submission else [],
    })


@router.post("/tasks/{task_id}/review")
def review_task_result(
    task_id: int,
    payload: CorrectionReviewIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user.role != "parent":
        raise HTTPException(status_code=403, detail="Only parents can review correction results")
    task = db.get(DailyTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    member = db.query(FamilyMember).filter(
        FamilyMember.user_id == user.id,
        FamilyMember.status == "active",
    ).order_by(FamilyMember.id.desc()).first()
    student = db.get(Student, task.student_id)
    if not member or not student or student.family_id != member.family_id:
        raise HTTPException(status_code=403, detail="Task does not belong to current family")
    submission = db.query(Submission).filter(Submission.daily_task_id == task_id).order_by(Submission.id.desc()).first()
    result = db.query(CorrectionResult).filter(
        CorrectionResult.submission_id == submission.id,
    ).order_by(CorrectionResult.id.desc()).first() if submission else None
    if not submission or not result:
        raise HTTPException(status_code=404, detail="Correction result not found")
    if not result.needs_review:
        raise HTTPException(status_code=409, detail="Correction result does not need review")

    result.reviewed_at = datetime.now(UTC).replace(tzinfo=None)
    result.review_note = payload.note.strip() if payload.note and payload.note.strip() else None
    if payload.action == "confirm":
        result.needs_review = False
        result.review_status = "confirmed"
        submission.status = "corrected"
        submission.error_code = None
        submission.error_message = None
        task.status = "corrected"
    else:
        result.review_status = "resubmit_required"
        submission.status = "resubmit_required"
        submission.error_code = "resubmit_required"
        submission.error_message = result.review_note or "家长要求重新提交作业。"
        task.status = "resubmit_required"
    db.commit()
    return ok({
        "task_id": task.id,
        "submission_status": submission.status,
        "review_status": result.review_status,
    })
