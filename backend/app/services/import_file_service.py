import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from backend.app.models import (
    AssignmentBatch,
    AssignmentItem,
    CorrectionResult,
    DailyTask,
    ImportBatch,
    ImportFile,
    StudySession,
    Submission,
    User,
)
from backend.app.services.answer_matching_service import match_batch_answers
from backend.app.services.answer_snapshot_service import (
    sync_pending_file_answer_snapshots,
)
from backend.app.services.import_access_service import (
    ImportAccessError,
    require_import_batch_access,
)
from backend.app.services.import_lock_service import (
    lock_import_batch_files,
    lock_student,
)
from backend.app.services.local_file_service import (
    is_remote_url,
    resolve_local_file,
    upload_root,
)
from backend.app.services.oss_service import (
    create_oss_delete_backup,
    delete_oss_url,
    discard_oss_delete_backup,
    restore_oss_delete_backup,
    validate_import_oss_url,
)


logger = logging.getLogger(__name__)


class StagedImportDeleteError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class LocalDeleteBackup:
    original: Path
    backup: Path


@dataclass
class StorageDeleteSnapshot:
    local_backups: list[LocalDeleteBackup]
    oss_backups: list[object]
    backup_dir: Path | None


def import_file_display_name(
    item: ImportFile,
    role_index: int,
    matched_homework_title: str | None = None,
) -> str:
    role = item.document_role or "homework"
    if role == "homework":
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
    batch = db.get(ImportBatch, batch_id)
    if not batch or batch.status == "confirmed":
        return False
    file_ids = [row.id for row in db.query(ImportFile.id).filter(
        ImportFile.import_batch_id == batch_id
    )]
    owning_plan_ids = [row.assignment_batch_id for row in db.query(
        AssignmentItem.assignment_batch_id
    ).filter(AssignmentItem.import_file_id.in_(file_ids))] if file_ids else []
    plans = db.query(AssignmentBatch).filter(or_(
        AssignmentBatch.import_batch_id == batch_id,
        AssignmentBatch.id.in_(owning_plan_ids),
    )).all()
    if any(plan.status != "pending_confirm" for plan in plans):
        return False
    plan_ids = [plan.id for plan in plans]
    if not plan_ids:
        return True
    items = db.query(AssignmentItem).filter(
        AssignmentItem.assignment_batch_id.in_(plan_ids)
    ).all()
    item_ids = [item.id for item in items]
    if not item_ids:
        return True
    tasks = db.query(DailyTask).filter(
        DailyTask.assignment_item_id.in_(item_ids)
    ).all()
    if any(task.status != "todo" for task in tasks):
        return False
    task_ids = [task.id for task in tasks]
    if not task_ids:
        return True
    return not (
        db.query(StudySession).filter(StudySession.daily_task_id.in_(task_ids)).first()
        or db.query(Submission).filter(Submission.daily_task_id.in_(task_ids)).first()
        or db.query(CorrectionResult).filter(
            CorrectionResult.daily_task_id.in_(task_ids)
        ).first()
    )


def _validated_storage_paths(item: ImportFile) -> list[Path]:
    batch_root = (upload_root() / "imports" / str(item.import_batch_id)).resolve()
    values: list[str] = []
    if item.storage_path:
        values.append(item.storage_path)
    if item.file_url and not is_remote_url(item.file_url):
        values.append(item.file_url)
    paths: list[Path] = []
    for value in values:
        path = resolve_local_file(value).resolve(strict=False)
        if not path.is_relative_to(batch_root):
            raise ValueError("Local path is outside import storage root")
        if path not in paths:
            paths.append(path)
    return paths


def _discard_storage_snapshot(snapshot: StorageDeleteSnapshot) -> list[str]:
    errors: list[str] = []
    for backup in snapshot.oss_backups:
        try:
            discard_oss_delete_backup(backup)
        except Exception as exc:
            errors.append(f"OSS backup cleanup failed: {exc}")
    for backup in snapshot.local_backups:
        try:
            backup.backup.unlink(missing_ok=True)
        except Exception as exc:
            errors.append(f"local backup cleanup failed: {exc}")
    if snapshot.backup_dir:
        for directory in (snapshot.backup_dir, snapshot.backup_dir.parent):
            try:
                directory.rmdir()
            except OSError:
                pass
    return errors


