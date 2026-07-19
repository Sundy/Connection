from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from threading import Barrier, Event, Lock
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from backend.app.api.routers import submissions as submissions_router
from backend.app.core.database import Base, SessionLocal, init_db
from backend.app.main import app
from backend.app.models import (
    AssignmentBatch,
    AssignmentItem,
    CorrectionResult,
    DailyTask,
    Family,
    Student,
    StudySession,
    Submission,
    SubmissionMedia,
)
from backend.app.services import study_service
from backend.app.services.correction_service import _create_result_from_payload
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
            correction_ids = {
                row.id
                for row in db.query(CorrectionResult.id).filter(
                    CorrectionResult.daily_task_id.in_(task_ids)
                )
            }

            def delete_exact(model, row_ids):
                if row_ids:
                    db.query(model).filter(model.id.in_(row_ids)).delete(
                        synchronize_session=False
                    )
                    db.flush()

            delete_exact(SubmissionMedia, media_ids)
            delete_exact(CorrectionResult, correction_ids)
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


def test_correction_result_uses_only_the_resubmission_linked_session(
    study_elapsed_fixture,
):
    task_id = study_elapsed_fixture["task_ids"][0]

    with SessionLocal() as db:
        task = db.get(DailyTask, task_id)
        previous_session = StudySession(
            daily_task_id=task.id,
            student_id=task.student_id,
            status="completed",
            duration_seconds=120,
        )
        linked_session = StudySession(
            daily_task_id=task.id,
            student_id=task.student_id,
            status="completed",
            duration_seconds=45,
        )
        db.add_all([previous_session, linked_session])
        db.flush()
        db.add(Submission(
            daily_task_id=task.id,
            student_id=task.student_id,
            submission_type="photo",
            linked_study_session_id=previous_session.id,
            status="corrected",
        ))
        submission = Submission(
            daily_task_id=task.id,
            student_id=task.student_id,
            submission_type="photo",
            linked_study_session_id=linked_session.id,
            status="processing",
        )
        db.add(submission)
        db.flush()

        result = _create_result_from_payload(db, submission, {})

        assert result.study_duration_seconds == 45


def test_correction_result_reports_zero_for_an_unlinked_submission(
    study_elapsed_fixture,
):
    task_id = study_elapsed_fixture["task_ids"][0]

    with SessionLocal() as db:
        task = db.get(DailyTask, task_id)
        db.add(StudySession(
            daily_task_id=task.id,
            student_id=task.student_id,
            status="completed",
            duration_seconds=120,
        ))
        submission = Submission(
            daily_task_id=task.id,
            student_id=task.student_id,
            submission_type="photo",
            status="processing",
        )
        db.add(submission)
        db.flush()

        result = _create_result_from_payload(db, submission, {})

        assert result.study_duration_seconds == 0


def test_correction_result_reports_zero_for_an_invalid_linked_session(
    study_elapsed_fixture,
):
    task_id = study_elapsed_fixture["task_ids"][0]

    with SessionLocal() as db:
        task = db.get(DailyTask, task_id)
        running_session = StudySession(
            daily_task_id=task.id,
            student_id=task.student_id,
            status="running",
            duration_seconds=90,
        )
        db.add(running_session)
        db.flush()
        submission = Submission(
            daily_task_id=task.id,
            student_id=task.student_id,
            submission_type="photo",
            linked_study_session_id=running_session.id,
            status="processing",
        )
        db.add(submission)
        db.flush()

        result = _create_result_from_payload(db, submission, {})

        assert result.study_duration_seconds == 0


def test_create_submission_rejects_session_for_another_task(study_elapsed_fixture):
    first_task_id, second_task_id = study_elapsed_fixture["task_ids"]
    session = unwrap(client.post(
        "/api/v1/study-sessions/start",
        json={"daily_task_id": first_task_id},
    ))

    response = client.post("/api/v1/submissions", json={
        "daily_task_id": second_task_id,
        "submission_type": "photo",
        "linked_study_session_id": session["session_id"],
    })

    assert response.status_code == 422
    assert "does not match" in response.json()["detail"]
    with SessionLocal() as db:
        assert db.query(Submission).filter(
            Submission.daily_task_id == second_task_id,
        ).count() == 0


