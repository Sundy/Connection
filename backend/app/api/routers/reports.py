from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.core.responses import ok
from backend.app.models import AssignmentBatch, CorrectionResult, DailyTask, StudySession

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/home")
def home(student_id: int, db: Session = Depends(get_db)):
    today_tasks = db.query(DailyTask).filter(DailyTask.student_id == student_id, DailyTask.task_date == date.today()).all()
    plan = db.query(AssignmentBatch).filter(AssignmentBatch.student_id == student_id).order_by(AssignmentBatch.id.desc()).first()
    all_tasks = db.query(DailyTask).filter(DailyTask.assignment_batch_id == plan.id).all() if plan else []
    done = len([t for t in all_tasks if t.status == "corrected"])
    duration = db.query(func.coalesce(func.sum(StudySession.duration_seconds), 0)).filter(StudySession.student_id == student_id).scalar()
    return ok({
        "today": {
            "total_tasks": len(today_tasks),
            "submitted_tasks": len([t for t in today_tasks if t.status in {"submitted", "correcting", "corrected"}]),
            "corrected_tasks": len([t for t in today_tasks if t.status == "corrected"]),
            "study_duration_seconds": int(duration or 0),
        },
        "period": {
            "plan_id": plan.id if plan else None,
            "title": plan.title if plan else None,
            "completion_rate": round(done / len(all_tasks) * 100, 1) if all_tasks else 0,
            "overdue_count": len([t for t in all_tasks if t.status == "overdue"]),
        },
        "alerts": [],
    })


@router.get("/period/{plan_id}")
def period(plan_id: int, db: Session = Depends(get_db)):
    tasks = db.query(DailyTask).filter(DailyTask.assignment_batch_id == plan_id).all()
    results = db.query(CorrectionResult).join(DailyTask, CorrectionResult.daily_task_id == DailyTask.id).filter(DailyTask.assignment_batch_id == plan_id).all()
    done = len([t for t in tasks if t.status == "corrected"])
    return ok({
        "completion_rate": round(done / len(tasks) * 100, 1) if tasks else 0,
        "total_tasks": len(tasks),
        "corrected_tasks": done,
        "average_accuracy": round(sum(r.accuracy_score or 0 for r in results) / len(results), 1) if results else None,
        "subjects": {},
        "overdue_tasks": [t.id for t in tasks if t.status == "overdue"],
    })
