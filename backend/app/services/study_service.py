from datetime import UTC, datetime

from sqlalchemy.orm import Session

from backend.app.models import DailyTask, StudySession


def start_session(db: Session, task_id: int) -> StudySession:
    task = db.get(DailyTask, task_id)
    if not task:
        raise ValueError("Task not found")

    running = db.query(StudySession).filter(
        StudySession.daily_task_id == task_id,
        StudySession.status == "running",
    ).first()
    if running:
        return running

    session = StudySession(daily_task_id=task.id, student_id=task.student_id)
    task.status = "running"
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def finish_session(db: Session, session_id: int) -> StudySession:
    session = db.get(StudySession, session_id)
    if not session:
        raise ValueError("Session not found")
    if not session.end_time:
        session.end_time = datetime.now(UTC).replace(tzinfo=None)
        session.duration_seconds = max(int((session.end_time - session.start_time).total_seconds()), 0)
    session.status = "completed"
    task = db.get(DailyTask, session.daily_task_id)
    if task and task.status in {"running", "paused"}:
        task.status = "ready_to_submit"
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