def test_create_submission_rejects_missing_linked_session(study_elapsed_fixture):
    task_id = study_elapsed_fixture["task_ids"][0]

    with TestClient(app, raise_server_exceptions=False) as unhandled_client:
        response = unhandled_client.post("/api/v1/submissions", json={
            "daily_task_id": task_id,
            "submission_type": "photo",
            "linked_study_session_id": 0,
        })

    assert response.status_code == 422
    assert "does not match" in response.json()["detail"]
    with SessionLocal() as db:
        assert db.query(Submission).filter(
            Submission.daily_task_id == task_id,
        ).count() == 0


def test_create_submission_rejects_completed_session(study_elapsed_fixture):
    first_task_id = study_elapsed_fixture["task_ids"][0]
    session = unwrap(client.post(
        "/api/v1/study-sessions/start",
        json={"daily_task_id": first_task_id},
    ))
    unwrap(client.post(
        f"/api/v1/study-sessions/{session['session_id']}/finish",
        json={},
    ))

    response = client.post("/api/v1/submissions", json={
        "daily_task_id": first_task_id,
        "submission_type": "photo",
        "linked_study_session_id": session["session_id"],
    })

    assert response.status_code == 422
    assert "does not match" in response.json()["detail"]
    with SessionLocal() as db:
        assert db.query(Submission).filter(
            Submission.daily_task_id == first_task_id,
        ).count() == 0


def test_mysql_completion_locks_submission_and_linked_session_rows():
    statements = []

    class RecordingSession:
        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="mysql"))

        def scalar(self, statement):
            statements.append(statement)
            return None

    db = RecordingSession()
    lock_submission = getattr(
        submissions_router,
        "_lock_submission_for_completion",
        None,
    )
    lock_session = getattr(
        submissions_router,
        "_lock_study_session_for_completion",
        None,
    )

    assert lock_submission is not None
    assert lock_session is not None
    lock_submission(db, 11)
    lock_session(db, 22)

    assert len(statements) == 2
    assert all(statement._for_update_arg is not None for statement in statements)


def test_complete_submission_revalidates_the_locked_session(
    study_elapsed_fixture,
    monkeypatch,
):
    first_task_id, second_task_id = study_elapsed_fixture["task_ids"]
    session_data = unwrap(client.post(
        "/api/v1/study-sessions/start",
        json={"daily_task_id": first_task_id},
    ))
    submission_data = unwrap(client.post("/api/v1/submissions", json={
        "daily_task_id": first_task_id,
        "submission_type": "photo",
        "linked_study_session_id": session_data["session_id"],
    }))

    with SessionLocal() as db:
        session = db.get(StudySession, session_data["session_id"])
        session.daily_task_id = second_task_id
        db.add(SubmissionMedia(
            submission_id=submission_data["submission_id"],
            media_type="image",
            purpose="homework",
            file_url="test://homework.jpg",
        ))
        db.commit()

    monkeypatch.setattr(
        "backend.app.api.routers.submissions.run_homework_correction.delay",
        lambda submission_id: None,
    )

    response = client.post(
        f"/api/v1/submissions/{submission_data['submission_id']}/complete",
    )

    assert response.status_code == 422
    assert "does not match" in response.json()["detail"]
    with SessionLocal() as db:
        submission = db.get(Submission, submission_data["submission_id"])
        session = db.get(StudySession, session_data["session_id"])
        assert submission.submitted_at is None
        assert session.end_time is None


