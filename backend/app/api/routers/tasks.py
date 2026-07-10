from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.core.responses import ok
from backend.app.models import AssignmentBatch, DailyTask
from backend.app.services.task_payload_service import task_payload

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("/today")
def today(student_id: int, target_date: date | None = None, db: Session = Depends(get_db)):
    day = target_date or date.today()
    latest_plan = (
        db.query(AssignmentBatch)
        .join(DailyTask, DailyTask.assignment_batch_id == AssignmentBatch.id)
        .filter(
            AssignmentBatch.student_id == student_id,
            AssignmentBatch.status == "active",
            DailyTask.task_date == day,
        )
        .order_by(AssignmentBatch.created_at.desc(), AssignmentBatch.id.desc())
        .first()
    )
    query = db.query(DailyTask).filter(DailyTask.student_id == student_id, DailyTask.task_date == day)
    if latest_plan:
        query = query.filter(DailyTask.assignment_batch_id == latest_plan.id)
    tasks = query.order_by(DailyTask.id).all()
    return ok({
        "date": day,
        "summary": {
            "total_tasks": len(tasks),
            "completed_tasks": len([t for t in tasks if t.status == "corrected"]),
            "study_duration_seconds": 0,
        },
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
