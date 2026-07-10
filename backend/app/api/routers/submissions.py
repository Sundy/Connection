from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.core.responses import ok
from backend.app.models import DailyTask, Submission, SubmissionMedia
from backend.app.schemas.requests import SubmissionCreateIn, SubmissionUpdateIn
from backend.app.services.local_file_service import upload_subdir
from backend.app.services.oss_service import build_submission_object_key, upload_file_to_oss
from backend.app.services.study_service import finish_session
from backend.app.worker.tasks.correct_homework import run_homework_correction

router = APIRouter(prefix="/submissions", tags=["submissions"])


@router.post("")
def create_submission(payload: SubmissionCreateIn, db: Session = Depends(get_db)):
    task = db.get(DailyTask, payload.daily_task_id)
    submission = Submission(
        daily_task_id=payload.daily_task_id,
        student_id=task.student_id,
        submission_type=payload.submission_type,
        linked_study_session_id=payload.linked_study_session_id,
        student_note=payload.student_note,
        answer_text=payload.answer_text.strip() if payload.answer_text and payload.answer_text.strip() else None,
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)
    return ok({"submission_id": submission.id, "status": submission.status})


@router.post("/{submission_id}/media")
async def upload_media(
    submission_id: int,
    file: UploadFile = File(...),
    media_type: str = Form("image"),
    purpose: str = Form("homework"),
    sort_order: int = Form(0),
    db: Session = Depends(get_db),
):
    upload_dir = upload_subdir("submissions", str(submission_id))
    suffix = Path(file.filename or "media.bin").suffix
    file_name = f"{uuid4().hex}{suffix}"
    path = upload_dir / file_name
    content = await file.read()
    path.write_bytes(content)
    storage_path = str(path.resolve())
    object_key = build_submission_object_key(submission_id, purpose, file.filename or file_name, suffix)
    oss_url = upload_file_to_oss(storage_path, object_key)
    media = SubmissionMedia(
        submission_id=submission_id,
        media_type=media_type,
        purpose=purpose,
        file_url=oss_url or storage_path,
        storage_path=storage_path,
        sort_order=sort_order,
    )
    db.add(media)
    submission = db.get(Submission, submission_id)
    submission.status = "uploaded"
    db.commit()
    db.refresh(media)
    return ok({
        "media_id": media.id,
        "media_type": media.media_type,
        "purpose": media.purpose,
        "file_url": media.file_url,
        "process_status": media.process_status,
    })


@router.post("/{submission_id}/complete")
def complete(submission_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    submission = db.get(Submission, submission_id)
    submission.status = "processing"
    task = db.get(DailyTask, submission.daily_task_id)
    task.status = "correcting"
    if submission.linked_study_session_id:
        finish_session(db, submission.linked_study_session_id)
    db.commit()
    background_tasks.add_task(run_homework_correction.delay, submission.id)
    return ok({"submission_id": submission.id, "status": "processing", "daily_task_status": "correcting"})


@router.get("/{submission_id}")
def get_submission(submission_id: int, db: Session = Depends(get_db)):
    submission = db.get(Submission, submission_id)
    has_answer_media = db.query(SubmissionMedia).filter(
        SubmissionMedia.submission_id == submission_id,
        SubmissionMedia.purpose == "answer",
    ).first() is not None
    return ok({
        "id": submission.id,
        "daily_task_id": submission.daily_task_id,
        "status": submission.status,
        "has_answer": bool(submission.answer_text and submission.answer_text.strip()) or has_answer_media,
    })


@router.patch("/{submission_id}")
def update_submission(submission_id: int, payload: SubmissionUpdateIn, db: Session = Depends(get_db)):
    submission = db.get(Submission, submission_id)
    if payload.answer_text is not None:
        text = payload.answer_text.strip()
        submission.answer_text = text or None
    db.commit()
    db.refresh(submission)
    return ok({
        "id": submission.id,
        "has_answer": bool(submission.answer_text and submission.answer_text.strip()),
    })