def test_complete_submission_rejects_a_session_finished_after_creation(
    study_elapsed_fixture,
    monkeypatch,
):
    task_id = study_elapsed_fixture["task_ids"][0]
    session_data = unwrap(client.post(
        "/api/v1/study-sessions/start",
        json={"daily_task_id": task_id},
    ))
    submission_data = unwrap(client.post("/api/v1/submissions", json={
        "daily_task_id": task_id,
        "submission_type": "photo",
        "linked_study_session_id": session_data["session_id"],
    }))

    with SessionLocal() as db:
        session = db.get(StudySession, session_data["session_id"])
        finished_at = session.start_time + timedelta(seconds=37)
        study_service.finish_session(db, session.id, finished_at)
        original_duration = session.duration_seconds
        db.add(SubmissionMedia(
            submission_id=submission_data["submission_id"],
            media_type="image",
            purpose="homework",
            file_url="test://homework.jpg",
        ))
        db.commit()

    monkeypatch.setattr(
        "backend.app.api.routers.submissions.run_homework_correction.delay",
        lambda submission_id: None,
    )

    response = client.post(
        f"/api/v1/submissions/{submission_data['submission_id']}/complete",
    )

    assert response.status_code == 422
    assert "already ended" in response.json()["detail"]
    with SessionLocal() as db:
        submission = db.get(Submission, submission_data["submission_id"])
        session = db.get(StudySession, session_data["session_id"])
        assert submission.submitted_at is None
        assert session.end_time == finished_at
        assert session.duration_seconds == original_duration


def test_complete_submission_accepts_only_matching_completed_timestamps(
    study_elapsed_fixture,
    monkeypatch,
):
    task_id = study_elapsed_fixture["task_ids"][0]

    with SessionLocal() as db:
        task = db.get(DailyTask, task_id)
        session = StudySession(
            daily_task_id=task.id,
            student_id=task.student_id,
            start_time=datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=60),
        )
        db.add(session)
        db.flush()
        completed_at = session.start_time + timedelta(seconds=41)
        session.end_time = completed_at
        session.duration_seconds = 41
        session.status = "completed"
        submission = Submission(
            daily_task_id=task.id,
            student_id=task.student_id,
            submission_type="photo",
            linked_study_session_id=session.id,
            submitted_at=completed_at,
            status="processing",
        )
        db.add(submission)
        db.flush()
        db.add(SubmissionMedia(
            submission_id=submission.id,
            media_type="image",
            purpose="homework",
            file_url="test://homework.jpg",
        ))
        db.commit()
        submission_id = submission.id
        session_id = session.id

    with SessionLocal() as db:
        persisted_submission = db.get(Submission, submission_id)
        persisted_session = db.get(StudySession, session_id)
        assert persisted_submission.submitted_at == persisted_session.end_time
        completed_at = persisted_submission.submitted_at

    monkeypatch.setattr(
        "backend.app.api.routers.submissions.run_homework_correction.delay",
        lambda submission_id: None,
    )

    first = client.post(f"/api/v1/submissions/{submission_id}/complete")
    second = client.post(f"/api/v1/submissions/{submission_id}/complete")

    assert first.status_code == 200
    assert second.status_code == 200
    with SessionLocal() as db:
        submission = db.get(Submission, submission_id)
        session = db.get(StudySession, session_id)
        assert submission.submitted_at == completed_at
        assert session.end_time == completed_at
        assert session.duration_seconds == 41


def test_mysql_direct_finish_locks_the_study_session_row():
    statements = []

    class RecordingSession:
        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="mysql"))

        def scalar(self, statement):
            statements.append(statement)
            return None

    with pytest.raises(ValueError, match="Session not found"):
        study_service.finish_session(RecordingSession(), 11)

    assert len(statements) == 1
    assert statements[0]._for_update_arg is not None


