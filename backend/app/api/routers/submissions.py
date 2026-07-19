from pathlib import Path
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from backend.app.api.deps import get_current_user
from backend.app.core.database import get_db
from backend.app.core.responses import ok
from backend.app.models import DailyTask, Student, StudySession, Submission, SubmissionMedia, User
from backend.app.schemas.requests import SubmissionCreateIn
from backend.app.services.access_service import can_access_student
from backend.app.services.local_file_service import local_path_for_submission_media, upload_subdir
from backend.app.services.notification_service import notify_submission_uploaded
from backend.app.services.oss_service import build_submission_object_key, signed_download_url, upload_file_to_oss
from backend.app.services.study_service import finish_session
from backend.app.worker.tasks.correct_homework import run_homework_correction

router = APIRouter(prefix="/submissions", tags=["submissions"])


@router.post("")
def create_submission(payload: SubmissionCreateIn, db: Session = Depends(get_db)):
    task = db.get(DailyTask, payload.daily_task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if payload.linked_study_session_id is not None:
        session = db.get(StudySession, payload.linked_study_session_id)
        if (
            not session
            or session.end_time is not None
            or session.daily_task_id != task.id
            or session.student_id != task.student_id
        ):
            raise HTTPException(status_code=422, detail="Study session does not match task")
    submission = Submission(
        daily_task_id=payload.daily_task_id,
        student_id=task.student_id,
        submission_type=payload.submission_type,
        linked_study_session_id=payload.linked_study_session_id,
        student_note=payload.student_note,
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
    submission = db.get(Submission, submission_id)
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    if purpose != "homework":
        raise HTTPException(status_code=422, detail="Student submissions only accept homework media")
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
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    has_homework_media = db.query(SubmissionMedia).filter(
        SubmissionMedia.submission_id == submission_id,
        SubmissionMedia.purpose == "homework",
    ).first() is not None
    if not has_homework_media:
        raise HTTPException(status_code=422, detail="Upload homework media before completing the submission")
    submission.status = "processing"
    task = db.get(DailyTask, submission.daily_task_id)
    task.status = "correcting"
    completed_at = submission.submitted_at or datetime.now(UTC).replace(
        tzinfo=None,
        microsecond=0,
    )
    if submission.submitted_at is None:
        submission.submitted_at = completed_at
    if submission.linked_study_session_id:
        finish_session(db, submission.linked_study_session_id, completed_at)
    notify_submission_uploaded(db, submission, task)
    db.commit()
    background_tasks.add_task(run_homework_correction.delay, submission.id)
    return ok({"submission_id": submission.id, "status": "processing", "daily_task_status": "correcting"})


@router.get("/{submission_id}")
def get_submission(submission_id: int, db: Session = Depends(get_db)):
    submission = db.get(Submission, submission_id)
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    has_answer_media = db.query(SubmissionMedia).filter(
        SubmissionMedia.submission_id == submission_id,
        SubmissionMedia.purpose == "answer",
    ).first() is not None
    homework_media_count = db.query(SubmissionMedia).filter(
        SubmissionMedia.submission_id == submission_id,
        SubmissionMedia.purpose == "homework",
    ).count()
    return ok({
        "id": submission.id,
        "daily_task_id": submission.daily_task_id,
        "status": submission.status,
        "homework_media_count": homework_media_count,
        "has_answer": bool(submission.answer_text and submission.answer_text.strip()) or has_answer_media,
    })


@router.get("/media/{media_id}/content")
def media_content(
    media_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    media = db.get(SubmissionMedia, media_id)
    submission = db.get(Submission, media.submission_id) if media else None
    student = db.get(Student, submission.student_id) if submission else None
    if not media or not submission or not student:
        raise HTTPException(status_code=404, detail="Submission media not found")
    if not can_access_student(db, user, student):
        raise HTTPException(status_code=403, detail="Submission media does not belong to current user")
    signed_url = signed_download_url(media.file_url)
    if signed_url.startswith("http"):
        return RedirectResponse(signed_url)
    local_path = local_path_for_submission_media(media)
    return FileResponse(local_path)
