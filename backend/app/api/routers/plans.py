from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.api.deps import get_current_user
from backend.app.core.database import get_db
from backend.app.core.responses import ok
from backend.app.models import (
    AssignmentBatch,
    AssignmentItem,
    DailyTask,
    ImportFile,
    Student,
    User,
)
from backend.app.schemas.requests import PlanConfirmIn
from backend.app.services.access_service import can_access_student
from backend.app.services.import_access_service import (
    ImportAccessError,
    require_import_batch_access,
)
from backend.app.services.import_file_service import StagedImportDeleteError
from backend.app.services.planning_service import (
    PlanConfirmationBlocked,
    PlanStateConflict,
    confirm_plan,
    delete_staged_assignment_item,
    find_active_merge_target,
    generate_plan_from_import,
    plan_confirmation_blockers,
)
from backend.app.services.task_payload_service import COMPLETED_TASK_STATUSES, source_file_payload, subject_summary, task_payload

router = APIRouter(prefix="/plans", tags=["plans"])


def _source_file_payload(db: Session, item: AssignmentItem) -> dict | None:
    return source_file_payload(db, item)


def _plan_access(
    db: Session,
    user: User,
    plan_id: int,
) -> AssignmentBatch:
    plan = db.get(AssignmentBatch, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    student = db.get(Student, plan.student_id)
    if not student:
        raise HTTPException(status_code=404, detail="Plan student not found")
    if not can_access_student(db, user, student):
        raise HTTPException(status_code=403, detail="Plan access forbidden")
    return plan


def _draft_item_payload(
    db: Session,
    item: AssignmentItem,
    can_delete: bool,
) -> dict:
    answer_status = "not_uploaded"
    if item.import_file_id:
        answer = db.query(ImportFile).filter(
            ImportFile.document_role == "answer",
            ImportFile.matched_homework_file_id == item.import_file_id,
        ).first()
        if answer:
            answer_status = answer.match_status or "pending"
    return {
        "id": item.id,
        "subject": item.subject,
        "title": item.title,
        "source_text": "" if item.import_file_id else item.source_text,
        "total_quantity": item.total_quantity,
        "unit": item.unit,
        "need_confirmation": item.need_confirmation,
        "answer_status": answer_status,
        "can_delete": can_delete,
        "source_file": _source_file_payload(db, item),
    }


@router.post("/from-import/{batch_id}/generate")
def generate_from_import(
    batch_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        require_import_batch_access(db, user, batch_id)
        plan = generate_plan_from_import(db, batch_id)
    except ImportAccessError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
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
def get_draft(
    plan_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    plan = _plan_access(db, user, plan_id)
    new_items = db.query(AssignmentItem).filter(
        AssignmentItem.assignment_batch_id == plan_id,
    ).order_by(AssignmentItem.id).all()
    target = find_active_merge_target(db, plan)
    existing_items = db.query(AssignmentItem).filter(
        AssignmentItem.assignment_batch_id == target.id,
    ).order_by(AssignmentItem.id).all() if target else []
    tasks = db.query(DailyTask).filter(DailyTask.assignment_batch_id == plan_id).order_by(DailyTask.task_date).limit(7).all()
    blockers = plan_confirmation_blockers(db, plan)
    new_item_payloads = [
        _draft_item_payload(db, item, plan.status == "pending_confirm")
        for item in new_items
    ]
    return ok({
        "plan": {
            "id": plan.id,
            "title": plan.title,
            "status": plan.status,
            "start_date": plan.start_date,
            "end_date": plan.end_date,
            "target_assignment_batch_id": target.id if target else None,
        },
        "existing_items": [
            _draft_item_payload(db, item, False) for item in existing_items
        ],
        "new_items": new_item_payloads,
        "assignment_items": new_item_payloads,
        "daily_preview": [
            {"id": t.id, "task_date": t.task_date, "subject": t.subject, "title": t.title, "estimated_minutes": t.estimated_minutes}
            for t in tasks
        ],
        "uncertain_items": [{"id": i.id, "text": i.source_text, "suggestion": "接受系统推荐"} for i in new_items if i.need_confirmation],
        "confirmation_blockers": blockers,
        "can_confirm": plan.status == "pending_confirm" and not blockers,
    })


@router.post("/{plan_id}/confirm")
def confirm(
    plan_id: int,
    payload: PlanConfirmIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _plan_access(db, user, plan_id)
    try:
        plan = confirm_plan(db, plan_id, payload.adjustments)
    except PlanConfirmationBlocked as exc:
        raise HTTPException(status_code=409, detail=exc.blockers) from exc
    except PlanStateConflict as exc:
        raise HTTPException(status_code=409, detail=exc.detail) from exc
    return ok({"plan_id": plan.id, "status": plan.status})


@router.delete("/{plan_id}/draft-items/{item_id}")
def delete_draft_item(
    plan_id: int,
    item_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        deleted_ids = delete_staged_assignment_item(
            db,
            user,
            plan_id,
            item_id,
        )
    except StagedImportDeleteError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return ok({"deleted_file_ids": deleted_ids})


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
