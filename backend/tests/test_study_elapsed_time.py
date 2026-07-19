from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app.core.database import SessionLocal, init_db
from backend.app.main import app
from backend.app.models import (
    AssignmentBatch,
    AssignmentItem,
    DailyTask,
    Family,
    Student,
    StudySession,
    Submission,
    SubmissionMedia,
)


init_db()
client = TestClient(app)


def unwrap(response):
    assert response.status_code < 300, response.text
    payload = response.json()
    assert payload["code"] == 0, payload
    return payload["data"]


@pytest.fixture
def study_elapsed_fixture():
    marker = f"study-elapsed-{uuid4().hex}"
    inserted_ids = {
        "family_ids": set(),
        "student_ids": set(),
        "batch_ids": set(),
        "item_ids": set(),
        "task_ids": set(),
    }

    with SessionLocal() as db:
        family = Family(name=marker)
        db.add(family)
        db.flush()
        student = Student(family_id=family.id, name=marker, grade="四年级")
        db.add(student)
        db.flush()
        batch = AssignmentBatch(
            student_id=student.id,
            title=marker,
            status="active",
        )
        db.add(batch)
        db.flush()
        item = AssignmentItem(
            assignment_batch_id=batch.id,
            subject="数学",
            title=marker,
        )
        db.add(item)
        db.flush()
        tasks = [
            DailyTask(
                student_id=student.id,
                assignment_batch_id=batch.id,
                assignment_item_id=item.id,
                task_date=date.today(),
                subject="数学",
                title=f"{marker}-{suffix}",
            )
            for suffix in ("first", "second")
        ]
        db.add_all(tasks)
        db.commit()

        inserted_ids["family_ids"].add(family.id)
        inserted_ids["student_ids"].add(student.id)
        inserted_ids["batch_ids"].add(batch.id)
        inserted_ids["item_ids"].add(item.id)
        inserted_ids["task_ids"].update(task.id for task in tasks)

    try:
        yield {"task_ids": [task.id for task in tasks]}
    finally:
        with SessionLocal() as db:
            task_ids = inserted_ids["task_ids"]
            session_ids = {
                row.id
                for row in db.query(StudySession.id).filter(
                    StudySession.daily_task_id.in_(task_ids)
                )
            }
            submission_ids = {
                row.id
                for row in db.query(Submission.id).filter(
                    Submission.daily_task_id.in_(task_ids)
                )
            }
            media_ids = {
                row.id
                for row in db.query(SubmissionMedia.id).filter(
                    SubmissionMedia.submission_id.in_(submission_ids)
                )
            } if submission_ids else set()

            def delete_exact(model, row_ids):
                if row_ids:
                    db.query(model).filter(model.id.in_(row_ids)).delete(
                        synchronize_session=False
                    )
                    db.flush()

            delete_exact(SubmissionMedia, media_ids)
            delete_exact(Submission, submission_ids)
            delete_exact(StudySession, session_ids)
            delete_exact(DailyTask, task_ids)
            delete_exact(AssignmentItem, inserted_ids["item_ids"])
            delete_exact(AssignmentBatch, inserted_ids["batch_ids"])
            delete_exact(Student, inserted_ids["student_ids"])
            delete_exact(Family, inserted_ids["family_ids"])
            db.commit()


def test_start_reuses_an_unfinished_legacy_paused_session(study_elapsed_fixture):
    task_id = study_elapsed_fixture["task_ids"][0]

    first = unwrap(client.post(
        "/api/v1/study-sessions/start",
        json={"daily_task_id": task_id},
    ))
    unwrap(client.post(f"/api/v1/study-sessions/{first['session_id']}/pause", json={}))
    second = unwrap(client.post(
        "/api/v1/study-sessions/start",
        json={"daily_task_id": task_id},
    ))

    assert second["session_id"] == first["session_id"]
    assert second["elapsed_seconds"] >= 0
    with SessionLocal() as db:
        assert db.query(StudySession).filter(
            StudySession.daily_task_id == task_id,
            StudySession.end_time.is_(None),
        ).count() == 1


def test_active_session_returns_server_calculated_elapsed_seconds(study_elapsed_fixture):
    task_id = study_elapsed_fixture["task_ids"][0]
    session = unwrap(client.post(
        "/api/v1/study-sessions/start",
        json={"daily_task_id": task_id},
    ))
    session_id = session["session_id"]

    with SessionLocal() as db:
        stored_session = db.get(StudySession, session_id)
        stored_session.start_time = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=2)
        db.commit()

    active = unwrap(client.get(
        f"/api/v1/study-sessions/active?daily_task_id={task_id}",
    ))
    assert active["session_id"] == session_id
    assert 119 <= active["elapsed_seconds"] <= 121


def test_active_session_returns_null_when_task_has_no_session(study_elapsed_fixture):
    task_id = study_elapsed_fixture["task_ids"][1]

    response = client.get(f"/api/v1/study-sessions/active?daily_task_id={task_id}")

    assert response.status_code < 300, response.text
    payload = response.json()
    assert payload["code"] == 0, payload
    assert payload["data"] is None
