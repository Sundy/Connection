from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.core.responses import ok
from backend.app.models import AssignmentBatch, DailyTask
from backend.app.services.task_payload_service import subject_summary, task_payload

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("/today")
def today(student_id: int, target_date: date | None = None, db: Session = Depends(get_db)):
    day = target_date or date.today()
    tasks = (
        db.query(DailyTask)
        .join(AssignmentBatch, DailyTask.assignment_batch_id == AssignmentBatch.id)
        .filter(
            DailyTask.student_id == student_id,
            DailyTask.task_date == day,
            AssignmentBatch.status == "active",
        )
        .order_by(DailyTask.id)
        .all()
    )
    return ok({
        "date": day,
        "summary": {
            "total_tasks": len(tasks),
            "completed_tasks": sum(item["completed_tasks"] for item in subject_summary(tasks)),
            "study_duration_seconds": 0,
        },
        "subject_summary": subject_summary(tasks),
        "tasks": [task_payload(db, task) for task in tasks],
    })


@router.get("/{task_id}")
def detail(task_id: int, db: Session = Depends(get_db)):
    task = db.get(DailyTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return ok(task_payload(db, task))


@router.post("/{task_id}/mark-ready")
def mark_ready(task_id: int, db: Session = Depends(get_db)):
    task = db.get(DailyTask, task_id)
    task.status = "ready_to_submit"
    db.commit()
    return ok({"id": task.id, "status": task.status})