def test_sqlite_completion_commits_once_at_the_router_boundary(sqlite_study_task):
    session_factory = sqlite_study_task["session_factory"]
    task_id = sqlite_study_task["task_id"]

    with session_factory() as db:
        task = db.get(DailyTask, task_id)
        session = StudySession(
            daily_task_id=task.id,
            student_id=task.student_id,
            start_time=datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=30),
        )
        db.add(session)
        db.flush()
        submission = Submission(
            daily_task_id=task.id,
            student_id=task.student_id,
            submission_type="photo",
            linked_study_session_id=session.id,
        )
        db.add(submission)
        db.flush()
        db.add(SubmissionMedia(
            submission_id=submission.id,
            media_type="image",
            purpose="homework",
            file_url="test://homework.jpg",
        ))
        db.commit()
        submission_id = submission.id

    commit_count = 0
    with session_factory() as db:
        def count_commit(session):
            nonlocal commit_count
            commit_count += 1

        event.listen(db, "after_commit", count_commit)
        submissions_router.complete(submission_id, BackgroundTasks(), db)

    assert commit_count == 1


def test_concurrent_sqlite_completions_preserve_one_timestamp_and_duration(
    sqlite_study_task,
    monkeypatch,
):
    session_factory = sqlite_study_task["session_factory"]
    task_id = sqlite_study_task["task_id"]

    with session_factory() as db:
        task = db.get(DailyTask, task_id)
        session = StudySession(
            daily_task_id=task.id,
            student_id=task.student_id,
            start_time=datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=30),
        )
        db.add(session)
        db.flush()
        submission = Submission(
            daily_task_id=task.id,
            student_id=task.student_id,
            submission_type="photo",
            linked_study_session_id=session.id,
        )
        db.add(submission)
        db.flush()
        db.add(SubmissionMedia(
            submission_id=submission.id,
            media_type="image",
            purpose="homework",
            file_url="test://homework.jpg",
        ))
        db.commit()
        submission_id = submission.id
        session_id = session.id

    lock_submission = getattr(
        submissions_router,
        "_lock_submission_for_completion",
        None,
    )
    assert lock_submission is not None
    call_count = 0
    call_count_lock = Lock()
    second_call_started = Event()

    def coordinated_lock(db, requested_submission_id):
        nonlocal call_count
        with call_count_lock:
            call_count += 1
            current_call = call_count
        if current_call == 2:
            second_call_started.set()
        locked_submission = lock_submission(db, requested_submission_id)
        if current_call == 1:
            assert second_call_started.wait(timeout=5)
        return locked_submission

    monkeypatch.setattr(
        submissions_router,
        "_lock_submission_for_completion",
        coordinated_lock,
    )
    start_barrier = Barrier(2)

    def complete_in_separate_connection():
        try:
            with session_factory() as db:
                start_barrier.wait(timeout=5)
                submissions_router.complete(
                    submission_id,
                    BackgroundTasks(),
                    db,
                )
                return None
        except Exception as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(complete_in_separate_connection)
            for _ in range(2)
        ]
        errors = [future.result(timeout=10) for future in futures]

    assert errors == [None, None]
    with session_factory() as db:
        submission = db.get(Submission, submission_id)
        session = db.get(StudySession, session_id)
        assert submission.submitted_at == session.end_time
        assert session.duration_seconds == elapsed_seconds(session)


