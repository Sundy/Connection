from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from backend.app.api.deps import get_current_user
from backend.app.core.database import get_db
from backend.app.core.responses import ok
from backend.app.models import FamilyMember, ImportBatch, ImportFile, User
from backend.app.schemas.requests import ImportBatchCreateIn, ImportBatchUpdateIn
from backend.app.services.local_file_service import is_remote_url, resolve_local_file, upload_subdir
from backend.app.services.oss_service import build_import_object_key, signed_download_url, upload_file_to_oss
from backend.app.worker.tasks.parse_files import parse_import_file

router = APIRouter(prefix="/import-batches", tags=["imports"])


def _preview_url(file_id: int) -> str:
    return f"/api/v1/import-batches/files/{file_id}/preview"


@router.post("")
def create_import_batch(payload: ImportBatchCreateIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    member = db.query(FamilyMember).filter(FamilyMember.user_id == user.id).first()
    batch = ImportBatch(
        family_id=member.family_id,
        student_id=payload.student_id,
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
    original_file_name: str | None = Form(None),
    sort_order: int = Form(0),
    db: Session = Depends(get_db),
):
    upload_dir = upload_subdir("imports", str(batch_id))
    display_name = original_file_name or file.filename or "upload.bin"
    suffix = Path(display_name).suffix
    file_name = f"{uuid4().hex}{suffix}"
    path = upload_dir / file_name
    content = await file.read()
    path.write_bytes(content)
    storage_path = str(path.resolve())
    object_key = build_import_object_key(batch_id, display_name, suffix)
    oss_url = upload_file_to_oss(storage_path, object_key)
    import_file = ImportFile(
        import_batch_id=batch_id,
        file_name=display_name,
        file_type=file_type,
        file_url=oss_url or storage_path,
        storage_path=storage_path,
        file_size=len(content),
        sort_order=sort_order,
    )
    db.add(import_file)
    batch = db.get(ImportBatch, batch_id)
    if batch:
        batch.status = "uploaded"
    db.commit()
    db.refresh(import_file)
    return ok({
        "file_id": import_file.id,
        "file_name": import_file.file_name,
        "file_type": import_file.file_type,
        "file_url": import_file.file_url,
        "preview_url": _preview_url(import_file.id),
        "parse_status": import_file.parse_status,
    })


@router.get("/files/{file_id}/preview")
def preview_import_file(file_id: int, db: Session = Depends(get_db)):
    item = db.get(ImportFile, file_id)
    if not item:
        raise HTTPException(status_code=404, detail="Import file not found")
    if is_remote_url(item.file_url):
        return RedirectResponse(signed_download_url(item.file_url))
    path = resolve_local_file(item.storage_path or item.file_url)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Import file content not found")
    return FileResponse(path, filename=item.file_name)


@router.post("/{batch_id}/parse")
def parse_batch(batch_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    batch = db.get(ImportBatch, batch_id)
    if batch:
        batch.status = "parsing"
        db.commit()
    files = db.query(ImportFile).filter(ImportFile.import_batch_id == batch_id).all()
    for item in files:
        background_tasks.add_task(parse_import_file.delay, item.id)
    if not files and batch:
        batch.merged_text = batch.raw_text
        batch.status = "parsed"
        db.commit()
    return ok({"batch_id": batch_id, "status": "parsing" if files else "parsed"})


@router.patch("/{batch_id}")
def update_import_batch(batch_id: int, payload: ImportBatchUpdateIn, db: Session = Depends(get_db)):
    batch = db.get(ImportBatch, batch_id)
    if payload.raw_text is not None:
        text = payload.raw_text.strip()
        batch.raw_text = text or None
    db.commit()
    db.refresh(batch)
    return ok({"id": batch.id, "raw_text": batch.raw_text})


@router.get("/{batch_id}")
def get_import_batch(batch_id: int, db: Session = Depends(get_db)):
    batch = db.get(ImportBatch, batch_id)
    files = db.query(ImportFile).filter(ImportFile.import_batch_id == batch_id).all()
    parsed_count = len([f for f in files if f.parse_status == "success"])
    if files and parsed_count == len(files) and batch and batch.status == "parsing":
        batch.status = "parsed"
        batch.merged_text = "\n".join([batch.raw_text or "", *[f.extracted_text or "" for f in files]]).strip()
        db.commit()
    return ok({
        "id": batch.id,
        "title": batch.title,
        "status": batch.status,
        "file_count": len(files),
        "parsed_file_count": parsed_count,
        "merged_text": batch.merged_text,
    })


@router.get("/{batch_id}/files")
def list_import_files(batch_id: int, db: Session = Depends(get_db)):
    files = db.query(ImportFile).filter(ImportFile.import_batch_id == batch_id).order_by(ImportFile.sort_order).all()
    return ok([{
        "id": item.id,
        "file_name": item.file_name,
        "file_type": item.file_type,
        "file_url": signed_download_url(item.file_url),
        "preview_url": _preview_url(item.id),
        "parse_status": item.parse_status,
        "sort_order": item.sort_order,
    } for item in files])
