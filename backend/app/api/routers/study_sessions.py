from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.core.responses import ok
from backend.app.schemas.requests import StudySessionFinishIn, StudySessionStartIn
from backend.app.services.study_service import (
    elapsed_seconds,
    finish_session,
    get_active_session,
    pause_session,
    resume_session,
    start_session,
)

router = APIRouter(prefix="/study-sessions", tags=["study-sessions"])


def session_payload(session):
    return {
        "session_id": session.id,
        "daily_task_id": session.daily_task_id,
        "start_time": session.start_time,
        "status": session.status,
        "elapsed_seconds": elapsed_seconds(session),
    }


@router.post("/start")
def start(payload: StudySessionStartIn, db: Session = Depends(get_db)):
    try:
        session = start_session(db, payload.daily_task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ok(session_payload(session))


@router.get("/active")
def active(daily_task_id: int, db: Session = Depends(get_db)):
    session = get_active_session(db, daily_task_id)
    if not session:
        return {"code": 0, "message": "ok", "data": None}
    return ok(session_payload(session))


@router.post("/{session_id}/pause")
def pause(session_id: int, db: Session = Depends(get_db)):
    session = pause_session(db, session_id)
    return ok({"session_id": session.id, "status": session.status})


@router.post("/{session_id}/resume")
def resume(session_id: int, db: Session = Depends(get_db)):
    session = resume_session(db, session_id)
    return ok({"session_id": session.id, "status": session.status})


@router.post("/{session_id}/finish")
def finish(session_id: int, payload: StudySessionFinishIn, db: Session = Depends(get_db)):
    session = finish_session(db, session_id)
    return ok({"session_id": session.id, "status": session.status, "duration_seconds": session.duration_seconds})
