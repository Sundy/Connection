from datetime import date, timedelta
from io import BytesIO
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app.core.database import init_db
from backend.app.core.database import SessionLocal
from backend.app.main import app
from backend.app.models import FamilyMember, Student


init_db()
client = TestClient(app)


def unwrap(response):
    assert response.status_code < 300, response.text
    payload = response.json()
    assert payload["code"] == 0, payload
    return payload["data"]


def test_homework_v1_flow():
    login = unwrap(client.post("/api/v1/auth/wechat-login", json={"code": "pytest-parent", "role": "parent"}))
    headers = {"Authorization": f"Bearer {login['token']}"}

    me = unwrap(client.get("/api/v1/auth/me", headers=headers))
    student_id = me["students"][0]["id"]
    today = date.today()

    batch = unwrap(client.post("/api/v1/import-batches", headers=headers, json={
        "student_id": student_id,
        "title": "寒假作业计划",
        "period_type": "winter_vacation",
        "start_date": today.isoformat(),
        "end_date": (today + timedelta(days=7)).isoformat(),
        "raw_text": "数学2张卷子，语文2篇作文，英语20个单词"
    }))

    uploaded = unwrap(client.post(
        f"/api/v1/import-batches/{batch['id']}/files",
        headers=headers,
        data={"file_type": "screenshot", "sort_order": "1"},
        files={"file": ("homework.txt", BytesIO(b"homework"), "text/plain")},
    ))
    assert uploaded["parse_status"] == "pending"

    unwrap(client.post(f"/api/v1/import-batches/{batch['id']}/parse", headers=headers))
    parsed = unwrap(client.get(f"/api/v1/import-batches/{batch['id']}", headers=headers))
    assert parsed["status"] == "parsed"

    plan = unwrap(client.post(f"/api/v1/plans/from-import/{batch['id']}/generate", headers=headers))
    plan_id = plan["assignment_batch_id"]
    draft = unwrap(client.get(f"/api/v1/plans/{plan_id}/draft", headers=headers))
    assert draft["assignment_items"]

    confirmed = unwrap(client.post(f"/api/v1/plans/{plan_id}/confirm", headers=headers, json={}))
    assert confirmed["status"] == "active"

    tasks = unwrap(client.get(f"/api/v1/tasks/today?student_id={student_id}", headers=headers))
    assert tasks["tasks"]
    task_id = tasks["tasks"][0]["id"]

    session = unwrap(client.post("/api/v1/study-sessions/start", headers=headers, json={"daily_task_id": task_id}))
    submission = unwrap(client.post("/api/v1/submissions", headers=headers, json={
        "daily_task_id": task_id,
        "submission_type": "photo",
        "linked_study_session_id": session["session_id"],
        "student_note": "第3题不确定"
    }))
    unwrap(client.post(
        f"/api/v1/submissions/{submission['submission_id']}/media",
        headers=headers,
        data={"media_type": "image", "sort_order": "1"},
        files={"file": ("page.jpg", BytesIO(b"fake-image"), "image/jpeg")},
    ))
    unwrap(client.post(f"/api/v1/submissions/{submission['submission_id']}/complete", headers=headers))

    result = unwrap(client.get(f"/api/v1/results/tasks/{task_id}", headers=headers))
    assert result["result"]["completion_score"] > 0
    assert result["questions"]


def test_family_invite_supports_multiple_guardians_and_students():
    suffix = uuid4().hex
    first_parent = unwrap(client.post("/api/v1/auth/wechat-login", json={"code": f"family-parent-a-{suffix}", "role": "parent"}))
    first_headers = {"Authorization": f"Bearer {first_parent['token']}"}

    first_context = unwrap(client.get("/api/v1/auth/me", headers=first_headers))
    family_id = first_context["family"]["id"]
    default_student_id = first_context["students"][0]["id"]

    second_student = unwrap(client.post("/api/v1/students", headers=first_headers, json={
        "name": "二宝",
        "grade": "一年级",
        "school": "实验小学"
    }))
    assert second_student["name"] == "二宝"

    invite = unwrap(client.post("/api/v1/families/invite-code", headers=first_headers))
    assert invite["family_id"] == family_id
    assert invite["invite_code"]

    second_parent = unwrap(client.post("/api/v1/auth/wechat-login", json={"code": f"family-parent-b-{suffix}", "role": "parent"}))
    second_headers = {"Authorization": f"Bearer {second_parent['token']}"}
    joined_parent = unwrap(client.post("/api/v1/families/join", headers=second_headers, json={
        "invite_code": invite["invite_code"]
    }))
    assert joined_parent["family"]["id"] == family_id

    second_parent_context = unwrap(client.get("/api/v1/auth/me", headers=second_headers))
    assert second_parent_context["family"]["id"] == family_id
    assert {student["name"] for student in second_parent_context["students"]} >= {"默认学生", "二宝"}
    assert len([member for member in second_parent_context["members"] if member["relation"] == "guardian"]) == 2

    student_login = unwrap(client.post("/api/v1/auth/wechat-login", json={"code": f"family-student-a-{suffix}", "role": "student"}))
    student_headers = {"Authorization": f"Bearer {student_login['token']}"}
    joined_student = unwrap(client.post("/api/v1/families/join", headers=student_headers, json={
        "invite_code": invite["invite_code"],
        "student_id": default_student_id
    }))
    assert joined_student["family"]["id"] == family_id

    student_context = unwrap(client.get("/api/v1/auth/me", headers=student_headers))
    assert student_context["family"]["id"] == family_id
    assert any(member["user_id"] == student_login["user"]["id"] and member["relation"] == "student" for member in student_context["members"])

    with SessionLocal() as db:
        bound_student = db.get(Student, default_student_id)
        assert bound_student.user_id == student_login["user"]["id"]
        active_member = db.query(FamilyMember).filter(
            FamilyMember.user_id == student_login["user"]["id"],
            FamilyMember.status == "active",
        ).one()
        assert active_member.family_id == family_id
