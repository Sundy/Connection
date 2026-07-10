from datetime import date, timedelta
from io import BytesIO
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app.core.database import init_db
from backend.app.core.database import SessionLocal
from backend.app.main import app
from backend.app.models import Family, FamilyMember, Student


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


def test_mock_wechat_login_reuses_existing_family_for_same_local_account():
    local_openid = f"local-parent-{uuid4().hex}"

    first_login = unwrap(client.post("/api/v1/auth/wechat-login", json={
        "code": f"first-code-{uuid4().hex}",
        "role": "parent",
        "client_openid": local_openid,
    }))
    first_headers = {"Authorization": f"Bearer {first_login['token']}"}
    first_context = unwrap(client.get("/api/v1/auth/me", headers=first_headers))
    first_family_id = first_context["family"]["id"]

    second_login = unwrap(client.post("/api/v1/auth/wechat-login", json={
        "code": f"second-code-{uuid4().hex}",
        "role": "parent",
        "client_openid": local_openid,
    }))
    second_headers = {"Authorization": f"Bearer {second_login['token']}"}
    second_context = unwrap(client.get("/api/v1/auth/me", headers=second_headers))

    assert second_login["user"]["id"] == first_login["user"]["id"]
    assert second_context["family"]["id"] == first_family_id

    with SessionLocal() as db:
        memberships = db.query(FamilyMember).filter(
            FamilyMember.user_id == first_login["user"]["id"],
            FamilyMember.status == "active",
        ).all()
        created_families = db.query(Family).filter(Family.created_by == first_login["user"]["id"]).all()

    assert len(memberships) == 1
    assert len(created_families) == 1


def test_task_detail_includes_assignment_content_and_answer_status():
    login = unwrap(client.post("/api/v1/auth/wechat-login", json={"code": f"task-detail-{uuid4().hex}", "role": "parent"}))
    headers = {"Authorization": f"Bearer {login['token']}"}
    me = unwrap(client.get("/api/v1/auth/me", headers=headers))
    student_id = me["students"][0]["id"]
    today = date.today()

    batch = unwrap(client.post("/api/v1/import-batches", headers=headers, json={
        "student_id": student_id,
        "title": "带详情的作业",
        "start_date": today.isoformat(),
        "end_date": today.isoformat(),
        "raw_text": "数学口算20道，第1页到第2页，要求写出过程"
    }))
    unwrap(client.post(f"/api/v1/import-batches/{batch['id']}/parse", headers=headers))
    plan = unwrap(client.post(f"/api/v1/plans/from-import/{batch['id']}/generate", headers=headers))
    draft = unwrap(client.get(f"/api/v1/plans/{plan['assignment_batch_id']}/draft", headers=headers))
    item_id = draft["assignment_items"][0]["id"]
    unwrap(client.post(f"/api/v1/plans/{plan['assignment_batch_id']}/confirm", headers=headers, json={
        "adjustments": [{"id": item_id, "answer_text": "口算答案：1.A 2.B 3.C"}]
    }))

    tasks = unwrap(client.get(f"/api/v1/tasks/today?student_id={student_id}", headers=headers))
    task = tasks["tasks"][0]
    detail = unwrap(client.get(f"/api/v1/tasks/{task['id']}", headers=headers))

    assert "第1页到第2页" in task["source_text"]
    assert "第1页到第2页" in detail["source_text"]
    assert task["planned_quantity"] == detail["planned_quantity"]
    assert detail["has_answer"] is True
