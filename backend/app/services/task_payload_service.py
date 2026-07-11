from sqlalchemy.orm import Session

from backend.app.models import AssignmentItem, DailyTask, ImportFile
from backend.app.services.oss_service import signed_download_url


COMPLETED_TASK_STATUSES = {"corrected", "needs_review"}


def subject_summary(tasks: list[DailyTask]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for task in tasks:
        row = grouped.setdefault(task.subject, {
            "subject": task.subject,
            "total_tasks": 0,
            "completed_tasks": 0,
        })
        row["total_tasks"] += 1
        row["completed_tasks"] += int(task.status in COMPLETED_TASK_STATUSES)
    return list(grouped.values())


def source_file_payload(db: Session, item: AssignmentItem | None) -> dict | None:
    if not item or not item.import_file_id:
        return None
    source = db.get(ImportFile, item.import_file_id)
    if not source:
        return None
    return {
        "id": source.id,
        "file_name": source.file_name,
        "file_type": source.file_type,
        "file_url": signed_download_url(source.file_url),
        "preview_url": f"/api/v1/import-batches/files/{source.id}/preview",
    }


def task_payload(db: Session, task: DailyTask) -> dict:
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
        "source_text": "" if item and item.import_file_id else (item.source_text if item else ""),
        "source_file": source_file_payload(db, item),
        "has_answer": bool(answer_text and answer_text.strip()),
    }
