from datetime import date
from io import BytesIO
from pathlib import Path
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
    FamilyMember,
    Notification,
    Student,
    Submission,
    SubmissionMedia,
    User,
)


init_db()
client = TestClient(app)


def unwrap(response):
    assert response.status_code < 300, response.text
    payload = response.json()
    assert payload["code"] == 0, payload
    return payload["data"]


@pytest.fixture
def notification_fixture(monkeypatch):
    marker = f"notification-{uuid4().hex}"
    user_ids: set[int] = set()
    family_ids: set[int] = set()
    student_ids: set[int] = set()
    plan_ids: set[int] = set()
    task_ids: set[int] = set()
    submission_ids: set[int] = set()
    storage_paths: set[Path] = set()

    monkeypatch.setattr(
        "backend.app.api.routers.submissions.run_homework_correction.delay",
        lambda _submission_id: None,
    )

    def login(role: str, suffix: str):
        data = unwrap(client.post("/api/v1/auth/wechat-login", json={
            "code": f"{marker}-{role}-{suffix}",
            "role": role,
        }))
        user_ids.add(data["user"]["id"])
        return data

    try:
        yield {
            "marker": marker,
            "login": login,
            "family_ids": family_ids,
            "student_ids": student_ids,
            "plan_ids": plan_ids,
            "task_ids": task_ids,
            "submission_ids": submission_ids,
        }
    finally:
        with SessionLocal() as db:
            if not family_ids and user_ids:
                family_ids.update(
                    row.id for row in db.query(Family.id).filter(
                        Family.created_by.in_(user_ids)
                    )
                )
            if family_ids:
                student_ids.update(
                    row.id for row in db.query(Student.id).filter(
                        Student.family_id.in_(family_ids)
                    )
                )
            if student_ids:
                plan_ids.update(
                    row.id for row in db.query(AssignmentBatch.id).filter(
                        AssignmentBatch.student_id.in_(student_ids)
                    )
                )
            if plan_ids:
                task_ids.update(
                    row.id for row in db.query(DailyTask.id).filter(
                        DailyTask.assignment_batch_id.in_(plan_ids)
                    )
                )
            if task_ids:
                submission_ids.update(
                    row.id for row in db.query(Submission.id).filter(
                        Submission.daily_task_id.in_(task_ids)
                    )
                )
            if submission_ids:
                media_rows = db.query(SubmissionMedia).filter(
                    SubmissionMedia.submission_id.in_(submission_ids)
                ).all()
                for media in media_rows:
                    if media.storage_path:
                        storage_paths.add(Path(media.storage_path))
            if user_ids:
                db.query(Notification).filter(
                    Notification.user_id.in_(user_ids)
                ).delete(synchronize_session=False)
            if submission_ids:
                db.query(SubmissionMedia).filter(
                    SubmissionMedia.submission_id.in_(submission_ids)
                ).delete(synchronize_session=False)
            if submission_ids:
                db.query(Submission).filter(
                    Submission.id.in_(submission_ids)
                ).delete(synchronize_session=False)
            if task_ids:
                db.query(DailyTask).filter(
                    DailyTask.id.in_(task_ids)
                ).delete(synchronize_session=False)
            if plan_ids:
                db.query(AssignmentItem).filter(
                    AssignmentItem.assignment_batch_id.in_(plan_ids)
                ).delete(synchronize_session=False)
                db.query(AssignmentBatch).filter(
                    AssignmentBatch.id.in_(plan_ids)
                ).delete(synchronize_session=False)
            if student_ids:
                db.query(Student).filter(
                    Student.id.in_(student_ids)
                ).delete(synchronize_session=False)
            if family_ids:
                db.query(FamilyMember).filter(
                    FamilyMember.family_id.in_(family_ids)
                ).delete(synchronize_session=False)
                db.query(Family).filter(
                    Family.id.in_(family_ids)
                ).delete(synchronize_session=False)
            if user_ids:
                db.query(User).filter(User.id.in_(user_ids)).delete(
                    synchronize_session=False
                )
            db.commit()
        for storage_path in storage_paths:
            if storage_path.is_file():
                storage_path.unlink()


