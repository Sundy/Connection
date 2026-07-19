from datetime import UTC, datetime, timedelta
import logging
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session

from backend.app.api.deps import get_current_user
from backend.app.core.config import settings
from backend.app.core.database import get_db
from backend.app.core.responses import ok
from backend.app.models import ImportBatch, ImportFile, Student, User
from backend.app.schemas.requests import ImportBatchCreateIn, ImportBatchUpdateIn
from backend.app.services.access_service import can_access_student
from backend.app.services.import_access_service import (
    ImportAccessError,
    require_import_batch_access,
)
from backend.app.services.import_file_service import (
    StagedImportDeleteError,
    delete_staged_import_file,
    import_batch_allows_staged_deletion,
    import_file_payload,
)
from backend.app.services.import_lock_service import lock_import_batch_files
from backend.app.services.import_state_service import (
    ImportBatchImmutableError,
    import_batch_read_state,
    lock_mutable_import_batch,
)
from backend.app.services.local_file_service import is_remote_url, resolve_local_file, upload_subdir
from backend.app.services.oss_service import (
    build_import_object_key,
    delete_oss_url,
    signed_download_url,
    upload_file_to_oss,
)
from backend.app.worker.tasks.parse_files import parse_import_file

router = APIRouter(prefix="/import-batches", tags=["imports"])
logger = logging.getLogger(__name__)


def _preview_url(file_id: int) -> str:
    return f"/api/v1/import-batches/files/{file_id}/preview"


def _batch_access(db: Session, user: User, batch_id: int) -> ImportBatch:
    try:
        return require_import_batch_access(db, user, batch_id)
    except ImportAccessError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _file_access(db: Session, user: User, file_id: int) -> ImportFile:
    item = db.get(ImportFile, file_id)
    if not item:
        raise HTTPException(status_code=404, detail="Import file not found")
    _batch_access(db, user, item.import_batch_id)
    return item


def _payloads(db: Session, batch: ImportBatch, files: list[ImportFile]) -> list[dict]:
    role_indexes = {"homework": 0, "answer": 0}
    can_delete = import_batch_allows_staged_deletion(db, batch.id)
    payloads: list[dict] = []
    for item in files:
        role = item.document_role or "homework"
        role_indexes[role] = role_indexes.get(role, 0) + 1
        matched_title = None
        if item.match_status == "matched" and item.matched_homework_file_id:
            homework = db.get(ImportFile, item.matched_homework_file_id)
            matched_title = homework.recognized_title if homework else None
        payload = import_file_payload(item, role_indexes[role], matched_title)
        payload["file_url"] = signed_download_url(item.file_url)
        payload["can_delete"] = can_delete
        payloads.append(payload)
    return payloads


def _blockers(files: list[ImportFile], raw_text: str | None) -> list[dict]:
    blockers: list[dict] = []
    for item in files:
        role = item.document_role or "homework"
        if item.parse_status == "failed":
            blockers.append({
                "code": "parse_failed",
                "file_id": item.id,
                "document_role": role,
                "message": "文件解析失败",
            })
            continue
        if item.parse_status in {"", "pending", "queued", "processing", None}:
            blockers.append({
                "code": "parse_pending",
                "file_id": item.id,
                "document_role": role,
                "message": "文件正在解析",
            })
            continue
        if item.recognition_status == "failed":
            blockers.append({
                "code": "recognition_failed",
                "file_id": item.id,
                "document_role": role,
                "message": "作业内容无法识别" if role == "homework" else "答案内容无法识别",
            })
        elif item.recognition_status in {"", "pending", "queued", "processing", None}:
            blockers.append({
                "code": "recognition_pending",
                "file_id": item.id,
                "document_role": role,
                "message": "内容正在识别",
            })
        elif role == "answer" and item.match_status != "matched":
            blockers.append({
                "code": "answer_unmatched" if item.match_status == "unmatched" else "answer_match_pending",
                "file_id": item.id,
                "document_role": role,
                "message": "答案未匹配到作业" if item.match_status == "unmatched" else "答案正在匹配",
            })
    if not files and not (raw_text or "").strip():
        blockers.append({
            "code": "no_import_content",
            "file_id": None,
            "document_role": None,
            "message": "请先上传作业或填写作业内容",
        })
    return blockers


