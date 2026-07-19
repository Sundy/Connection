from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.app.models import DailyTask, StudySession


def get_active_session(db: Session, task_id: int) -> StudySession | None:
    return db.query(StudySession).filter(
        StudySession.daily_task_id == task_id,
        StudySession.end_time.is_(None),
        StudySession.status.in_({"running", "paused"}),
    ).order_by(StudySession.id.desc()).first()


def elapsed_seconds(session: StudySession, at: datetime | None = None) -> int:
    end = session.end_time or at or datetime.now(UTC).replace(tzinfo=None)
    return max(int((end - session.start_time).total_seconds()), 0)


def _lock_task_for_start(db: Session, task_id: int) -> DailyTask | None:
    if db.get_bind().dialect.name == "sqlite" and not db.in_transaction():
        db.execute(text("BEGIN IMMEDIATE"))
    return db.scalar(
        select(DailyTask)
        .where(DailyTask.id == task_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )


def start_session(db: Session, task_id: int) -> StudySession:
    task = _lock_task_for_start(db, task_id)
    if not task:
        raise ValueError("Task not found")

    active_session = get_active_session(db, task_id)
    if active_session:
        return active_session

    session = StudySession(daily_task_id=task.id, student_id=task.student_id)
    task.status = "running"
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def finish_session(
    db: Session,
    session_id: int,
    finished_at: datetime | None = None,
    *,
    commit: bool = True,
) -> StudySession:
    session = db.get(StudySession, session_id)
    if not session:
        raise ValueError("Session not found")
    if not session.end_time:
        session.end_time = finished_at or datetime.now(UTC).replace(tzinfo=None)
        session.duration_seconds = elapsed_seconds(session, at=session.end_time)
    session.status = "completed"
    task = db.get(DailyTask, session.daily_task_id)
    if task and task.status in {"running", "paused"}:
        task.status = "ready_to_submit"
    if commit:
        db.commit()
        db.refresh(session)
    return session


def pause_session(db: Session, session_id: int) -> StudySession:
    session = db.get(StudySession, session_id)
    if not session:
        raise ValueError("Session not found")
    session.status = "paused"
    task = db.get(DailyTask, session.daily_task_id)
    if task:
        task.status = "paused"
    db.commit()
    db.refresh(session)
    return session


def resume_session(db: Session, session_id: int) -> StudySession:
    session = db.get(StudySession, session_id)
    if not session:
        raise ValueError("Session not found")
    session.status = "running"
    task = db.get(DailyTask, session.daily_task_id)
    if task:
        task.status = "running"
    db.commit()
    db.refresh(session)
    return session
