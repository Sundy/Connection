from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from backend.app.api.deps import get_current_user
from backend.app.core.config import settings
from backend.app.core.database import get_db
from backend.app.core.responses import ok
from backend.app.models import FamilyMember, ImportBatch, ImportFile, User
from backend.app.schemas.requests import ImportBatchCreateIn
from backend.app.worker.tasks.parse_files import parse_import_file

router = APIRouter(prefix="/import-batches", tags=["imports"])


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
    sort_order: int = Form(0),
    db: Session = Depends(get_db),
):
    upload_dir = Path(settings.upload_dir) / "imports" / str(batch_id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "upload.bin").suffix
    file_name = f"{uuid4().hex}{suffix}"
    path = upload_dir / file_name
    content = await file.read()
    path.write_bytes(content)
    import_file = ImportFile(
        import_batch_id=batch_id,
        file_name=file.filename or file_name,
        file_type=file_type,
        file_url=str(path),
        file_size=len(content),
        sort_order=sort_order,
    )
    db.add(import_file)
    batch = db.get(ImportBatch, batch_id)
    if batch:
        batch.status = "uploaded"
    db.commit()
    db.refresh(import_file)
    return ok({"file_id": import_file.id, "file_url": import_file.file_url, "parse_status": import_file.parse_status})


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
        "file_url": item.file_url,
        "parse_status": item.parse_status,
        "sort_order": item.sort_order,
    } for item in files])
