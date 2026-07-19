import re
from pathlib import Path

from sqlalchemy.orm import Session

from backend.app.models import (
    AssignmentBatch,
    AssignmentItem,
    CorrectionResult,
    DailyTask,
    ImportFile,
    StudySession,
    Submission,
    User,
)
from backend.app.services.answer_matching_service import match_batch_answers
from backend.app.services.import_access_service import (
    ImportAccessError,
    require_import_batch_access,
)
from backend.app.services.local_file_service import is_remote_url, resolve_local_file
from backend.app.services.oss_service import delete_oss_url


class StagedImportDeleteError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def import_file_display_name(
    item: ImportFile,
    role_index: int,
    matched_homework_title: str | None = None,
) -> str:
    if item.document_role == "homework":
        return item.recognized_title or (
            "作业内容无法识别"
            if item.recognition_status == "failed"
            else f"正在识别第 {role_index} 份作业"
        )
    if item.match_status == "matched":
        assert item.matched_homework_file_id is not None
        assert matched_homework_title and matched_homework_title.strip()
        assert re.search(r"[\u4e00-\u9fff]", matched_homework_title)
        return f"《{matched_homework_title.strip()}》答案"
    return (
        "未匹配答案"
        if item.match_status == "unmatched"
        else f"正在识别第 {role_index} 份答案"
    )


def import_file_payload(
    item: ImportFile,
    role_index: int,
    matched_homework_title: str | None = None,
) -> dict:
    return {
        "id": item.id,
        "file_id": item.id,
        "document_role": item.document_role or "homework",
        "display_name": import_file_display_name(
            item,
            role_index,
            matched_homework_title,
        ),
        "original_file_name": item.file_name,
        "file_type": item.file_type,
        "file_url": item.file_url,
        "preview_url": f"/api/v1/import-batches/files/{item.id}/preview",
        "parse_status": item.parse_status,
        "parse_error": item.parse_error,
        "recognition_status": item.recognition_status,
        "recognition_error": item.recognition_error,
        "recognized_title": item.recognized_title,
        "content_summary": item.content_summary,
        "match_status": item.match_status,
        "matched_homework_file_id": item.matched_homework_file_id,
        "match_confidence": item.match_confidence,
        "match_reason": item.match_reason,
        "sort_order": item.sort_order,
        "can_delete": True,
    }


def import_batch_allows_staged_deletion(db: Session, batch_id: int) -> bool:
    from backend.app.models import ImportBatch

    batch = db.get(ImportBatch, batch_id)
    if not batch or batch.status == "confirmed":
        return False
    return not db.query(AssignmentBatch).filter(
        AssignmentBatch.import_batch_id == batch_id,
        AssignmentBatch.status != "pending_confirm",
    ).first()


def _storage_paths(item: ImportFile) -> list[Path]:
    values: list[str] = []
    if item.storage_path:
        values.append(item.storage_path)
    if item.file_url and not is_remote_url(item.file_url):
        values.append(item.file_url)
    paths: list[Path] = []
    for value in values:
        path = resolve_local_file(value)
        if path not in paths:
            paths.append(path)
    return paths


def _delete_storage(items: list[ImportFile]) -> None:
    try:
        for item in items:
            delete_oss_url(item.file_url)
        paths: list[Path] = []
        for item in items:
            for path in _storage_paths(item):
                if path not in paths:
                    paths.append(path)
        for path in paths:
            path.unlink(missing_ok=True)
    except Exception as exc:
        raise StagedImportDeleteError(
            502,
            "Failed to delete staged file storage",
        ) from exc


def delete_staged_import_file(
    db: Session,
    user: User,
    file_id: int,
) -> list[int]:
    item = db.get(ImportFile, file_id)
    if not item:
        raise StagedImportDeleteError(404, "Import file not found")
    try:
        batch = require_import_batch_access(db, user, item.import_batch_id)
    except ImportAccessError as exc:
        raise StagedImportDeleteError(exc.status_code, exc.detail) from exc

    if batch.status == "confirmed":
        raise StagedImportDeleteError(409, "Confirmed import files cannot be deleted")
    plans = db.query(AssignmentBatch).filter(
        AssignmentBatch.import_batch_id == batch.id
    ).all()
    if any(plan.status != "pending_confirm" for plan in plans):
        raise StagedImportDeleteError(409, "Active import files cannot be deleted")

    items = [item]
    if item.document_role == "homework":
        paired_answer = db.query(ImportFile).filter(
            ImportFile.import_batch_id == batch.id,
            ImportFile.document_role == "answer",
            ImportFile.matched_homework_file_id == item.id,
        ).first()
        if paired_answer:
            items.append(paired_answer)
    deleted_ids = [row.id for row in items]
    deleted_role = item.document_role or "homework"

    assignment_item_ids = [
        row.id
        for row in db.query(AssignmentItem).filter(
            AssignmentItem.import_file_id.in_(deleted_ids)
        )
    ]
    daily_task_ids = [
        row.id
        for row in db.query(DailyTask).filter(
            DailyTask.assignment_item_id.in_(assignment_item_ids)
        )
    ] if assignment_item_ids else []
    has_progressed_task = bool(daily_task_ids and db.query(DailyTask).filter(
        DailyTask.id.in_(daily_task_ids),
        DailyTask.status != "todo",
    ).first())
    has_study_history = bool(daily_task_ids and (
        db.query(Submission).filter(Submission.daily_task_id.in_(daily_task_ids)).first()
        or db.query(CorrectionResult).filter(
            CorrectionResult.daily_task_id.in_(daily_task_ids)
        ).first()
        or db.query(StudySession).filter(
            StudySession.daily_task_id.in_(daily_task_ids)
        ).first()
    ))
    if has_progressed_task or has_study_history:
        raise StagedImportDeleteError(409, "Import file has study history")

    _delete_storage(items)

    if daily_task_ids:
        db.query(DailyTask).filter(DailyTask.id.in_(daily_task_ids)).delete(
            synchronize_session=False
        )
        db.flush()
    if assignment_item_ids:
        db.query(AssignmentItem).filter(
            AssignmentItem.id.in_(assignment_item_ids)
        ).delete(synchronize_session=False)
        db.flush()
    db.query(ImportFile).filter(ImportFile.id.in_(deleted_ids)).update(
        {"matched_homework_file_id": None},
        synchronize_session=False,
    )
    db.flush()
    db.query(ImportFile).filter(ImportFile.id.in_(deleted_ids)).delete(
        synchronize_session=False
    )
    db.flush()

    if deleted_role == "answer" and db.query(ImportFile).filter(
        ImportFile.import_batch_id == batch.id,
        ImportFile.document_role == "answer",
    ).first():
        match_batch_answers(db, batch.id)
    else:
        db.commit()
    return deleted_ids