def test_concurrent_sqlite_direct_finish_then_complete_has_one_valid_outcome(
    sqlite_study_task,
    monkeypatch,
):
    session_factory = sqlite_study_task["session_factory"]
    task_id = sqlite_study_task["task_id"]

    with session_factory() as db:
        task = db.get(DailyTask, task_id)
        session = StudySession(
            daily_task_id=task.id,
            student_id=task.student_id,
            start_time=datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=30),
        )
        db.add(session)
        db.flush()
        submission = Submission(
            daily_task_id=task.id,
            student_id=task.student_id,
            submission_type="photo",
            linked_study_session_id=session.id,
        )
        db.add(submission)
        db.flush()
        db.add(SubmissionMedia(
            submission_id=submission.id,
            media_type="image",
            purpose="homework",
            file_url="test://homework.jpg",
        ))
        db.commit()
        submission_id = submission.id
        session_id = session.id

    direct_lock = getattr(study_service, "_lock_session_for_finish", None)
    assert direct_lock is not None
    completion_lock = submissions_router._lock_submission_for_completion
    direct_locked = Event()
    completion_started = Event()

    def coordinated_direct_lock(db, requested_session_id):
        locked_session = direct_lock(db, requested_session_id)
        direct_locked.set()
        assert completion_started.wait(timeout=5)
        return locked_session

    def coordinated_completion_lock(db, requested_submission_id):
        assert direct_locked.wait(timeout=5)
        completion_started.set()
        return completion_lock(db, requested_submission_id)

    monkeypatch.setattr(
        study_service,
        "_lock_session_for_finish",
        coordinated_direct_lock,
    )
    monkeypatch.setattr(
        submissions_router,
        "_lock_submission_for_completion",
        coordinated_completion_lock,
    )
    start_barrier = Barrier(2)

    def finish_in_separate_connection():
        try:
            with session_factory() as db:
                start_barrier.wait(timeout=5)
                study_service.finish_session(db, session_id)
                return 200
        except Exception as exc:
            return exc

    def complete_in_separate_connection():
        try:
            with session_factory() as db:
                start_barrier.wait(timeout=5)
                submissions_router.complete(
                    submission_id,
                    BackgroundTasks(),
                    db,
                )
                return 200
        except HTTPException as exc:
            return exc.status_code
        except Exception as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        finish_future = executor.submit(finish_in_separate_connection)
        complete_future = executor.submit(complete_in_separate_connection)
        outcomes = [
            finish_future.result(timeout=10),
            complete_future.result(timeout=10),
        ]

    assert outcomes == [200, 422]
    with session_factory() as db:
        submission = db.get(Submission, submission_id)
        session = db.get(StudySession, session_id)
        assert submission.submitted_at is None
        assert session.end_time is not None
        assert session.duration_seconds == elapsed_seconds(session)


def test_complete_submission_sets_and_preserves_authoritative_session_time(
    study_elapsed_fixture,
    monkeypatch,
):
    task_id = study_elapsed_fixture["task_ids"][0]
    session_data = unwrap(client.post(
        "/api/v1/study-sessions/start",
        json={"daily_task_id": task_id},
    ))
    submission_data = unwrap(client.post("/api/v1/submissions", json={
        "daily_task_id": task_id,
        "submission_type": "photo",
        "linked_study_session_id": session_data["session_id"],
    }))
    submission_id = submission_data["submission_id"]

    with SessionLocal() as db:
        db.add(SubmissionMedia(
            submission_id=submission_id,
            media_type="image",
            purpose="homework",
            file_url="test://homework.jpg",
        ))
        db.commit()

    monkeypatch.setattr(
        "backend.app.api.routers.submissions.run_homework_correction.delay",
        lambda submission_id: None,
    )

    response = client.post(f"/api/v1/submissions/{submission_id}/complete")

    assert response.status_code == 200
    with SessionLocal() as db:
        submission = db.get(Submission, submission_id)
        session = db.get(StudySession, session_data["session_id"])
        assert submission.submitted_at is not None
        assert session.end_time == submission.submitted_at
        assert session.duration_seconds == int(
            (submission.submitted_at - session.start_time).total_seconds()
        )
        submitted_at = submission.submitted_at
        end_time = session.end_time
        duration_seconds = session.duration_seconds

    response = client.post(f"/api/v1/submissions/{submission_id}/complete")

    assert response.status_code == 200
    with SessionLocal() as db:
        submission = db.get(Submission, submission_id)
        session = db.get(StudySession, session_data["session_id"])
        assert submission.submitted_at == submitted_at
        assert session.end_time == end_time
        assert session.duration_seconds == duration_seconds

    unlinked_submission = unwrap(client.post("/api/v1/submissions", json={
        "daily_task_id": study_elapsed_fixture["task_ids"][1],
        "submission_type": "photo",
    }))
    with SessionLocal() as db:
        db.add(SubmissionMedia(
            submission_id=unlinked_submission["submission_id"],
            media_type="image",
            purpose="homework",
            file_url="test://unlinked-homework.jpg",
        ))
        db.commit()

    response = client.post(
        f"/api/v1/submissions/{unlinked_submission['submission_id']}/complete",
    )

    assert response.status_code == 200