@router.post("")
def create_import_batch(
    payload: ImportBatchCreateIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    student = db.get(Student, payload.student_id)
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    if not can_access_student(db, user, student):
        raise HTTPException(status_code=403, detail="Student access forbidden")
    batch = ImportBatch(
        family_id=student.family_id,
        student_id=student.id,
        title=payload.title,
        period_type=payload.period_type,
        start_date=payload.start_date,
        end_date=payload.end_date,
        raw_text=payload.raw_text,
        status="draft",
        created_by=user.id,
        source_type="mixed",
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)
    return ok({"id": batch.id, "status": batch.status})


def _commit_import_upload(db: Session) -> None:
    db.commit()


@router.post("/{batch_id}/files")
def upload_import_file(
    batch_id: int,
    file: UploadFile = File(...),
    file_type: str = Form("image"),
    document_role: Literal["homework", "answer"] = Form("homework"),
    original_file_name: str | None = Form(None),
    sort_order: int = Form(0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _batch_access(db, user, batch_id)
    try:
        batch, _files, _plans = lock_mutable_import_batch(db, batch_id)
    except ImportBatchImmutableError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=exc.detail) from exc
    if not batch:
        raise HTTPException(status_code=404, detail="Import batch not found")
    upload_dir = upload_subdir("imports", str(batch_id))
    original_name = original_file_name or file.filename or "upload.bin"
    suffix = Path(original_name).suffix
    file_name = f"{uuid4().hex}{suffix}"
    path = upload_dir / file_name
    content = file.file.read()
    storage_path = str(path.resolve())
    oss_url = ""
    try:
        path.write_bytes(content)
        object_key = build_import_object_key(batch_id, original_name, suffix)
        oss_url = upload_file_to_oss(storage_path, object_key)
        import_file = ImportFile(
            import_batch_id=batch_id,
            file_name=original_name,
            file_type=file_type,
            file_url=oss_url or storage_path,
            storage_path=storage_path,
            file_size=len(content),
            sort_order=sort_order,
            document_role=document_role,
            recognition_status="pending",
        )
        db.add(import_file)
        batch.status = "uploaded"
        db.flush()
        role_filter = ImportFile.document_role == document_role
        if document_role == "homework":
            role_filter = or_(role_filter, ImportFile.document_role.is_(None))
        role_index = db.query(ImportFile).filter(
            ImportFile.import_batch_id == batch_id,
            role_filter,
        ).count()
        payload = import_file_payload(import_file, role_index)
        payload["can_delete"] = import_batch_allows_staged_deletion(db, batch.id)
        _commit_import_upload(db)
    except Exception as exc:
        db.rollback()
        cleanup_errors: list[str] = []
        if oss_url:
            try:
                delete_oss_url(oss_url)
            except Exception as cleanup_exc:
                cleanup_errors.append(f"OSS cleanup failed: {cleanup_exc}")
        try:
            resolved_path = path.resolve(strict=False)
            if resolved_path.is_relative_to(upload_dir.resolve()):
                resolved_path.unlink(missing_ok=True)
            else:
                cleanup_errors.append("local cleanup refused unsafe path")
        except Exception as cleanup_exc:
            cleanup_errors.append(f"local cleanup failed: {cleanup_exc}")
        detail = "Failed to save import upload"
        if cleanup_errors:
            detail = f"{detail}; {'; '.join(cleanup_errors)}"
        raise HTTPException(status_code=500, detail=detail) from exc
    return ok(payload)


@router.get("/files/{file_id}/preview")
def preview_import_file(
    file_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    item = _file_access(db, user, file_id)
    if is_remote_url(item.file_url):
        return RedirectResponse(signed_download_url(item.file_url))
    path = resolve_local_file(item.storage_path or item.file_url)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Import file content not found")
    return FileResponse(path, filename=item.file_name)


@router.delete("/files/{file_id}")
def delete_import_file(
    file_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        deleted_ids = delete_staged_import_file(db, user, file_id)
    except StagedImportDeleteError as exc:
        db.rollback()
        detail = exc.detail
        if exc.status_code >= 500:
            logger.exception(
                "Staged import file deletion failed",
                extra={"event": "staged_import_delete_failed", "file_id": file_id},
            )
            detail = {
                "code": "import_storage_backup_failed",
                "message": "暂时无法删除文件，请稍后重试",
            }
        raise HTTPException(status_code=exc.status_code, detail=detail) from exc
    return ok({"deleted_file_ids": deleted_ids})


def _parse_in_progress(item: ImportFile) -> bool:
    active_states = {None, "", "pending", "queued", "processing"}
    return (
        item.parse_status in active_states
        or item.recognition_status in active_states
    )


def _claim_parse_files(
    db: Session,
    batch_id: int,
) -> tuple[ImportBatch, list[tuple[int, str]]]:
    batch, files, _plans = lock_mutable_import_batch(db, batch_id)
    if not batch:
        raise ValueError("Import batch not found")
    now = datetime.now(UTC).replace(tzinfo=None)
    lease_cutoff = now - timedelta(seconds=settings.import_parse_lease_seconds)
    retryable_states = {None, "", "pending", "failed"}
    leased_states = {"queued", "processing"}
    claimed: list[tuple[int, str]] = []
    for item in files:
        is_retryable = (
            item.parse_status in retryable_states
            or item.recognition_status in retryable_states
        )
        is_stale = (
            item.parse_status in leased_states
            or item.recognition_status in leased_states
        ) and (item.updated_at is None or item.updated_at < lease_cutoff)
        if not is_retryable and not is_stale:
            continue
        item.parse_status = "queued"
        item.parse_error = None
        item.recognition_status = "queued"
        item.recognition_error = None
        item.parse_claim_token = uuid4().hex
        item.updated_at = now
        claimed.append((item.id, item.parse_claim_token))
    if claimed:
        batch.status = "parsing"
    elif not files:
        batch.merged_text = batch.raw_text
        batch.status = "parsed"
    elif any(_parse_in_progress(item) for item in files):
        batch.status = "parsing"
    else:
        batch.status = "parsed"
    db.commit()
    return batch, claimed


def _release_parse_claims(
    db: Session,
    batch_id: int,
    claims: list[tuple[int, str]],
    error: str,
) -> None:
    batch, files = lock_import_batch_files(db, batch_id)
    released = dict(claims)
    for item in files:
        if item.parse_claim_token == released.get(item.id) and (
            item.parse_status == "queued"
            or item.recognition_status == "queued"
        ):
            item.parse_status = "failed"
            item.parse_error = error
            item.recognition_status = "failed"
            item.recognition_error = error
            item.parse_claim_token = None
    batch.status = (
        "parsing" if any(_parse_in_progress(item) for item in files) else "parsed"
    )
    db.commit()


@router.post("/{batch_id}/parse")
def parse_batch(
    batch_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _batch_access(db, user, batch_id)
    try:
        batch, claimed_ids = _claim_parse_files(db, batch_id)
    except ImportBatchImmutableError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=exc.detail) from exc
    for index, (file_id, claim_token) in enumerate(claimed_ids):
        try:
            parse_import_file.delay(file_id, claim_token)
        except Exception as exc:
            error = f"Parse dispatch failed: {exc}"
            _release_parse_claims(db, batch_id, claimed_ids[index:], error)
            raise HTTPException(
                status_code=503,
                detail="文件解析任务暂时无法提交，请稍后重试",
            ) from exc
    return ok({"batch_id": batch_id, "status": batch.status})


@router.patch("/{batch_id}")
def update_import_batch(
    batch_id: int,
    payload: ImportBatchUpdateIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _batch_access(db, user, batch_id)
    try:
        batch, _files, _plans = lock_mutable_import_batch(db, batch_id)
    except ImportBatchImmutableError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=exc.detail) from exc
    if not batch:
        raise HTTPException(status_code=404, detail="Import batch not found")
    if payload.raw_text is not None:
        text = payload.raw_text.strip()
        batch.raw_text = text or None
    db.commit()
    db.refresh(batch)
    return ok({"id": batch.id, "raw_text": batch.raw_text})


@router.get("/{batch_id}")
def get_import_batch(
    batch_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    batch = _batch_access(db, user, batch_id)
    files = db.query(ImportFile).filter(
        ImportFile.import_batch_id == batch_id
    ).order_by(ImportFile.sort_order, ImportFile.id).all()
    parsed_count = len([item for item in files if item.parse_status == "success"])
    if batch.status == "parsing" and not any(
        item.parse_status in {"", "pending", "queued", "processing", None}
        or item.recognition_status in {"", "pending", "queued", "processing", None}
        for item in files
    ):
        batch.status = "parsed"
        batch.merged_text = "\n".join([
            batch.raw_text or "",
            *[item.extracted_text or "" for item in files],
        ]).strip()
        db.commit()
    blockers = _blockers(files, batch.raw_text)
    can_edit, canonical_plan_id = import_batch_read_state(db, batch)
    return ok({
        "id": batch.id,
        "title": batch.title,
        "status": batch.status,
        "file_count": len(files),
        "parsed_file_count": parsed_count,
        "merged_text": batch.merged_text,
        "can_generate": not blockers,
        "blockers": blockers,
        "can_edit": can_edit,
        "read_only": not can_edit,
        "canonical_plan_id": canonical_plan_id,
        "files": _payloads(db, batch, files),
    })


@router.get("/{batch_id}/files")
def list_import_files(
    batch_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    batch = _batch_access(db, user, batch_id)
    files = db.query(ImportFile).filter(
        ImportFile.import_batch_id == batch_id
    ).order_by(ImportFile.sort_order, ImportFile.id).all()
    return ok(_payloads(db, batch, files))
