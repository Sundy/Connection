from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app.core.database import init_db
from backend.app.core.database import SessionLocal
from backend.app.main import app
from backend.app.models import AssignmentBatch, AssignmentItem, CorrectionResult, DailyTask, Family, FamilyMember, ImportFile, QuestionResult, Student, Submission, SubmissionMedia


init_db()
client = TestClient(app)


def unwrap(response):
    assert response.status_code < 300, response.text
    payload = response.json()
    assert payload["code"] == 0, payload
    return payload["data"]


def test_homework_v1_flow(monkeypatch):
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
    submission_detail = unwrap(client.get(f"/api/v1/submissions/{submission['submission_id']}", headers=headers))
    assert submission_detail["homework_media_count"] == 1
    monkeypatch.setattr(
        "backend.app.services.correction_service.build_ai_correction_payload",
        lambda db, submission: {
            "completion_score": 90,
            "accuracy_score": 85,
            "confidence_score": 0.9,
            "summary": "已完成真实批改测试",
            "needs_review": False,
            "questions": [{
                "question_no": "1",
                "question_type": "calculation",
                "recognized_answer": "42",
                "expected_answer": "42",
                "is_correct": True,
                "score": 1,
                "explanation": "回答正确",
                "confidence_score": 0.95,
            }],
        },
    )
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

    invite = unwrap(client.post("/api/v1/families/invite-code", headers=first_headers))
    assert invite["family_id"] == family_id
    assert invite["invite_code"]

    second_parent = unwrap(client.post("/api/v1/auth/wechat-login", json={"code": f"family-parent-b-{suffix}", "role": "parent"}))
    second_headers = {"Authorization": f"Bearer {second_parent['token']}"}
    parent_join = client.post("/api/v1/families/join", headers=second_headers, json={
        "invite_code": invite["invite_code"]
    })
    assert parent_join.status_code == 400
    assert parent_join.json()["detail"] == "Parents should share invite code with students instead"

    student_login = unwrap(client.post("/api/v1/auth/wechat-login", json={"code": f"family-student-a-{suffix}", "role": "student"}))
    student_headers = {"Authorization": f"Bearer {student_login['token']}"}
    joined_student = unwrap(client.post("/api/v1/families/join", headers=student_headers, json={
        "invite_code": invite["invite_code"]
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


def test_profile_update_student_updates_bound_student_fields():
    suffix = uuid4().hex
    parent_login = unwrap(client.post("/api/v1/auth/wechat-login", json={"code": f"profile-parent-{suffix}", "role": "parent"}))
    parent_headers = {"Authorization": f"Bearer {parent_login['token']}"}
    invite = unwrap(client.post("/api/v1/families/invite-code", headers=parent_headers))

    student_login = unwrap(client.post("/api/v1/auth/wechat-login", json={"code": f"profile-student-{suffix}", "role": "student"}))
    student_headers = {"Authorization": f"Bearer {student_login['token']}"}
    unwrap(client.post("/api/v1/families/join", headers=student_headers, json={
        "invite_code": invite["invite_code"]
    }))

    updated = unwrap(client.post("/api/v1/auth/profile", headers=student_headers, json={
        "nickname": "小明",
        "grade": "三年级",
        "school": "实验小学"
    }))

    assert updated["user"]["nickname"] == "小明"
    bound_student = next(student for student in updated["students"] if student["user_id"] == student_login["user"]["id"])
    assert bound_student["name"] == "小明"
    assert bound_student["grade"] == "三年级"
    assert bound_student["school"] == "实验小学"


def test_student_endpoints_use_latest_active_family_membership():
    login = unwrap(client.post("/api/v1/auth/wechat-login", json={
        "code": f"active-family-student-{uuid4().hex}",
        "role": "parent",
    }))
    headers = {"Authorization": f"Bearer {login['token']}"}
    user_id = login["user"]["id"]

    with SessionLocal() as db:
        old_member = db.query(FamilyMember).filter(FamilyMember.user_id == user_id).one()
        old_family_id = old_member.family_id
        old_member.status = "inactive"
        new_family = Family(name="当前家庭", created_by=user_id)
        db.add(new_family)
        db.flush()
        db.add(FamilyMember(family_id=new_family.id, user_id=user_id, relation="guardian", status="active"))
        db.commit()
        new_family_id = new_family.id

    created = unwrap(client.post("/api/v1/students", headers=headers, json={
        "name": "当前家庭孩子",
        "grade": "三年级",
    }))
    listed = unwrap(client.get("/api/v1/students", headers=headers))

    assert [student["id"] for student in listed] == [created["id"]]
    with SessionLocal() as db:
        student = db.get(Student, created["id"])
        assert student.family_id == new_family_id
        assert student.family_id != old_family_id


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
    unwrap(client.get(f"/api/v1/plans/{plan['assignment_batch_id']}/draft", headers=headers))
    unwrap(client.post(f"/api/v1/plans/{plan['assignment_batch_id']}/confirm", headers=headers, json={}))

    tasks = unwrap(client.get(f"/api/v1/tasks/today?student_id={student_id}", headers=headers))
    task = tasks["tasks"][0]
    detail = unwrap(client.get(f"/api/v1/tasks/{task['id']}", headers=headers))

    assert "第1页到第2页" in task["source_text"]
    assert "第1页到第2页" in detail["source_text"]
    assert task["planned_quantity"] == detail["planned_quantity"]
    assert detail["has_answer"] is False


def test_task_payload_processing_stage():
    login = unwrap(client.post("/api/v1/auth/wechat-login", json={
        "code": f"task-stage-{uuid4().hex}",
        "role": "parent",
    }))
    headers = {"Authorization": f"Bearer {login['token']}"}
    context = unwrap(client.get("/api/v1/auth/me", headers=headers))
    student_id = context["students"][0]["id"]
    with SessionLocal() as db:
        plan = AssignmentBatch(student_id=student_id, title="阶段显示", status="active")
        db.add(plan)
        db.flush()
        item = AssignmentItem(assignment_batch_id=plan.id, subject="数学", title="阶段显示")
        db.add(item)
        db.flush()
        task = DailyTask(
            student_id=student_id,
            assignment_batch_id=plan.id,
            assignment_item_id=item.id,
            task_date=date.today(),
            subject="数学",
            title="阶段显示",
            status="correcting",
        )
        db.add(task)
        db.flush()
        db.add(Submission(
            daily_task_id=task.id,
            student_id=student_id,
            submission_type="photo",
            status="processing",
            processing_stage="annotating",
            processing_message="正在生成卷面批注",
        ))
        db.commit()
        task_id = task.id

    task_payload_result = unwrap(client.get(f"/api/v1/tasks/{task_id}", headers=headers))
    assert task_payload_result["processing_stage"] == "annotating"


def test_today_tasks_returns_all_active_plans_for_student_date():
    login = unwrap(client.post("/api/v1/auth/wechat-login", json={"code": f"latest-plan-{uuid4().hex}", "role": "parent"}))
    headers = {"Authorization": f"Bearer {login['token']}"}
    me = unwrap(client.get("/api/v1/auth/me", headers=headers))
    student_id = me["students"][0]["id"]
    today = date.today()

    with SessionLocal() as db:
        old_plan = AssignmentBatch(student_id=student_id, title="旧计划", status="active", start_date=today, end_date=today)
        new_plan = AssignmentBatch(student_id=student_id, title="新计划", status="active", start_date=today, end_date=today)
        db.add_all([old_plan, new_plan])
        db.flush()
        old_item = AssignmentItem(assignment_batch_id=old_plan.id, subject="数学", title="旧任务", source_text="旧计划内容")
        new_item = AssignmentItem(assignment_batch_id=new_plan.id, subject="语文", title="新任务", source_text="新计划内容")
        db.add_all([old_item, new_item])
        db.flush()
        db.add(DailyTask(
            student_id=student_id,
            assignment_batch_id=old_plan.id,
            assignment_item_id=old_item.id,
            task_date=today,
            subject="数学",
            title="旧计划任务",
        ))
        db.add(DailyTask(
            student_id=student_id,
            assignment_batch_id=new_plan.id,
            assignment_item_id=new_item.id,
            task_date=today,
            subject="语文",
            title="新计划任务",
        ))
        db.commit()

    tasks = unwrap(client.get(f"/api/v1/tasks/today?student_id={student_id}", headers=headers))

    assert [task["title"] for task in tasks["tasks"]] == ["旧计划任务", "新计划任务"]
    assert tasks["summary"]["total_tasks"] == 2


def test_confirm_plan_moves_first_tasks_to_start_date_when_start_day_is_empty():
    login = unwrap(client.post("/api/v1/auth/wechat-login", json={"code": f"confirm-start-day-{uuid4().hex}", "role": "parent"}))
    headers = {"Authorization": f"Bearer {login['token']}"}
    me = unwrap(client.get("/api/v1/auth/me", headers=headers))
    student_id = me["students"][0]["id"]
    start = date.today()

    with SessionLocal() as db:
        plan = AssignmentBatch(student_id=student_id, title="补齐首日", status="pending_confirm", start_date=start, end_date=start + timedelta(days=2))
        db.add(plan)
        db.flush()
        item = AssignmentItem(assignment_batch_id=plan.id, subject="数学", title="口算")
        db.add(item)
        db.flush()
        task = DailyTask(
            student_id=student_id,
            assignment_batch_id=plan.id,
            assignment_item_id=item.id,
            task_date=start + timedelta(days=1),
            subject="数学",
            title="口算",
        )
        db.add(task)
        db.commit()
        plan_id = plan.id

    unwrap(client.post(f"/api/v1/plans/{plan_id}/confirm", headers=headers, json={}))

    with SessionLocal() as db:
        task = db.query(DailyTask).filter(DailyTask.assignment_batch_id == plan_id).one()
        assert task.task_date == start


def test_target_date_returns_subject_summary_and_calendar_date_summary():
    login = unwrap(client.post("/api/v1/auth/wechat-login", json={"code": f"subject-summary-{uuid4().hex}", "role": "parent"}))
    headers = {"Authorization": f"Bearer {login['token']}"}
    me = unwrap(client.get("/api/v1/auth/me", headers=headers))
    student_id = me["students"][0]["id"]
    tomorrow = date.today() + timedelta(days=1)

    with SessionLocal() as db:
        plan = AssignmentBatch(
            student_id=student_id,
            title="科目汇总计划",
            status="active",
            start_date=tomorrow,
            end_date=tomorrow,
        )
        db.add(plan)
        db.flush()
        math_item = AssignmentItem(assignment_batch_id=plan.id, subject="数学", title="数学任务")
        english_item = AssignmentItem(assignment_batch_id=plan.id, subject="英语", title="英语任务")
        db.add_all([math_item, english_item])
        db.flush()
        db.add_all([
            DailyTask(student_id=student_id, assignment_batch_id=plan.id, assignment_item_id=math_item.id, task_date=tomorrow, subject="数学", title="口算", status="corrected"),
            DailyTask(student_id=student_id, assignment_batch_id=plan.id, assignment_item_id=math_item.id, task_date=tomorrow, subject="数学", title="应用题", status="todo"),
            DailyTask(student_id=student_id, assignment_batch_id=plan.id, assignment_item_id=english_item.id, task_date=tomorrow, subject="英语", title="朗读", status="todo"),
        ])
        db.commit()
        plan_id = plan.id

    payload = unwrap(client.get(
        f"/api/v1/tasks/today?student_id={student_id}&target_date={tomorrow.isoformat()}",
        headers=headers,
    ))
    assert payload["date"] == tomorrow.isoformat()
    assert payload["subject_summary"] == [
        {"subject": "数学", "total_tasks": 2, "completed_tasks": 1},
        {"subject": "英语", "total_tasks": 1, "completed_tasks": 0},
    ]

    calendar = unwrap(client.get(f"/api/v1/plans/{plan_id}/calendar", headers=headers))
    assert len(calendar["items"]) == 3
    assert calendar["plan"] == {
        "id": plan_id,
        "start_date": tomorrow.isoformat(),
        "end_date": tomorrow.isoformat(),
    }
    assert calendar["date_summary"] == [{
        "date": tomorrow.isoformat(),
        "total_tasks": 3,
        "completed_tasks": 1,
        "subjects": payload["subject_summary"],
    }]


def test_import_batch_raw_text_can_be_added_from_upload_step():
    login = unwrap(client.post("/api/v1/auth/wechat-login", json={"code": f"import-text-{uuid4().hex}", "role": "parent"}))
    headers = {"Authorization": f"Bearer {login['token']}"}
    me = unwrap(client.get("/api/v1/auth/me", headers=headers))
    student_id = me["students"][0]["id"]
    today = date.today()

    batch = unwrap(client.post("/api/v1/import-batches", headers=headers, json={
        "student_id": student_id,
        "title": f"{today.isoformat()} 作业",
        "period_type": "daily",
        "start_date": today.isoformat(),
        "end_date": today.isoformat(),
        "raw_text": ""
    }))
    updated = unwrap(client.patch(f"/api/v1/import-batches/{batch['id']}", headers=headers, json={
        "raw_text": "老师补充：数学口算20道"
    }))

    assert updated["raw_text"] == "老师补充：数学口算20道"


def test_uploaded_import_and_submission_files_are_saved_to_oss_with_local_cache(monkeypatch):
    def fake_upload(file_path, object_key=None):
        return f"https://oss.example.com/{object_key}"

    monkeypatch.setattr("backend.app.api.routers.imports.upload_file_to_oss", fake_upload)
    monkeypatch.setattr("backend.app.api.routers.submissions.upload_file_to_oss", fake_upload)

    login = unwrap(client.post("/api/v1/auth/wechat-login", json={"code": f"upload-path-{uuid4().hex}", "role": "parent"}))
    headers = {"Authorization": f"Bearer {login['token']}"}
    me = unwrap(client.get("/api/v1/auth/me", headers=headers))
    student_id = me["students"][0]["id"]
    today = date.today()
    today_key = today.isoformat()

    batch = unwrap(client.post("/api/v1/import-batches", headers=headers, json={
        "student_id": student_id,
        "title": f"{today.isoformat()} 作业",
        "start_date": today.isoformat(),
        "end_date": today.isoformat(),
        "raw_text": ""
    }))
    uploaded = unwrap(client.post(
        f"/api/v1/import-batches/{batch['id']}/files",
        headers=headers,
        data={"file_type": "file", "sort_order": "0"},
        files={"file": ("paper.txt", BytesIO("数学口算20道".encode("utf-8")), "text/plain")},
    ))
    imported_preview = client.get(uploaded["preview_url"], headers=headers, follow_redirects=False)
    assert imported_preview.status_code in {302, 307}
    assert imported_preview.headers["location"].startswith(f"https://oss.example.com/connection/imports/{today_key}/batch-")
    assert "/paper-" in imported_preview.headers["location"]

    with SessionLocal() as db:
        import_file = db.get(ImportFile, uploaded["file_id"])
        import_path = Path(import_file.storage_path)

    assert import_file.file_url.startswith(f"https://oss.example.com/connection/imports/{today_key}/batch-")
    assert "/paper-" in import_file.file_url
    assert import_path.exists()
    assert import_path.is_absolute()
    assert "backend/uploads/imports" in import_path.as_posix()

    unwrap(client.post(f"/api/v1/import-batches/{batch['id']}/parse", headers=headers))
    plan = unwrap(client.post(f"/api/v1/plans/from-import/{batch['id']}/generate", headers=headers))
    unwrap(client.post(f"/api/v1/plans/{plan['assignment_batch_id']}/confirm", headers=headers, json={}))
    tasks = unwrap(client.get(f"/api/v1/tasks/today?student_id={student_id}", headers=headers))
    submission = unwrap(client.post("/api/v1/submissions", headers=headers, json={
        "daily_task_id": tasks["tasks"][0]["id"],
        "submission_type": "photo"
    }))
    media = unwrap(client.post(
        f"/api/v1/submissions/{submission['submission_id']}/media",
        headers=headers,
        data={"media_type": "image", "purpose": "homework", "sort_order": "0"},
        files={"file": ("answer.jpg", BytesIO(b"fake-image"), "image/jpeg")},
    ))

    with SessionLocal() as db:
        media_row = db.get(SubmissionMedia, media["media_id"])
        media_path = Path(media_row.storage_path)

    assert media_row.file_url.startswith(f"https://oss.example.com/connection/submissions/{today_key}/submission-")
    assert "/homework/answer-" in media_row.file_url
    assert media_path.exists()
    assert media_path.is_absolute()
    assert "backend/uploads/submissions" in media_path.as_posix()


def test_imported_files_generate_one_assignment_item_per_file_with_preview_url():
    login = unwrap(client.post("/api/v1/auth/wechat-login", json={"code": f"file-plan-{uuid4().hex}", "role": "parent"}))
    headers = {"Authorization": f"Bearer {login['token']}"}
    me = unwrap(client.get("/api/v1/auth/me", headers=headers))
    student_id = me["students"][0]["id"]
    today = date.today()

    batch = unwrap(client.post("/api/v1/import-batches", headers=headers, json={
        "student_id": student_id,
        "title": f"{today.isoformat()} 作业",
        "period_type": "daily",
        "start_date": today.isoformat(),
        "end_date": today.isoformat(),
        "raw_text": ""
    }))

    with SessionLocal() as db:
        db.add(ImportFile(
            import_batch_id=batch["id"],
            file_name="数学周测卷.pdf",
            file_type="pdf",
            file_url="/tmp/math.pdf",
            extracted_text="数学第二周巩固练习，口算和应用题。",
            parse_status="success",
            sort_order=0,
        ))
        db.add(ImportFile(
            import_batch_id=batch["id"],
            file_name="语文阅读.docx",
            file_type="docx",
            file_url="/tmp/chinese.docx",
            extracted_text="语文阅读理解专项练习。",
            parse_status="success",
            sort_order=1,
        ))
        db.commit()

    plan = unwrap(client.post(f"/api/v1/plans/from-import/{batch['id']}/generate", headers=headers))
    draft = unwrap(client.get(f"/api/v1/plans/{plan['assignment_batch_id']}/draft", headers=headers))

    assert len(draft["assignment_items"]) == 2
    assert [item["title"] for item in draft["assignment_items"]] == ["数学周测卷", "语文阅读"]
    assert {item["subject"] for item in draft["assignment_items"]} == {"数学", "语文"}
    assert all(item["total_quantity"] == 1 for item in draft["assignment_items"])
    assert all(item["unit"] == "份" for item in draft["assignment_items"])
    assert all(item["source_file"]["preview_url"].endswith("/preview") for item in draft["assignment_items"])
    assert all(item["source_file"]["file_url"] for item in draft["assignment_items"])
    assert all(item["source_text"] == "" for item in draft["assignment_items"])
    assert len(draft["daily_preview"]) == 2

    unwrap(client.post(f"/api/v1/plans/{plan['assignment_batch_id']}/confirm", headers=headers, json={}))
    today_tasks = unwrap(client.get(f"/api/v1/tasks/today?student_id={student_id}", headers=headers))
    first_task = today_tasks["tasks"][0]
    task_detail = unwrap(client.get(f"/api/v1/tasks/{first_task['id']}", headers=headers))
    calendar = unwrap(client.get(f"/api/v1/plans/{plan['assignment_batch_id']}/calendar", headers=headers))
    result = unwrap(client.get(f"/api/v1/results/tasks/{first_task['id']}", headers=headers))

    assert first_task["source_file"]["file_name"] == "数学周测卷.pdf"
    assert first_task["source_text"] == ""
    assert first_task["source_file"]["file_url"]
    assert first_task["source_file"]["preview_url"].endswith("/preview")
    assert task_detail["source_file"]["file_name"] == "数学周测卷.pdf"
    assert calendar["items"][0]["source_file"]["file_name"] == "数学周测卷.pdf"
    assert result["task"]["source_file"]["file_name"] == "数学周测卷.pdf"


def test_submission_rejects_student_answer_and_requires_homework_media():
    login = unwrap(client.post("/api/v1/auth/wechat-login", json={"code": f"submission-answer-{uuid4().hex}", "role": "parent"}))
    headers = {"Authorization": f"Bearer {login['token']}"}
    me = unwrap(client.get("/api/v1/auth/me", headers=headers))
    student_id = me["students"][0]["id"]
    today = date.today()

    batch = unwrap(client.post("/api/v1/import-batches", headers=headers, json={
        "student_id": student_id,
        "title": f"{today.isoformat()} 作业",
        "start_date": today.isoformat(),
        "end_date": today.isoformat(),
        "raw_text": "数学口算20道"
    }))
    unwrap(client.post(f"/api/v1/import-batches/{batch['id']}/parse", headers=headers))
    plan = unwrap(client.post(f"/api/v1/plans/from-import/{batch['id']}/generate", headers=headers))
    unwrap(client.post(f"/api/v1/plans/{plan['assignment_batch_id']}/confirm", headers=headers, json={}))
    tasks = unwrap(client.get(f"/api/v1/tasks/today?student_id={student_id}", headers=headers))
    task_id = tasks["tasks"][0]["id"]

    answer_response = client.post("/api/v1/submissions", headers=headers, json={
        "daily_task_id": task_id,
        "submission_type": "photo",
        "answer_text": "1.A 2.B 3.C"
    })
    assert answer_response.status_code == 422

    submission = unwrap(client.post("/api/v1/submissions", headers=headers, json={
        "daily_task_id": task_id,
        "submission_type": "photo",
    }))
    complete_response = client.post(
        f"/api/v1/submissions/{submission['submission_id']}/complete",
        headers=headers,
    )
    assert complete_response.status_code == 422
    detail = unwrap(client.get(f"/api/v1/submissions/{submission['submission_id']}", headers=headers))
    assert detail["status"] == "draft"


def test_parent_can_confirm_or_request_resubmission_for_ai_review():
    login = unwrap(client.post("/api/v1/auth/wechat-login", json={"code": f"review-result-{uuid4().hex}", "role": "parent"}))
    headers = {"Authorization": f"Bearer {login['token']}"}
    me = unwrap(client.get("/api/v1/auth/me", headers=headers))
    student_id = me["students"][0]["id"]
    today = date.today()

    with SessionLocal() as db:
        plan = AssignmentBatch(student_id=student_id, title="复核计划", status="active", start_date=today, end_date=today)
        db.add(plan)
        db.flush()
        item = AssignmentItem(assignment_batch_id=plan.id, subject="语文", title="阅读")
        db.add(item)
        db.flush()
        task = DailyTask(student_id=student_id, assignment_batch_id=plan.id, assignment_item_id=item.id, task_date=today, subject="语文", title="阅读", status="needs_review")
        db.add(task)
        db.flush()
        submission = Submission(daily_task_id=task.id, student_id=student_id, submission_type="photo", status="needs_review")
        db.add(submission)
        db.flush()
        result = CorrectionResult(submission_id=submission.id, daily_task_id=task.id, completion_score=85, confidence_score=0.7, summary="待复核", needs_review=True, review_reason="有一题无法确认")
        db.add(result)
        db.commit()
        task_id = task.id

    confirmed = unwrap(client.post(f"/api/v1/results/tasks/{task_id}/review", headers=headers, json={"action": "confirm"}))
    assert confirmed["submission_status"] == "corrected"
    assert confirmed["review_status"] == "confirmed"

    with SessionLocal() as db:
        task = db.get(DailyTask, task_id)
        submission = db.query(Submission).filter(Submission.daily_task_id == task_id).one()
        result = db.query(CorrectionResult).filter(CorrectionResult.daily_task_id == task_id).one()
        task.status = "needs_review"
        submission.status = "needs_review"
        result.needs_review = True
        result.review_status = "pending"
        db.commit()

    requested = unwrap(client.post(f"/api/v1/results/tasks/{task_id}/review", headers=headers, json={"action": "resubmit", "note": "照片不清楚"}))
    assert requested["submission_status"] == "resubmit_required"
    assert requested["review_status"] == "resubmit_required"


def test_teacher_style_pages_are_ordered_and_protected(tmp_path):
    owner = unwrap(client.post("/api/v1/auth/wechat-login", json={"code": f"page-owner-{uuid4().hex}", "role": "parent"}))
    owner_headers = {"Authorization": f"Bearer {owner['token']}"}
    owner_context = unwrap(client.get("/api/v1/auth/me", headers=owner_headers))
    student_id = owner_context["students"][0]["id"]
    other = unwrap(client.post("/api/v1/auth/wechat-login", json={"code": f"page-other-{uuid4().hex}", "role": "parent"}))
    other_parent_headers = {"Authorization": f"Bearer {other['token']}"}
    first_file = tmp_path / "page-one.jpg"
    second_file = tmp_path / "page-two.jpg"
    ungraded_file = tmp_path / "page-three.jpg"
    first_file.write_bytes(b"page-one")
    second_file.write_bytes(b"page-two")
    ungraded_file.write_bytes(b"page-three")

    with SessionLocal() as db:
        plan = AssignmentBatch(student_id=student_id, title="多页卷面", status="active")
        db.add(plan)
        db.flush()
        item = AssignmentItem(assignment_batch_id=plan.id, subject="语文", title="练习册")
        db.add(item)
        db.flush()
        task = DailyTask(
            student_id=student_id,
            assignment_batch_id=plan.id,
            assignment_item_id=item.id,
            task_date=date.today(),
            subject="语文",
            title="多页练习册",
            status="corrected",
        )
        db.add(task)
        db.flush()
        submission = Submission(
            daily_task_id=task.id,
            student_id=student_id,
            submission_type="photo",
            status="corrected",
            processing_stage="corrected",
            processing_message="批改完成",
        )
        db.add(submission)
        db.flush()
        page_with_sort_20 = SubmissionMedia(
            submission_id=submission.id,
            media_type="image",
            purpose="homework",
            file_url=str(second_file),
            storage_path=str(second_file),
            sort_order=20,
        )
        page_with_sort_10 = SubmissionMedia(
            submission_id=submission.id,
            media_type="image",
            purpose="homework",
            file_url=str(first_file),
            storage_path=str(first_file),
            sort_order=10,
        )
        page_with_sort_30 = SubmissionMedia(
            submission_id=submission.id,
            media_type="image",
            purpose="homework",
            file_url=str(ungraded_file),
            storage_path=str(ungraded_file),
            sort_order=30,
        )
        db.add_all([page_with_sort_20, page_with_sort_10, page_with_sort_30])
        db.flush()
        correction = CorrectionResult(
            submission_id=submission.id,
            daily_task_id=task.id,
            completion_score=88,
            accuracy_score=75,
            confidence_score=0.9,
            summary="多页批改完成",
        )
        db.add(correction)
        db.flush()
        db.add_all([
            QuestionResult(
                correction_result_id=correction.id,
                source_media_id=page_with_sort_10.id,
                question_no="1",
                is_correct=True,
                annotations_json='[{"kind":"correct_tick","x":0.8,"y":0.2,"width":0.1,"height":0.1,"text":null,"confidence":0.9}]',
            ),
            QuestionResult(
                correction_result_id=correction.id,
                source_media_id=page_with_sort_20.id,
                section_no="四",
                question_no="12",
                subquestion_no="1",
                is_correct=True,
                annotations_json='[{"kind":"correct_tick","x":0.1,"y":0.5,"width":0.1,"height":0.1,"text":null,"confidence":0.9}]',
            ),
            QuestionResult(
                correction_result_id=correction.id,
                source_media_id=page_with_sort_20.id,
                section_no="四",
                question_no="12",
                subquestion_no="2",
                is_correct=False,
                annotations_json='[{"kind":"error_circle","x":0.2,"y":0.5,"width":0.3,"height":0.1,"text":null,"confidence":0.9}]',
            ),
        ])
        db.commit()
        task_id = task.id
        page_with_sort_10_id = page_with_sort_10.id
        page_with_sort_20_id = page_with_sort_20.id
        page_with_sort_30_id = page_with_sort_30.id

    result = unwrap(client.get(f"/api/v1/results/tasks/{task_id}", headers=owner_headers))
    assert result["submission"]["processing_stage"] == "corrected"
    assert [page["media_id"] for page in result["pages"]] == [
        page_with_sort_10_id,
        page_with_sort_20_id,
        page_with_sort_30_id,
    ]
    assert result["pages"][0]["page_number"] == 1
    assert result["pages"][0]["has_correction"] is True
    assert result["pages"][0]["review_message"] is None
    assert result["pages"][0]["questions"][0]["annotations"][0]["kind"] == "correct_tick"
    assert result["pages"][1]["questions"][0]["question_no"] == "12"
    assert result["pages"][1]["questions"][0]["is_correct"] is False
    assert len(result["pages"][1]["questions"][0]["subquestions"]) == 2
    assert [
        annotation["kind"]
        for annotation in result["pages"][1]["questions"][0]["annotations"]
    ] == ["correct_tick", "error_circle"]
    assert result["pages"][1]["summary"] == {
        "correct_question_nos": [],
        "incorrect_question_nos": ["四、12"],
        "review_question_nos": [],
    }
    assert [question["question_no"] for question in result["questions"]] == [
        "1",
        "12",
    ]
    assert result["pages"][2]["has_correction"] is False
    assert result["pages"][2]["review_message"] == (
        "本页未生成批改结果，不能判断为全对，请重新批改或人工复核"
    )

    denied = client.get(f"/api/v1/results/tasks/{task_id}", headers=other_parent_headers)
    assert denied.status_code == 403

    missing_auth = client.get(f"/api/v1/results/tasks/{task_id}")
    assert missing_auth.status_code == 401

    allowed = client.get(f"/api/v1/submissions/media/{page_with_sort_10_id}/content", headers=owner_headers)
    assert allowed.status_code == 200
    assert allowed.content == b"page-one"

    denied_media = client.get(f"/api/v1/submissions/media/{page_with_sort_10_id}/content", headers=other_parent_headers)
    assert denied_media.status_code == 403

    missing_auth_media = client.get(f"/api/v1/submissions/media/{page_with_sort_10_id}/content")
    assert missing_auth_media.status_code == 401
