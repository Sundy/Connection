from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.core.responses import ok
from backend.app.models import AssignmentBatch, AssignmentItem, DailyTask
from backend.app.schemas.requests import PlanConfirmIn
from backend.app.services.planning_service import confirm_plan, generate_plan_from_import
from backend.app.services.task_payload_service import COMPLETED_TASK_STATUSES, source_file_payload, subject_summary, task_payload

router = APIRouter(prefix="/plans", tags=["plans"])


def _source_file_payload(db: Session, item: AssignmentItem) -> dict | None:
    return source_file_payload(db, item)


@router.post("/from-import/{batch_id}/generate")
def generate_from_import(batch_id: int, db: Session = Depends(get_db)):
    try:
        plan = generate_plan_from_import(db, batch_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    items = db.query(AssignmentItem).filter(AssignmentItem.assignment_batch_id == plan.id).all()
    tasks = db.query(DailyTask).filter(DailyTask.assignment_batch_id == plan.id).all()
    return ok({
        "assignment_batch_id": plan.id,
        "status": plan.status,
        "summary": {
            "total_items": len(items),
            "total_daily_tasks": len(tasks),
            "estimated_minutes_total": plan.total_estimated_minutes,
        },
        "uncertain_items": [
            {"id": item.id, "text": item.source_text, "reason": "数量或单位不明确", "suggestion": "按默认节奏安排"}
            for item in items if item.need_confirmation
        ],
    })


@router.get("/{plan_id}/draft")
def get_draft(plan_id: int, db: Session = Depends(get_db)):
    plan = db.get(AssignmentBatch, plan_id)
    items = db.query(AssignmentItem).filter(AssignmentItem.assignment_batch_id == plan_id).all()
    tasks = db.query(DailyTask).filter(DailyTask.assignment_batch_id == plan_id).order_by(DailyTask.task_date).limit(7).all()
    return ok({
        "plan": {"id": plan.id, "title": plan.title, "status": plan.status, "start_date": plan.start_date, "end_date": plan.end_date},
        "assignment_items": [
            {
                "id": i.id,
                "subject": i.subject,
                "title": i.title,
                "source_text": "" if i.import_file_id else i.source_text,
                "total_quantity": i.total_quantity,
                "unit": i.unit,
                "need_confirmation": i.need_confirmation,
                "source_file": _source_file_payload(db, i),
            }
            for i in items
        ],
        "daily_preview": [
            {"id": t.id, "task_date": t.task_date, "subject": t.subject, "title": t.title, "estimated_minutes": t.estimated_minutes}
            for t in tasks
        ],
        "uncertain_items": [{"id": i.id, "text": i.source_text, "suggestion": "接受系统推荐"} for i in items if i.need_confirmation],
    })


@router.post("/{plan_id}/confirm")
def confirm(plan_id: int, payload: PlanConfirmIn, db: Session = Depends(get_db)):
    plan = confirm_plan(db, plan_id, payload.adjustments)
    return ok({"plan_id": plan.id, "status": plan.status})


@router.get("/{plan_id}/calendar")
def calendar(plan_id: int, db: Session = Depends(get_db)):
    plan = db.get(AssignmentBatch, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    tasks = db.query(DailyTask).filter(DailyTask.assignment_batch_id == plan_id).order_by(DailyTask.task_date).all()
    tasks_by_date: dict = {}
    for task in tasks:
        tasks_by_date.setdefault(task.task_date, []).append(task)
    return ok({
        "plan": {"id": plan.id, "start_date": plan.start_date, "end_date": plan.end_date},
        "date_summary": [
            {
                "date": day,
                "total_tasks": len(day_tasks),
                "completed_tasks": len([task for task in day_tasks if task.status in COMPLETED_TASK_STATUSES]),
                "subjects": subject_summary(day_tasks),
            }
            for day, day_tasks in tasks_by_date.items()
        ],
        "items": [task_payload(db, task) for task in tasks],
    })


@router.post("/{plan_id}/rebalance")
def rebalance(plan_id: int, db: Session = Depends(get_db)):
    plan = db.get(AssignmentBatch, plan_id)
    plan.status = "rebalanced"
    db.commit()
    return ok({"plan_id": plan_id, "status": "rebalanced"})
