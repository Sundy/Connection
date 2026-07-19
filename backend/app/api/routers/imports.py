from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from backend.app.api.deps import get_current_user
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
from backend.app.services.local_file_service import is_remote_url, resolve_local_file, upload_subdir
from backend.app.services.oss_service import build_import_object_key, signed_download_url, upload_file_to_oss
from backend.app.worker.tasks.parse_files import parse_import_file

router = APIRouter(prefix="/import-batches", tags=["imports"])


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
        if item.parse_status in {"pending", "processing"}:
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
        elif item.recognition_status in {"pending", "processing", None}:
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


@router.post("/{batch_id}/files")
async def upload_import_file(
    batch_id: int,
    file: UploadFile = File(...),
    file_type: str = Form("image"),
    document_role: Literal["homework", "answer"] = Form("homework"),
    original_file_name: str | None = Form(None),
    sort_order: int = Form(0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    batch = _batch_access(db, user, batch_id)
    upload_dir = upload_subdir("imports", str(batch_id))
    original_name = original_file_name or file.filename or "upload.bin"
    suffix = Path(original_name).suffix
    file_name = f"{uuid4().hex}{suffix}"
    path = upload_dir / file_name
    content = await file.read()
    path.write_bytes(content)
    storage_path = str(path.resolve())
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
    db.commit()
    db.refresh(import_file)
    role_index = db.query(ImportFile).filter(
        ImportFile.import_batch_id == batch_id,
        ImportFile.document_role == document_role,
    ).count()
    payload = import_file_payload(import_file, role_index)
    payload["can_delete"] = import_batch_allows_staged_deletion(db, batch.id)
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
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return ok({"deleted_file_ids": deleted_ids})


@router.post("/{batch_id}/parse")
def parse_batch(
    batch_id: int,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    batch = _batch_access(db, user, batch_id)
    files = db.query(ImportFile).filter(
        ImportFile.import_batch_id == batch_id
    ).all()
    candidates = [
        item for item in files
        if item.parse_status in {"pending", "failed"}
        or item.recognition_status in {"pending", "failed"}
    ]
    for item in candidates:
        background_tasks.add_task(parse_import_file.delay, item.id)
    if candidates:
        batch.status = "parsing"
    elif not files:
        batch.merged_text = batch.raw_text
        batch.status = "parsed"
    elif any(
        item.parse_status == "processing" or item.recognition_status == "processing"
        for item in files
    ):
        batch.status = "parsing"
    else:
        batch.status = "parsed"
    db.commit()
    return ok({"batch_id": batch_id, "status": batch.status})


@router.patch("/{batch_id}")
def update_import_batch(
    batch_id: int,
    payload: ImportBatchUpdateIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    batch = _batch_access(db, user, batch_id)
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
        item.parse_status in {"pending", "processing"}
        or item.recognition_status in {"pending", "processing", None}
        for item in files
    ):
        batch.status = "parsed"
        batch.merged_text = "\n".join([
            batch.raw_text or "",
            *[item.extracted_text or "" for item in files],
        ]).strip()
        db.commit()
    blockers = _blockers(files, batch.raw_text)
    return ok({
        "id": batch.id,
        "title": batch.title,
        "status": batch.status,
        "file_count": len(files),
        "parsed_file_count": parsed_count,
        "merged_text": batch.merged_text,
        "can_generate": not blockers,
        "blockers": blockers,
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