def _prepare_storage_snapshot(items: list[ImportFile]) -> StorageDeleteSnapshot:
    batch_id = items[0].import_batch_id
    batch_root = (upload_root() / "imports" / str(batch_id)).resolve()
    local_paths: list[Path] = []
    remote_items: list[ImportFile] = []
    for item in items:
        for path in _validated_storage_paths(item):
            if path not in local_paths:
                local_paths.append(path)
        if is_remote_url(item.file_url):
            validate_import_oss_url(item.file_url, item.import_batch_id)
            remote_items.append(item)

    existing_paths = [path for path in local_paths if path.exists()]
    backup_dir = None
    local_backups: list[LocalDeleteBackup] = []
    oss_backups: list[object] = []
    try:
        if existing_paths:
            backup_dir = batch_root / ".delete-backups" / uuid4().hex
            backup_dir.mkdir(parents=True, exist_ok=False)
            for index, path in enumerate(existing_paths):
                backup = backup_dir / f"{index}-{path.name}"
                shutil.copy2(path, backup)
                local_backups.append(LocalDeleteBackup(path, backup))
        for item in remote_items:
            backup = create_oss_delete_backup(item.file_url, item.import_batch_id)
            if backup is not None:
                oss_backups.append(backup)
    except Exception as exc:
        cleanup_errors = _discard_storage_snapshot(
            StorageDeleteSnapshot(local_backups, oss_backups, backup_dir)
        )
        if cleanup_errors:
            raise RuntimeError(
                f"{exc}; backup cleanup failed: {'; '.join(cleanup_errors)}"
            ) from exc
        raise
    return StorageDeleteSnapshot(local_backups, oss_backups, backup_dir)


def _delete_storage(snapshot: StorageDeleteSnapshot) -> None:
    for backup in snapshot.oss_backups:
        delete_oss_url(backup.url if hasattr(backup, "url") else backup)
    for backup in snapshot.local_backups:
        backup.original.unlink(missing_ok=True)


def _restore_storage_snapshot(snapshot: StorageDeleteSnapshot) -> list[str]:
    errors: list[str] = []
    for backup in snapshot.local_backups:
        try:
            backup.original.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup.backup, backup.original)
        except Exception as exc:
            errors.append(f"local restore failed: {exc}")
    for backup in snapshot.oss_backups:
        try:
            restore_oss_delete_backup(backup)
        except Exception as exc:
            errors.append(f"OSS restore failed: {exc}")
    return errors


def _raise_compensated_error(
    status_code: int,
    detail: str,
    original: Exception,
    snapshot: StorageDeleteSnapshot,
) -> None:
    restore_errors = _restore_storage_snapshot(snapshot)
    if not restore_errors:
        _discard_storage_snapshot(snapshot)
    combined = f"{detail}: {original}"
    if restore_errors:
        combined = f"{combined}; restoration failed: {'; '.join(restore_errors)}"
    raise StagedImportDeleteError(status_code, combined) from original


