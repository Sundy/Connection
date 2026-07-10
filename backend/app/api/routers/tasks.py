from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.core.responses import ok
from backend.app.models import AssignmentItem, DailyTask

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _task_payload(db: Session, task: DailyTask) -> dict:
    item = db.get(AssignmentItem, task.assignment_item_id)
    answer_text = item.answer_text if item else None
    return {
        "id": task.id,
        "subject": task.subject,
        "title": task.title,
        "task_type": task.task_type,
        "submit_type": task.submit_type,
        "estimated_minutes": task.estimated_minutes,
        "status": task.status,
        "task_date": task.task_date,
        "planned_quantity": task.planned_quantity,
        "unit": task.unit,
        "source_text": item.source_text if item else "",
        "has_answer": bool(answer_text and answer_text.strip()),
    }


@router.get("/today")
def today(student_id: int, target_date: date | None = None, db: Session = Depends(get_db)):
    day = target_date or date.today()
    tasks = db.query(DailyTask).filter(DailyTask.student_id == student_id, DailyTask.task_date == day).all()
    return ok({
        "date": day,
        "summary": {
            "total_tasks": len(tasks),
            "completed_tasks": len([t for t in tasks if t.status == "corrected"]),
            "study_duration_seconds": 0,
        },
        "tasks": [_task_payload(db, task) for task in tasks],
    })


@router.get("/{task_id}")
def detail(task_id: int, db: Session = Depends(get_db)):
    task = db.get(DailyTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return ok(_task_payload(db, task))


@router.post("/{task_id}/mark-ready")
def mark_ready(task_id: int, db: Session = Depends(get_db)):
    task = db.get(DailyTask, task_id)
    task.status = "ready_to_submit"
    db.commit()
    return ok({"id": task.id, "status": task.status})