def bind_student_to_parent_family(fixture):
    parent = fixture["login"]("parent", "parent")
    parent_headers = {"Authorization": f"Bearer {parent['token']}"}
    parent_context = unwrap(client.get("/api/v1/auth/me", headers=parent_headers))
    family_id = parent_context["family"]["id"]
    assert parent_context["students"] == []
    fixture["family_ids"].add(family_id)

    invite = unwrap(client.post("/api/v1/families/invite-code", headers=parent_headers))
    student = fixture["login"]("student", "student")
    student_headers = {"Authorization": f"Bearer {student['token']}"}
    unwrap(client.post("/api/v1/families/join", headers=student_headers, json={
        "invite_code": invite["invite_code"],
    }))
    parent_context = unwrap(client.get("/api/v1/auth/me", headers=parent_headers))
    student_id = parent_context["students"][0]["id"]
    fixture["student_ids"].add(student_id)
    return parent, parent_headers, student, student_headers, student_id


def create_confirmable_plan(fixture, student_id: int) -> int:
    with SessionLocal() as db:
        plan = AssignmentBatch(
            student_id=student_id,
            title=fixture["marker"],
            start_date=date.today(),
            end_date=date.today(),
            status="pending_confirm",
        )
        db.add(plan)
        db.flush()
        item = AssignmentItem(
            assignment_batch_id=plan.id,
            subject="数学",
            title=fixture["marker"],
            source_text="口算 10 道",
            total_quantity=1,
            unit="项",
            estimated_minutes_total=30,
        )
        db.add(item)
        db.flush()
        task = DailyTask(
            student_id=student_id,
            assignment_batch_id=plan.id,
            assignment_item_id=item.id,
            task_date=date.today(),
            subject=item.subject,
            title=item.title,
            estimated_minutes=30,
        )
        db.add(task)
        db.commit()
        fixture["plan_ids"].add(plan.id)
        fixture["task_ids"].add(task.id)
        return plan.id


def test_confirming_plan_notifies_bound_student(notification_fixture):
    fixture = notification_fixture
    _parent, parent_headers, student, student_headers, student_id = bind_student_to_parent_family(fixture)
    plan_id = create_confirmable_plan(fixture, student_id)

    unwrap(client.post(f"/api/v1/plans/{plan_id}/confirm", headers=parent_headers, json={}))

    notifications = unwrap(client.get(
        "/api/v1/notifications?status=pending",
        headers=student_headers,
    ))
    assert len(notifications) == 1
    assert notifications[0]["type"] == "assignment_updated"
    assert notifications[0]["student_id"] == student_id
    assert fixture["marker"] in notifications[0]["content"]

    with SessionLocal() as db:
        assert db.query(Notification).filter(
            Notification.user_id == student["user"]["id"],
            Notification.type == "assignment_updated",
        ).count() == 1


def test_completing_submission_notifies_family_guardians(notification_fixture):
    fixture = notification_fixture
    parent, parent_headers, _student, student_headers, student_id = bind_student_to_parent_family(fixture)
    plan_id = create_confirmable_plan(fixture, student_id)
    unwrap(client.post(f"/api/v1/plans/{plan_id}/confirm", headers=parent_headers, json={}))

    today = unwrap(client.get(
        f"/api/v1/tasks/today?student_id={student_id}&target_date={date.today()}",
        headers=student_headers,
    ))
    task_id = today["tasks"][0]["id"]
    submission = unwrap(client.post("/api/v1/submissions", headers=student_headers, json={
        "daily_task_id": task_id,
        "submission_type": "photo",
    }))
    fixture["submission_ids"].add(submission["submission_id"])
    unwrap(client.post(
        f"/api/v1/submissions/{submission['submission_id']}/media",
        headers=student_headers,
        data={"media_type": "image", "purpose": "homework", "sort_order": "0"},
        files={"file": ("homework.jpg", BytesIO(b"homework"), "image/jpeg")},
    ))

    unwrap(client.post(
        f"/api/v1/submissions/{submission['submission_id']}/complete",
        headers=student_headers,
    ))

    notifications = unwrap(client.get(
        "/api/v1/notifications?status=pending",
        headers=parent_headers,
    ))
    assert any(
        item["type"] == "submission_uploaded"
        and item["student_id"] == student_id
        and fixture["marker"] in item["content"]
        for item in notifications
    )

    with SessionLocal() as db:
        assert db.query(Notification).filter(
            Notification.user_id == parent["user"]["id"],
            Notification.type == "submission_uploaded",
        ).count() == 1