def _locked_rows_for_deletion(
    db: Session,
    batch_id: int,
    file_id: int,
):
    batch, files = lock_import_batch_files(db, batch_id)
    item = next((row for row in files if row.id == file_id), None)
    if not batch or not item:
        raise StagedImportDeleteError(404, "Import file not found")
    if not lock_student(db, batch.student_id):
        raise StagedImportDeleteError(404, "Import batch student not found")
    role = item.document_role or "homework"
    targets = [item]
    if role == "homework":
        paired = next((
            row for row in files
            if row.document_role == "answer"
            and row.matched_homework_file_id == item.id
        ), None)
        if paired:
            targets.append(paired)
    target_ids = [row.id for row in targets]
    actual_owner_plan_ids = select(AssignmentItem.assignment_batch_id).where(
        AssignmentItem.import_file_id.in_(target_ids)
    )
    locked_plans = list(db.scalars(
        select(AssignmentBatch)
        .where(or_(
            AssignmentBatch.import_batch_id == batch_id,
            AssignmentBatch.id.in_(actual_owner_plan_ids),
        ))
        .order_by(AssignmentBatch.id)
        .execution_options(populate_existing=True)
        .with_for_update()
    ))
    locked_assignment_items = list(db.scalars(
        select(AssignmentItem)
        .where(AssignmentItem.assignment_batch_id.in_([
            row.id for row in locked_plans
        ]))
        .order_by(AssignmentItem.id)
        .execution_options(populate_existing=True)
        .with_for_update()
    ))
    assignment_items = [
        row for row in locked_assignment_items if row.import_file_id in target_ids
    ]
    owning_plan_ids = {row.assignment_batch_id for row in assignment_items}
    plans = [
        row for row in locked_plans
        if row.import_batch_id == batch_id or row.id in owning_plan_ids
    ]
    if owning_plan_ids - {row.id for row in plans}:
        raise StagedImportDeleteError(409, "Import file ownership changed; retry deletion")
    assignment_item_ids = [row.id for row in assignment_items]
    tasks = list(db.scalars(
        select(DailyTask)
        .where(DailyTask.assignment_item_id.in_(assignment_item_ids))
        .order_by(DailyTask.id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )) if assignment_item_ids else []
    task_ids = [row.id for row in tasks]
    sessions = list(db.scalars(
        select(StudySession)
        .where(StudySession.daily_task_id.in_(task_ids))
        .order_by(StudySession.id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )) if task_ids else []
    submissions = list(db.scalars(
        select(Submission)
        .where(Submission.daily_task_id.in_(task_ids))
        .order_by(Submission.id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )) if task_ids else []
    corrections = list(db.scalars(
        select(CorrectionResult)
        .where(CorrectionResult.daily_task_id.in_(task_ids))
        .order_by(CorrectionResult.id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )) if task_ids else []
    return (
        batch,
        role,
        targets,
        plans,
        assignment_items,
        tasks,
        sessions,
        submissions,
        corrections,
        files,
        locked_assignment_items,
    )


def delete_staged_import_file(
    db: Session,
    user: User,
    file_id: int,
) -> list[int]:
    item = db.get(ImportFile, file_id)
    if not item:
        raise StagedImportDeleteError(404, "Import file not found")
    try:
        accessible_batch = require_import_batch_access(db, user, item.import_batch_id)
    except ImportAccessError as exc:
        raise StagedImportDeleteError(exc.status_code, exc.detail) from exc

    (
        batch,
        deleted_role,
        items,
        plans,
        assignment_items,
        tasks,
        sessions,
        submissions,
        corrections,
        batch_files,
        locked_assignment_items,
    ) = _locked_rows_for_deletion(db, accessible_batch.id, file_id)
    if batch.status == "confirmed":
        raise StagedImportDeleteError(409, "Confirmed import files cannot be deleted")
    if any(plan.status != "pending_confirm" for plan in plans):
        raise StagedImportDeleteError(409, "Active import files cannot be deleted")
    if any(task.status != "todo" for task in tasks) or sessions or submissions or corrections:
        raise StagedImportDeleteError(409, "Import file has study history")

    deleted_ids = [row.id for row in items]
    assignment_item_ids = [row.id for row in assignment_items]
    affected_plan_ids = sorted({
        row.assignment_batch_id for row in assignment_items
    })
    daily_task_ids = [row.id for row in tasks]
    try:
        snapshot = _prepare_storage_snapshot(items)
    except ValueError as exc:
        db.rollback()
        raise StagedImportDeleteError(409, str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise StagedImportDeleteError(
            502,
            f"Failed to back up staged file storage: {exc}",
        ) from exc

    try:
        _delete_storage(snapshot)
    except Exception as exc:
        db.rollback()
        _raise_compensated_error(
            502,
            "Failed to delete staged file storage",
            exc,
            snapshot,
        )

    try:
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
        for plan_id in affected_plan_ids:
            remaining_minutes = db.scalar(
                select(func.coalesce(func.sum(
                    AssignmentItem.estimated_minutes_total
                ), 0)).where(
                    AssignmentItem.assignment_batch_id == plan_id
                )
            )
            db.query(AssignmentBatch).filter(
                AssignmentBatch.id == plan_id
            ).update(
                {"total_estimated_minutes": int(remaining_minutes or 0)},
                synchronize_session=False,
            )
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
        remaining_locked_items = [
            row for row in locked_assignment_items
            if row.id not in assignment_item_ids
        ]
        if deleted_role == "answer":
            match_batch_answers(
                db,
                batch.id,
                commit=False,
                locked_plans=plans,
                locked_items=remaining_locked_items,
            )
        else:
            sync_pending_file_answer_snapshots(
                db,
                batch.id,
                [row for row in batch_files if row.id not in deleted_ids],
                locked_plans=plans,
                locked_items=remaining_locked_items,
            )
        db.commit()
    except Exception as exc:
        db.rollback()
        _raise_compensated_error(
            500,
            "Failed to update staged file database",
            exc,
            snapshot,
        )

    cleanup_errors = _discard_storage_snapshot(snapshot)
    if cleanup_errors:
        logger.warning(
            "Import storage backup cleanup failed after committed deletion",
            extra={
                "event": "import_backup_cleanup_failed",
                "batch_id": batch.id,
                "deleted_file_ids": deleted_ids,
                "cleanup_errors": cleanup_errors,
            },
        )
    return deleted_ids
