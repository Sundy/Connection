from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from threading import Barrier, Event, Lock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from backend.app.core.database import Base, SessionLocal, init_db
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
from backend.app.services import study_service
from backend.app.services.study_service import elapsed_seconds, start_session


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


@pytest.fixture
def sqlite_study_task(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'study-elapsed.db'}",
        connect_args={"timeout": 5},
    )
    Base.metadata.create_all(engine)
    sqlite_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with sqlite_session() as db:
        family = Family(name="sqlite-concurrent-start")
        db.add(family)
        db.flush()
        student = Student(
            family_id=family.id,
            name="sqlite-concurrent-start",
            grade="四年级",
        )
        db.add(student)
        db.flush()
        batch = AssignmentBatch(
            student_id=student.id,
            title="sqlite-concurrent-start",
            status="active",
        )
        db.add(batch)
        db.flush()
        item = AssignmentItem(
            assignment_batch_id=batch.id,
            subject="数学",
            title="sqlite-concurrent-start",
        )
        db.add(item)
        db.flush()
        task = DailyTask(
            student_id=student.id,
            assignment_batch_id=batch.id,
            assignment_item_id=item.id,
            task_date=date.today(),
            subject="数学",
            title="sqlite-concurrent-start",
        )
        db.add(task)
        db.commit()
        task_id = task.id

    try:
        yield {"session_factory": sqlite_session, "task_id": task_id}
    finally:
        engine.dispose()


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


def test_concurrent_starts_create_only_one_unfinished_session(
    sqlite_study_task,
    monkeypatch,
):
    session_factory = sqlite_study_task["session_factory"]
    task_id = sqlite_study_task["task_id"]
    original_get_active_session = study_service.get_active_session
    lookup_count = 0
    lookup_count_lock = Lock()
    second_lookup_complete = Event()

    def coordinated_get_active_session(db, requested_task_id):
        nonlocal lookup_count
        active_session = original_get_active_session(db, requested_task_id)
        with lookup_count_lock:
            lookup_count += 1
            current_lookup = lookup_count
        if current_lookup == 1:
            second_lookup_complete.wait(timeout=1)
        elif current_lookup == 2:
            second_lookup_complete.set()
        return active_session

    monkeypatch.setattr(
        study_service,
        "get_active_session",
        coordinated_get_active_session,
    )
    start_barrier = Barrier(2)

    def start_in_separate_connection():
        try:
            with session_factory() as db:
                start_barrier.wait(timeout=5)
                return start_session(db, task_id).id
        except Exception as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(start_in_separate_connection)
            for _ in range(2)
        ]
        results = [future.result(timeout=10) for future in futures]

    assert all(isinstance(result, int) for result in results), results
    assert len(set(results)) == 1
    with session_factory() as db:
        assert db.query(StudySession).filter(
            StudySession.daily_task_id == task_id,
            StudySession.end_time.is_(None),
        ).count() == 1


def test_start_locks_daily_task_before_active_session_lookup(
    study_elapsed_fixture,
):
    task_id = study_elapsed_fixture["task_ids"][0]
    executed_selects = []

    with SessionLocal() as db:
        def capture_orm_execute(execute_state):
            if execute_state.is_select:
                executed_selects.append(execute_state.statement)

        event.listen(db, "do_orm_execute", capture_orm_execute)
        start_session(db, task_id)

    task_select_index = next(
        index
        for index, statement in enumerate(executed_selects)
        if any(
            description.get("entity") is DailyTask
            for description in statement.column_descriptions
        )
    )
    session_select_index = next(
        index
        for index, statement in enumerate(executed_selects)
        if any(
            description.get("entity") is StudySession
            for description in statement.column_descriptions
        )
    )
    assert executed_selects[task_select_index]._for_update_arg is not None
    assert task_select_index < session_select_index


def test_elapsed_seconds_uses_end_time_for_ended_session():
    start_time = datetime(2026, 7, 19, 10, 0, 0)
    session = StudySession(
        start_time=start_time,
        end_time=start_time + timedelta(seconds=93),
    )

    assert elapsed_seconds(
        session,
        at=start_time + timedelta(hours=1),
    ) == 93
