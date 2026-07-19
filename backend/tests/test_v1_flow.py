import json
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
from threading import Barrier, Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from backend.app.core.database import init_db
from backend.app.core.database import SessionLocal
from backend.app.main import app
from backend.app.models import AssignmentBatch, AssignmentItem, CorrectionResult, DailyTask, Family, FamilyMember, ImportBatch, ImportFile, QuestionResult, Student, Submission, SubmissionMedia, User
from backend.app.services.local_file_service import upload_subdir
from backend.app.services.import_file_service import (
    StagedImportDeleteError,
    delete_staged_import_file,
)
from backend.app.services.answer_matching_service import match_batch_answers
from backend.app.services.planning_service import (
    confirm_plan,
    generate_plan_from_import,
)


init_db()
client = TestClient(app)


def unwrap(response):
    assert response.status_code < 300, response.text
    payload = response.json()
    assert payload["code"] == 0, payload
    return payload["data"]


def create_joined_student_for_parent(parent_headers: dict[str, str], code_prefix: str) -> dict:
    invite = unwrap(client.post("/api/v1/families/invite-code", headers=parent_headers))
    student_login = unwrap(client.post("/api/v1/auth/wechat-login", json={
        "code": f"{code_prefix}-student-{uuid4().hex}",
        "role": "student",
    }))
    student_headers = {"Authorization": f"Bearer {student_login['token']}"}
    unwrap(client.post("/api/v1/families/join", headers=student_headers, json={
        "invite_code": invite["invite_code"],
    }))
    parent_context = unwrap(client.get("/api/v1/auth/me", headers=parent_headers))
    assert len(parent_context["students"]) == 1
    return parent_context["students"][0]


@pytest.fixture
def isolated_import_fixture():
    marker = f"task5-import-{uuid4().hex}"
    user_ids: set[int] = set()
    family_ids: set[int] = set()
    student_ids: set[int] = set()
    batch_ids: set[int] = set()
    plan_ids: set[int] = set()
    storage_paths: set[Path] = set()

    def create_parent(suffix: str) -> SimpleNamespace:
        with SessionLocal() as db:
            user = User(
                openid=f"mock-openid-{marker}-{suffix}",
                role="parent",
                nickname=marker,
            )
            db.add(user)
            db.flush()
            family = Family(name=f"{marker}-{suffix}", created_by=user.id)
            db.add(family)
            db.flush()
            db.add(FamilyMember(
                family_id=family.id,
                user_id=user.id,
                relation="guardian",
                status="active",
            ))
            student = Student(
                family_id=family.id,
                name=f"{marker}-{suffix}",
                grade="四年级",
            )
            db.add(student)
            db.commit()
            user_ids.add(user.id)
            family_ids.add(family.id)
            student_ids.add(student.id)
            return SimpleNamespace(
                user_id=user.id,
                family_id=family.id,
                student_id=student.id,
                headers={"Authorization": f"Bearer dev-token-{user.id}"},
            )

    fixture = SimpleNamespace(
        marker=marker,
        create_parent=create_parent,
        register_batch=lambda batch_id: batch_ids.add(batch_id),
        register_plan=lambda plan_id: plan_ids.add(plan_id),
    )
    try:
        yield fixture
    finally:
        with SessionLocal() as db:
            if batch_ids:
                plan_ids.update(
                    row.id for row in db.query(AssignmentBatch.id).filter(
                        AssignmentBatch.import_batch_id.in_(batch_ids)
                    )
                )
            if student_ids:
                plan_ids.update(
                    row.id for row in db.query(AssignmentBatch.id).filter(
                        AssignmentBatch.student_id.in_(student_ids),
                        AssignmentBatch.title.contains(marker),
                    )
                )
            files = (
                db.query(ImportFile)
                .filter(ImportFile.import_batch_id.in_(batch_ids))
                .all()
                if batch_ids
                else []
            )
            for item in files:
                if item.storage_path:
                    storage_paths.add(Path(item.storage_path))
            file_ids = [item.id for item in files]
            task_ids = [
                row.id
                for row in db.query(DailyTask).filter(
                    DailyTask.assignment_batch_id.in_(plan_ids)
                )
            ] if plan_ids else []
            submission_ids = [
                row.id
                for row in db.query(Submission).filter(
                    Submission.daily_task_id.in_(task_ids)
                )
            ] if task_ids else []
            correction_ids = [
                row.id
                for row in db.query(CorrectionResult).filter(
                    CorrectionResult.daily_task_id.in_(task_ids)
                )
            ] if task_ids else []
            item_ids = [
                row.id
                for row in db.query(AssignmentItem).filter(
                    AssignmentItem.assignment_batch_id.in_(plan_ids)
                )
            ] if plan_ids else []
            member_ids = [
                row.id
                for row in db.query(FamilyMember).filter(
                    FamilyMember.family_id.in_(family_ids)
                )
            ] if family_ids else []

            def delete_exact_ids(model, row_ids) -> None:
                if row_ids:
                    db.query(model).filter(model.id.in_(row_ids)).delete(
                        synchronize_session=False
                    )
                    db.flush()

            if file_ids:
                db.query(ImportFile).filter(ImportFile.id.in_(file_ids)).update(
                    {"matched_homework_file_id": None},
                    synchronize_session=False,
                )
                db.flush()
            delete_exact_ids(CorrectionResult, correction_ids)
            delete_exact_ids(Submission, submission_ids)
            delete_exact_ids(DailyTask, task_ids)
            delete_exact_ids(AssignmentItem, item_ids)
            if plan_ids:
                db.query(AssignmentBatch).filter(
                    AssignmentBatch.id.in_(plan_ids)
                ).update(
                    {"target_assignment_batch_id": None},
                    synchronize_session=False,
                )
                db.flush()
            delete_exact_ids(AssignmentBatch, plan_ids)
            delete_exact_ids(ImportFile, file_ids)
            delete_exact_ids(ImportBatch, batch_ids)
            delete_exact_ids(Student, student_ids)
            delete_exact_ids(FamilyMember, member_ids)
            delete_exact_ids(Family, family_ids)
            delete_exact_ids(User, user_ids)
            db.commit()

        for storage_path in storage_paths:
            if storage_path.is_file():
                storage_path.unlink()

        with SessionLocal() as db:
            marker_rows = (
                db.query(User).filter(User.openid.contains(marker)).count()
                + db.query(Family).filter(Family.name.contains(marker)).count()
                + db.query(Student).filter(Student.name.contains(marker)).count()
                + db.query(ImportBatch).filter(ImportBatch.title.contains(marker)).count()
                + db.query(AssignmentBatch).filter(AssignmentBatch.title.contains(marker)).count()
                + db.query(AssignmentItem).filter(AssignmentItem.title.contains(marker)).count()
                + db.query(DailyTask).filter(DailyTask.title.contains(marker)).count()
                + db.query(ImportFile).filter(ImportFile.file_name.contains(marker)).count()
                + db.query(Submission).filter(Submission.student_note.contains(marker)).count()
                + db.query(CorrectionResult).filter(CorrectionResult.summary.contains(marker)).count()
            )
            assert marker_rows == 0


def test_import_upload_roles_use_content_display_names(isolated_import_fixture):
    fixture = isolated_import_fixture
    owner = fixture.create_parent("roles")
    batch = unwrap(client.post("/api/v1/import-batches", headers=owner.headers, json={
        "student_id": owner.student_id,
        "title": fixture.marker,
    }))
    fixture.register_batch(batch["id"])

    homework = unwrap(client.post(
        f"/api/v1/import-batches/{batch['id']}/files",
        headers=owner.headers,
        data={
            "file_type": "image",
            "document_role": "homework",
            "sort_order": "0",
        },
        files={"file": (f"tmp_{fixture.marker}.jpg", BytesIO(b"homework"), "image/jpeg")},
    ))
    assert homework["document_role"] == "homework"
    assert homework["display_name"] == "第 1 份作业资料"
    assert "tmp_" not in homework["display_name"]
    assert homework["can_delete"] is True

    answer = unwrap(client.post(
        f"/api/v1/import-batches/{batch['id']}/files",
        headers=owner.headers,
        data={
            "file_type": "image",
            "document_role": "answer",
            "sort_order": "1",
        },
        files={"file": (f"tmp_{fixture.marker}-answer.jpg", BytesIO(b"answer"), "image/jpeg")},
    ))
    assert answer["document_role"] == "answer"
    assert answer["display_name"] == "第 1 份答案资料"
    assert "tmp_" not in answer["display_name"]

    invalid = client.post(
        f"/api/v1/import-batches/{batch['id']}/files",
        headers=owner.headers,
        data={"file_type": "image", "document_role": "reference", "sort_order": "2"},
        files={"file": ("invalid.jpg", BytesIO(b"invalid"), "image/jpeg")},
    )
    assert invalid.status_code == 422


def test_import_routes_enforce_family_access(isolated_import_fixture, monkeypatch):
    fixture = isolated_import_fixture
    monkeypatch.setattr(
        "backend.app.api.routers.imports.parse_import_file.delay",
        lambda _file_id, _token: None,
    )
    owner = fixture.create_parent("owner")
    outsider = fixture.create_parent("outsider")
    batch = unwrap(client.post("/api/v1/import-batches", headers=owner.headers, json={
        "student_id": owner.student_id,
        "title": fixture.marker,
    }))
    fixture.register_batch(batch["id"])
    uploaded = unwrap(client.post(
        f"/api/v1/import-batches/{batch['id']}/files",
        headers=owner.headers,
        data={"file_type": "image", "document_role": "homework", "sort_order": "0"},
        files={"file": (f"{fixture.marker}.jpg", BytesIO(b"homework"), "image/jpeg")},
    ))

    forbidden_requests = [
        client.post(
            f"/api/v1/import-batches/{batch['id']}/files",
            headers=outsider.headers,
            data={"file_type": "image", "document_role": "homework", "sort_order": "1"},
            files={"file": ("forbidden.jpg", BytesIO(b"forbidden"), "image/jpeg")},
        ),
        client.get(f"/api/v1/import-batches/{batch['id']}", headers=outsider.headers),
        client.get(f"/api/v1/import-batches/{batch['id']}/files", headers=outsider.headers),
        client.get(uploaded["preview_url"], headers=outsider.headers, follow_redirects=False),
        client.post(f"/api/v1/import-batches/{batch['id']}/parse", headers=outsider.headers),
        client.patch(
            f"/api/v1/import-batches/{batch['id']}",
            headers=outsider.headers,
            json={"raw_text": "forbidden"},
        ),
        client.delete(
            f"/api/v1/import-batches/files/{uploaded['file_id']}",
            headers=outsider.headers,
        ),
    ]
    assert [response.status_code for response in forbidden_requests] == [403] * 7

    other_student = client.post("/api/v1/import-batches", headers=owner.headers, json={
        "student_id": outsider.student_id,
        "title": f"{fixture.marker}-forbidden",
    })
    assert other_student.status_code == 403


def test_staged_import_file_deletion_cascades_without_false_success(
    isolated_import_fixture,
    monkeypatch,
):
    fixture = isolated_import_fixture
    owner = fixture.create_parent("delete")

    def create_batch(suffix: str) -> int:
        payload = unwrap(client.post(
            "/api/v1/import-batches",
            headers=owner.headers,
            json={
                "student_id": owner.student_id,
                "title": f"{fixture.marker}-{suffix}",
            },
        ))
        fixture.register_batch(payload["id"])
        return payload["id"]

    staged_batch_id = create_batch("staged")
    staged_root = upload_subdir("imports", str(staged_batch_id))
    pair_homework_path = staged_root / "pair-homework.jpg"
    pair_answer_path = staged_root / "pair-answer.jpg"
    pair_homework_path.write_bytes(b"homework")
    pair_answer_path.write_bytes(b"answer")
    with SessionLocal() as db:
        homework = ImportFile(
            import_batch_id=staged_batch_id,
            file_name=f"{fixture.marker}-homework.jpg",
            file_type="image",
            file_url="https://staged.example/pair-homework.jpg",
            storage_path=str(pair_homework_path),
            document_role="homework",
            recognized_title="四年级数学第一单元练习",
            parse_status="success",
            recognition_status="success",
            match_status="not_required",
        )
        db.add(homework)
        db.flush()
        answer = ImportFile(
            import_batch_id=staged_batch_id,
            file_name=f"{fixture.marker}-answer.jpg",
            file_type="image",
            file_url="https://staged.example/pair-answer.jpg",
            storage_path=str(pair_answer_path),
            document_role="answer",
            parse_status="success",
            recognition_status="success",
            match_status="matched",
            matched_homework_file_id=homework.id,
        )
        db.add(answer)
        db.flush()
        plan = AssignmentBatch(
            student_id=owner.student_id,
            import_batch_id=staged_batch_id,
            title=f"{fixture.marker}-pending-plan",
            status="pending_confirm",
        )
        db.add(plan)
        db.flush()
        assignment_item = AssignmentItem(
            assignment_batch_id=plan.id,
            subject="数学",
            title="四年级数学第一单元练习",
            import_file_id=homework.id,
        )
        db.add(assignment_item)
        db.flush()
        daily_task = DailyTask(
            student_id=owner.student_id,
            assignment_batch_id=plan.id,
            assignment_item_id=assignment_item.id,
            task_date=date.today(),
            subject="数学",
            title="第一单元练习",
        )
        db.add(daily_task)
        db.commit()
        homework_id = homework.id
        answer_id = answer.id
        plan_id = plan.id
        assignment_item_id = assignment_item.id
        daily_task_id = daily_task.id
    fixture.register_plan(plan_id)

    deleted_oss_urls: list[str] = []
    monkeypatch.setattr(
        "backend.app.services.import_file_service.validate_import_oss_url",
        lambda url, _batch_id: url,
    )
    monkeypatch.setattr(
        "backend.app.services.import_file_service.create_oss_delete_backup",
        lambda url, _batch_id: SimpleNamespace(url=url),
    )
    monkeypatch.setattr(
        "backend.app.services.import_file_service.restore_oss_delete_backup",
        lambda _backup: None,
    )
    monkeypatch.setattr(
        "backend.app.services.import_file_service.discard_oss_delete_backup",
        lambda _backup: None,
    )
    monkeypatch.setattr(
        "backend.app.services.import_file_service.delete_oss_url",
        lambda url: deleted_oss_urls.append(url),
        raising=False,
    )
    pair_deleted = unwrap(client.delete(
        f"/api/v1/import-batches/files/{homework_id}",
        headers=owner.headers,
    ))
    assert pair_deleted == {"deleted_file_ids": [homework_id, answer_id]}
    assert deleted_oss_urls == [
        "https://staged.example/pair-homework.jpg",
        "https://staged.example/pair-answer.jpg",
    ]
    assert not pair_homework_path.exists()
    assert not pair_answer_path.exists()
    with SessionLocal() as db:
        assert db.get(ImportFile, homework_id) is None
        assert db.get(ImportFile, answer_id) is None
        assert db.get(AssignmentItem, assignment_item_id) is None
        assert db.get(DailyTask, daily_task_id) is None
        assert db.get(AssignmentBatch, plan_id) is not None

    unmatched_path = staged_root / "unmatched-answer.jpg"
    unmatched_path.write_bytes(b"unmatched")
    with SessionLocal() as db:
        unmatched = ImportFile(
            import_batch_id=staged_batch_id,
            file_name=f"{fixture.marker}-unmatched.jpg",
            file_type="image",
            file_url=str(unmatched_path),
            storage_path=str(unmatched_path),
            document_role="answer",
            parse_status="success",
            recognition_status="success",
            match_status="unmatched",
        )
        db.add(unmatched)
        db.commit()
        unmatched_id = unmatched.id
    answer_deleted = unwrap(client.delete(
        f"/api/v1/import-batches/files/{unmatched_id}",
        headers=owner.headers,
    ))
    assert answer_deleted == {"deleted_file_ids": [unmatched_id]}
    assert not unmatched_path.exists()

    failed_storage_path = staged_root / "failed-storage.jpg"
    failed_storage_path.write_bytes(b"must-remain")
    failed_url = "https://staged.example/fail-delete.jpg"
    with SessionLocal() as db:
        failed_file = ImportFile(
            import_batch_id=staged_batch_id,
            file_name=f"{fixture.marker}-failed.jpg",
            file_type="image",
            file_url=failed_url,
            storage_path=str(failed_storage_path),
            document_role="homework",
            parse_status="success",
            recognition_status="success",
            recognized_title="语文阅读练习",
            match_status="not_required",
        )
        db.add(failed_file)
        db.commit()
        failed_file_id = failed_file.id

    def fail_oss_delete(url: str) -> None:
        if url == failed_url:
            raise RuntimeError("OSS unavailable")

    monkeypatch.setattr(
        "backend.app.services.import_file_service.delete_oss_url",
        fail_oss_delete,
        raising=False,
    )
    failed_delete = client.delete(
        f"/api/v1/import-batches/files/{failed_file_id}",
        headers=owner.headers,
    )
    assert failed_delete.status_code == 502
    assert failed_storage_path.exists()
    with SessionLocal() as db:
        assert db.get(ImportFile, failed_file_id) is not None
    cards = unwrap(client.get(
        f"/api/v1/import-batches/{staged_batch_id}/files",
        headers=owner.headers,
    ))
    assert failed_file_id in {card["id"] for card in cards}

    confirmed_batch_id = create_batch("confirmed")
    confirmed_path = upload_subdir("imports", str(confirmed_batch_id)) / "confirmed.jpg"
    confirmed_path.write_bytes(b"confirmed")
    with SessionLocal() as db:
        confirmed_batch = db.get(ImportBatch, confirmed_batch_id)
        confirmed_batch.status = "confirmed"
        confirmed_file = ImportFile(
            import_batch_id=confirmed_batch_id,
            file_name=f"{fixture.marker}-confirmed.jpg",
            file_type="image",
            file_url=str(confirmed_path),
            storage_path=str(confirmed_path),
            document_role="homework",
        )
        db.add(confirmed_file)
        db.commit()
        confirmed_file_id = confirmed_file.id
    confirmed_delete = client.delete(
        f"/api/v1/import-batches/files/{confirmed_file_id}",
        headers=owner.headers,
    )
    assert confirmed_delete.status_code == 409

    active_batch_id = create_batch("active")
    active_path = upload_subdir("imports", str(active_batch_id)) / "active.jpg"
    active_path.write_bytes(b"active")
    with SessionLocal() as db:
        active_file = ImportFile(
            import_batch_id=active_batch_id,
            file_name=f"{fixture.marker}-active.jpg",
            file_type="image",
            file_url=str(active_path),
            storage_path=str(active_path),
            document_role="homework",
        )
        db.add(active_file)
        active_plan = AssignmentBatch(
            student_id=owner.student_id,
            import_batch_id=active_batch_id,
            title=f"{fixture.marker}-active-plan",
            status="active",
        )
        db.add(active_plan)
        db.commit()
        active_file_id = active_file.id
        active_plan_id = active_plan.id
    fixture.register_plan(active_plan_id)
    active_delete = client.delete(
        f"/api/v1/import-batches/files/{active_file_id}",
        headers=owner.headers,
    )
    assert active_delete.status_code == 409
    with SessionLocal() as db:
        assert db.get(ImportFile, confirmed_file_id) is not None
        assert db.get(ImportFile, active_file_id) is not None


def test_import_generation_is_idempotent_and_appends_new_files(
    isolated_import_fixture,
):
    fixture = isolated_import_fixture
    owner = fixture.create_parent("incremental-generation")
    today = date.today()
    batch = unwrap(client.post(
        "/api/v1/import-batches",
        headers=owner.headers,
        json={
            "student_id": owner.student_id,
            "title": f"{fixture.marker}-incremental",
            "period_type": "daily",
            "start_date": today.isoformat(),
            "end_date": today.isoformat(),
        },
    ))
    fixture.register_batch(batch["id"])

    with SessionLocal() as db:
        first_file = ImportFile(
            import_batch_id=batch["id"],
            file_name=f"tmp_{fixture.marker}-first.jpg",
            file_type="image",
            file_url=f"https://staged.example/{fixture.marker}-first.jpg",
            extracted_text="数学四年级下册第3单元练习",
            parse_status="success",
            document_role="homework",
            recognized_title="数学四年级下册第3单元练习",
            recognition_status="success",
            match_status="not_required",
            sort_order=0,
        )
        db.add(first_file)
        db.add(ImportFile(
            import_batch_id=batch["id"],
            file_name=f"tmp_{fixture.marker}-failed-recognition.jpg",
            file_type="image",
            file_url=f"https://staged.example/{fixture.marker}-failed-recognition.jpg",
            extracted_text="不得生成的文件",
            parse_status="success",
            document_role="homework",
            recognized_title="失败识别残留标题不得生成",
            recognition_status="failed",
            match_status="not_required",
            sort_order=99,
        ))
        db.commit()
        first_file_id = first_file.id
        first_file.document_role = None
        db.commit()

    first = unwrap(client.post(
        f"/api/v1/plans/from-import/{batch['id']}/generate",
        headers=owner.headers,
    ))
    fixture.register_plan(first["assignment_batch_id"])
    with SessionLocal() as db:
        first_item = db.query(AssignmentItem).filter(
            AssignmentItem.assignment_batch_id == first["assignment_batch_id"],
            AssignmentItem.import_file_id == first_file_id,
        ).one()
        first_item_id = first_item.id
        first_task_ids = [row.id for row in db.query(DailyTask).filter(
            DailyTask.assignment_item_id == first_item_id,
        ).order_by(DailyTask.id)]
        assert first_item.title == "数学四年级下册第3单元练习"
        assert "tmp_" not in first_item.title

    second = unwrap(client.post(
        f"/api/v1/plans/from-import/{batch['id']}/generate",
        headers=owner.headers,
    ))
    assert second["assignment_batch_id"] == first["assignment_batch_id"]
    with SessionLocal() as db:
        assert db.query(AssignmentItem).filter(
            AssignmentItem.assignment_batch_id == first["assignment_batch_id"],
        ).count() == 1
        assert [row.id for row in db.query(DailyTask).filter(
            DailyTask.assignment_batch_id == first["assignment_batch_id"],
        ).order_by(DailyTask.id)] == first_task_ids

        db.add(ImportFile(
            import_batch_id=batch["id"],
            file_name=f"tmp_{fixture.marker}-first-answer.jpg",
            file_type="image",
            file_url=f"https://staged.example/{fixture.marker}-first-answer.jpg",
            extracted_text="参考答案：1.A 2.B",
            parse_status="success",
            document_role="answer",
            recognition_status="success",
            match_status="matched",
            matched_homework_file_id=first_file_id,
            sort_order=1,
        ))
        db.commit()

    enriched = unwrap(client.post(
        f"/api/v1/plans/from-import/{batch['id']}/generate",
        headers=owner.headers,
    ))
    assert enriched["assignment_batch_id"] == first["assignment_batch_id"]
    with SessionLocal() as db:
        assert db.get(AssignmentItem, first_item_id).answer_text == "参考答案：1.A 2.B"
        assert [row.id for row in db.query(DailyTask).filter(
            DailyTask.assignment_batch_id == first["assignment_batch_id"],
        ).order_by(DailyTask.id)] == first_task_ids

    with SessionLocal() as db:
        second_file = ImportFile(
            import_batch_id=batch["id"],
            file_name=f"tmp_{fixture.marker}-second.jpg",
            file_type="image",
            file_url=f"https://staged.example/{fixture.marker}-second.jpg",
            extracted_text="语文四年级古诗背诵",
            parse_status="success",
            document_role="homework",
            recognized_title="语文四年级古诗背诵",
            recognition_status="success",
            match_status="not_required",
            sort_order=2,
        )
        db.add(second_file)
        db.commit()
        second_file_id = second_file.id

    third = unwrap(client.post(
        f"/api/v1/plans/from-import/{batch['id']}/generate",
        headers=owner.headers,
    ))
    assert third["assignment_batch_id"] == first["assignment_batch_id"]
    with SessionLocal() as db:
        items = db.query(AssignmentItem).filter(
            AssignmentItem.assignment_batch_id == first["assignment_batch_id"],
        ).order_by(AssignmentItem.id).all()
        assert len(items) == 2
        assert db.get(AssignmentItem, first_item_id) is not None
        assert all(db.get(DailyTask, task_id) is not None for task_id in first_task_ids)
        appended = next(item for item in items if item.import_file_id == second_file_id)
        assert appended.title == "语文四年级古诗背诵"
        assert db.query(DailyTask).filter(
            DailyTask.assignment_item_id == appended.id,
        ).count() == 1


def test_raw_text_only_generation_reuses_item_and_task_ids(
    isolated_import_fixture,
):
    fixture = isolated_import_fixture
    owner = fixture.create_parent("raw-text-idempotent")
    today = date.today()
    batch = unwrap(client.post(
        "/api/v1/import-batches",
        headers=owner.headers,
        json={
            "student_id": owner.student_id,
            "title": f"{fixture.marker}-raw-text",
            "period_type": "daily",
            "start_date": today.isoformat(),
            "end_date": today.isoformat(),
            "raw_text": "数学口算20道，语文阅读1篇",
        },
    ))
    fixture.register_batch(batch["id"])

    first = unwrap(client.post(
        f"/api/v1/plans/from-import/{batch['id']}/generate",
        headers=owner.headers,
    ))
    fixture.register_plan(first["assignment_batch_id"])
    with SessionLocal() as db:
        first_item_ids = [row.id for row in db.query(AssignmentItem).filter(
            AssignmentItem.assignment_batch_id == first["assignment_batch_id"],
        ).order_by(AssignmentItem.id)]
        first_task_ids = [row.id for row in db.query(DailyTask).filter(
            DailyTask.assignment_batch_id == first["assignment_batch_id"],
        ).order_by(DailyTask.id)]
    assert first_item_ids
    assert first_task_ids

    second = unwrap(client.post(
        f"/api/v1/plans/from-import/{batch['id']}/generate",
        headers=owner.headers,
    ))
    assert second["assignment_batch_id"] == first["assignment_batch_id"]
    with SessionLocal() as db:
        assert [row.id for row in db.query(AssignmentItem).filter(
            AssignmentItem.assignment_batch_id == first["assignment_batch_id"],
        ).order_by(AssignmentItem.id)] == first_item_ids
        assert [row.id for row in db.query(DailyTask).filter(
            DailyTask.assignment_batch_id == first["assignment_batch_id"],
        ).order_by(DailyTask.id)] == first_task_ids


def test_plan_confirmation_blocks_unready_import_files(
    isolated_import_fixture,
):
    fixture = isolated_import_fixture
    owner = fixture.create_parent("confirmation-blockers")
    today = date.today()

    def create_batch(suffix: str) -> int:
        payload = unwrap(client.post(
            "/api/v1/import-batches",
            headers=owner.headers,
            json={
                "student_id": owner.student_id,
                "title": f"{fixture.marker}-{suffix}",
                "period_type": "daily",
                "start_date": today.isoformat(),
                "end_date": today.isoformat(),
            },
        ))
        fixture.register_batch(payload["id"])
        return payload["id"]

    blocked_batch_id = create_batch("blocked")
    with SessionLocal() as db:
        homework = ImportFile(
            import_batch_id=blocked_batch_id,
            file_name=f"tmp_{fixture.marker}-ready.jpg",
            file_type="image",
            file_url=f"https://staged.example/{fixture.marker}-ready.jpg",
            extracted_text="数学第3单元练习",
            parse_status="success",
            document_role="homework",
            recognized_title="数学第3单元练习",
            recognition_status="success",
            match_status="not_required",
            sort_order=0,
        )
        processing = ImportFile(
            import_batch_id=blocked_batch_id,
            file_name=f"tmp_{fixture.marker}-processing.jpg",
            file_type="image",
            file_url=f"https://staged.example/{fixture.marker}-processing.jpg",
            parse_status="processing",
            document_role="homework",
            recognition_status="pending",
            sort_order=1,
        )
        db.add_all([homework, processing])
        db.commit()
        homework_id = homework.id
        blocker_file_id = processing.id

    generated = unwrap(client.post(
        f"/api/v1/plans/from-import/{blocked_batch_id}/generate",
        headers=owner.headers,
    ))
    plan_id = generated["assignment_batch_id"]
    fixture.register_plan(plan_id)

    def assert_blocked(code: str) -> None:
        response = client.post(
            f"/api/v1/plans/{plan_id}/confirm",
            headers=owner.headers,
            json={},
        )
        assert response.status_code == 409
        assert {row["code"] for row in response.json()["detail"]} == {code}
        assert response.json()["detail"][0]["file_id"] == blocker_file_id
        with SessionLocal() as db:
            assert db.get(AssignmentBatch, plan_id).status == "pending_confirm"

    assert_blocked("file_processing")

    with SessionLocal() as db:
        blocker = db.get(ImportFile, blocker_file_id)
        blocker.parse_status = "success"
        blocker.recognition_status = "success"
        blocker.recognized_title = "异常租约文件不得确认"
        blocker.parse_claim_token = "orphan-parse-claim"
        db.commit()
    assert_blocked("file_processing")

    with SessionLocal() as db:
        blocker = db.get(ImportFile, blocker_file_id)
        blocker.parse_claim_token = None
        blocker.recognition_status = "failed"
        db.commit()
    assert_blocked("homework_title_unrecognized")

    with SessionLocal() as db:
        blocker = db.get(ImportFile, blocker_file_id)
        blocker.document_role = "answer"
        blocker.recognition_status = "success"
        blocker.match_status = "pending"
        db.commit()
    assert_blocked("answer_pending")

    with SessionLocal() as db:
        blocker = db.get(ImportFile, blocker_file_id)
        blocker.recognition_status = "failed"
        blocker.match_status = "matched"
        blocker.matched_homework_file_id = homework_id
        db.commit()
    assert_blocked("answer_pending")

    with SessionLocal() as db:
        blocker = db.get(ImportFile, blocker_file_id)
        blocker.parse_status = "failed"
        blocker.recognition_status = "success"
        db.commit()
    assert_blocked("answer_pending")

    with SessionLocal() as db:
        blocker = db.get(ImportFile, blocker_file_id)
        blocker.parse_status = "success"
        blocker.match_status = "unmatched"
        blocker.matched_homework_file_id = None
        db.commit()
    assert_blocked("answer_unmatched")

    with SessionLocal() as db:
        blocker = db.get(ImportFile, blocker_file_id)
        blocker.match_status = "matched"
        blocker.matched_homework_file_id = blocker_file_id
        db.commit()
    assert_blocked("answer_match_conflict")

    with SessionLocal() as db:
        blocker = db.get(ImportFile, blocker_file_id)
        blocker.matched_homework_file_id = homework_id
        blocker.extracted_text = "参考答案：1.A"
        db.commit()
    confirmed = unwrap(client.post(
        f"/api/v1/plans/{plan_id}/confirm",
        headers=owner.headers,
        json={},
    ))
    assert confirmed == {"plan_id": plan_id, "status": "active"}

    no_answer_batch_id = create_batch("no-answer")
    with SessionLocal() as db:
        db.add(ImportFile(
            import_batch_id=no_answer_batch_id,
            file_name=f"tmp_{fixture.marker}-no-answer.jpg",
            file_type="image",
            file_url=f"https://staged.example/{fixture.marker}-no-answer.jpg",
            extracted_text="语文阅读练习",
            parse_status="success",
            document_role="homework",
            recognized_title="语文阅读练习",
            recognition_status="success",
            match_status="not_required",
        ))
        db.commit()
    no_answer_plan = unwrap(client.post(
        f"/api/v1/plans/from-import/{no_answer_batch_id}/generate",
        headers=owner.headers,
    ))
    fixture.register_plan(no_answer_plan["assignment_batch_id"])
    no_answer_confirmed = unwrap(client.post(
        f"/api/v1/plans/{no_answer_plan['assignment_batch_id']}/confirm",
        headers=owner.headers,
        json={},
    ))
    assert no_answer_confirmed["status"] == "active"


def test_same_range_confirmation_merges_without_touching_history(
    isolated_import_fixture,
):
    fixture = isolated_import_fixture
    owner = fixture.create_parent("same-range-merge")
    today = date.today()

    def create_ready_plan(suffix: str, start: date, end: date) -> tuple[int, int]:
        batch = unwrap(client.post(
            "/api/v1/import-batches",
            headers=owner.headers,
            json={
                "student_id": owner.student_id,
                "title": f"{fixture.marker}-{suffix}",
                "period_type": "custom",
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            },
        ))
        fixture.register_batch(batch["id"])
        with SessionLocal() as db:
            source = ImportFile(
                import_batch_id=batch["id"],
                file_name=f"tmp_{fixture.marker}-{suffix}.jpg",
                file_type="image",
                file_url=f"https://staged.example/{fixture.marker}-{suffix}.jpg",
                extracted_text=f"{suffix}作业内容",
                parse_status="success",
                document_role="homework",
                recognized_title=f"{fixture.marker}-{suffix}-recognized",
                recognition_status="success",
                match_status="not_required",
            )
            db.add(source)
            db.commit()
            source_id = source.id
        generated = unwrap(client.post(
            f"/api/v1/plans/from-import/{batch['id']}/generate",
            headers=owner.headers,
        ))
        fixture.register_plan(generated["assignment_batch_id"])
        return generated["assignment_batch_id"], source_id

    first_plan_id, _ = create_ready_plan(
        "first",
        today,
        today + timedelta(days=2),
    )
    first_confirmed = unwrap(client.post(
        f"/api/v1/plans/{first_plan_id}/confirm",
        headers=owner.headers,
        json={},
    ))
    assert first_confirmed == {"plan_id": first_plan_id, "status": "active"}
    with SessionLocal() as db:
        old_item = db.query(AssignmentItem).filter(
            AssignmentItem.assignment_batch_id == first_plan_id,
        ).one()
        old_task = db.query(DailyTask).filter(
            DailyTask.assignment_batch_id == first_plan_id,
        ).one()
        submission = Submission(
            daily_task_id=old_task.id,
            student_id=owner.student_id,
            submission_type="photo",
            status="corrected",
            student_note=f"{fixture.marker}-historical-submission",
            answer_text="历史提交答案",
        )
        db.add(submission)
        db.flush()
        correction = CorrectionResult(
            submission_id=submission.id,
            daily_task_id=old_task.id,
            completion_score=93,
            accuracy_score=88,
            confidence_score=0.91,
            summary=f"{fixture.marker}-historical-correction",
        )
        db.add(correction)
        db.commit()
        old_item_id = old_item.id
        old_task_id = old_task.id
        submission_id = submission.id
        correction_id = correction.id
        history_snapshot = (
            submission.student_note,
            submission.answer_text,
            correction.completion_score,
            correction.summary,
        )

    staging_plan_id, second_source_id = create_ready_plan(
        "second",
        today,
        today + timedelta(days=2),
    )
    with SessionLocal() as db:
        new_item = db.query(AssignmentItem).filter(
            AssignmentItem.assignment_batch_id == staging_plan_id,
            AssignmentItem.import_file_id == second_source_id,
        ).one()
        new_task = db.query(DailyTask).filter(
            DailyTask.assignment_batch_id == staging_plan_id,
            DailyTask.assignment_item_id == new_item.id,
        ).one()
        new_item_id = new_item.id
        new_task_id = new_task.id

    merged = unwrap(client.post(
        f"/api/v1/plans/{staging_plan_id}/confirm",
        headers=owner.headers,
        json={},
    ))
    assert merged == {"plan_id": first_plan_id, "status": "active"}
    with SessionLocal() as db:
        assert db.get(AssignmentBatch, staging_plan_id).status == "merged"
        assert db.get(AssignmentBatch, staging_plan_id).target_assignment_batch_id == first_plan_id
        assert db.get(AssignmentItem, old_item_id).assignment_batch_id == first_plan_id
        assert db.get(DailyTask, old_task_id).assignment_batch_id == first_plan_id
        assert db.get(AssignmentItem, new_item_id).assignment_batch_id == first_plan_id
        assert db.get(DailyTask, new_task_id).assignment_batch_id == first_plan_id
        saved_submission = db.get(Submission, submission_id)
        saved_correction = db.get(CorrectionResult, correction_id)
        assert (
            saved_submission.student_note,
            saved_submission.answer_text,
            saved_correction.completion_score,
            saved_correction.summary,
        ) == history_snapshot

    separate_plan_id, _ = create_ready_plan(
        "different-range",
        today + timedelta(days=1),
        today + timedelta(days=3),
    )
    separate = unwrap(client.post(
        f"/api/v1/plans/{separate_plan_id}/confirm",
        headers=owner.headers,
        json={},
    ))
    assert separate == {"plan_id": separate_plan_id, "status": "active"}


def test_staged_assignment_item_can_be_deleted_before_confirmation(
    isolated_import_fixture,
):
    fixture = isolated_import_fixture
    owner = fixture.create_parent("draft-item-owner")
    outsider = fixture.create_parent("draft-item-outsider")
    today = date.today()

    def create_batch(suffix: str) -> int:
        payload = unwrap(client.post(
            "/api/v1/import-batches",
            headers=owner.headers,
            json={
                "student_id": owner.student_id,
                "title": f"{fixture.marker}-{suffix}",
                "period_type": "daily",
                "start_date": today.isoformat(),
                "end_date": today.isoformat(),
            },
        ))
        fixture.register_batch(payload["id"])
        return payload["id"]

    batch_id = create_batch("deletable")
    staged_root = upload_subdir("imports", str(batch_id))
    homework_path = staged_root / f"{fixture.marker}-homework.jpg"
    answer_path = staged_root / f"{fixture.marker}-answer.jpg"
    homework_path.write_bytes(b"homework")
    answer_path.write_bytes(b"answer")
    with SessionLocal() as db:
        homework = ImportFile(
            import_batch_id=batch_id,
            file_name=f"tmp_{fixture.marker}-homework.jpg",
            file_type="image",
            file_url=str(homework_path),
            storage_path=str(homework_path),
            extracted_text="数学练习",
            parse_status="success",
            document_role="homework",
            recognized_title="数学四年级下册第3单元练习",
            recognition_status="success",
            match_status="not_required",
            sort_order=0,
        )
        db.add(homework)
        db.flush()
        answer = ImportFile(
            import_batch_id=batch_id,
            file_name=f"tmp_{fixture.marker}-answer.jpg",
            file_type="image",
            file_url=str(answer_path),
            storage_path=str(answer_path),
            extracted_text="参考答案：1.A",
            parse_status="success",
            document_role="answer",
            recognition_status="success",
            match_status="matched",
            matched_homework_file_id=homework.id,
            sort_order=1,
        )
        db.add(answer)
        db.commit()
        homework_id = homework.id
        answer_id = answer.id

    generated = unwrap(client.post(
        f"/api/v1/plans/from-import/{batch_id}/generate",
        headers=owner.headers,
    ))
    plan_id = generated["assignment_batch_id"]
    fixture.register_plan(plan_id)
    with SessionLocal() as db:
        item = db.query(AssignmentItem).filter(
            AssignmentItem.assignment_batch_id == plan_id,
            AssignmentItem.import_file_id == homework_id,
        ).one()
        item_id = item.id
        task_ids = [row.id for row in db.query(DailyTask).filter(
            DailyTask.assignment_item_id == item_id,
        )]

    missing_auth = client.delete(
        f"/api/v1/plans/{plan_id}/draft-items/{item_id}",
    )
    assert missing_auth.status_code == 401
    forbidden = client.delete(
        f"/api/v1/plans/{plan_id}/draft-items/{item_id}",
        headers=outsider.headers,
    )
    assert forbidden.status_code == 403
    with SessionLocal() as db:
        assert db.get(AssignmentItem, item_id) is not None
        assert db.get(ImportFile, homework_id) is not None

    draft = unwrap(client.get(
        f"/api/v1/plans/{plan_id}/draft",
        headers=owner.headers,
    ))
    assert draft["plan"]["target_assignment_batch_id"] is None
    assert draft["existing_items"] == []
    assert draft["new_items"][0]["id"] == item_id
    assert draft["new_items"][0]["title"] == "数学四年级下册第3单元练习"
    assert draft["new_items"][0]["answer_status"] == "matched"
    assert draft["new_items"][0]["can_delete"] is True
    assert draft["new_items"][0]["source_file"]["display_name"] == "数学四年级下册第3单元练习"
    assert draft["can_confirm"] is True

    deleted = unwrap(client.delete(
        f"/api/v1/plans/{plan_id}/draft-items/{item_id}",
        headers=owner.headers,
    ))
    assert deleted == {"deleted_file_ids": [homework_id, answer_id]}
    assert not homework_path.exists()
    assert not answer_path.exists()
    with SessionLocal() as db:
        assert db.get(AssignmentItem, item_id) is None
        assert db.get(ImportFile, homework_id) is None
        assert db.get(ImportFile, answer_id) is None
        assert all(db.get(DailyTask, task_id) is None for task_id in task_ids)

    active_batch_id = create_batch("active-reject")
    with SessionLocal() as db:
        active_file = ImportFile(
            import_batch_id=active_batch_id,
            file_name=f"tmp_{fixture.marker}-active.jpg",
            file_type="image",
            file_url=f"https://staged.example/{fixture.marker}-active.jpg",
            extracted_text="英语练习",
            parse_status="success",
            document_role="homework",
            recognized_title="英语四年级阅读练习",
            recognition_status="success",
            match_status="not_required",
        )
        db.add(active_file)
        db.commit()
        active_file_id = active_file.id
    active_generated = unwrap(client.post(
        f"/api/v1/plans/from-import/{active_batch_id}/generate",
        headers=owner.headers,
    ))
    active_plan_id = active_generated["assignment_batch_id"]
    fixture.register_plan(active_plan_id)
    unwrap(client.post(
        f"/api/v1/plans/{active_plan_id}/confirm",
        headers=owner.headers,
        json={},
    ))
    with SessionLocal() as db:
        active_item_id = db.query(AssignmentItem.id).filter(
            AssignmentItem.import_file_id == active_file_id,
        ).scalar()
    active_rejected = client.delete(
        f"/api/v1/plans/{active_plan_id}/draft-items/{active_item_id}",
        headers=owner.headers,
    )
    assert active_rejected.status_code == 409

    merged_batch_id = create_batch("merged-reject")
    with SessionLocal() as db:
        merged_file = ImportFile(
            import_batch_id=merged_batch_id,
            file_name=f"tmp_{fixture.marker}-merged.jpg",
            file_type="image",
            file_url=f"https://staged.example/{fixture.marker}-merged.jpg",
            extracted_text="语文练习",
            parse_status="success",
            document_role="homework",
            recognized_title="语文四年级阅读练习",
            recognition_status="success",
            match_status="not_required",
        )
        db.add(merged_file)
        merged_plan = AssignmentBatch(
            student_id=owner.student_id,
            import_batch_id=merged_batch_id,
            target_assignment_batch_id=active_plan_id,
            title=f"{fixture.marker}-merged-plan",
            period_type="daily",
            start_date=today,
            end_date=today,
            status="merged",
        )
        db.add(merged_plan)
        db.flush()
        merged_item = AssignmentItem(
            assignment_batch_id=merged_plan.id,
            subject="语文",
            title="语文四年级阅读练习",
            import_file_id=merged_file.id,
        )
        db.add(merged_item)
        db.commit()
        merged_plan_id = merged_plan.id
        merged_item_id = merged_item.id
    fixture.register_plan(merged_plan_id)
    merged_rejected = client.delete(
        f"/api/v1/plans/{merged_plan_id}/draft-items/{merged_item_id}",
        headers=owner.headers,
    )
    assert merged_rejected.status_code == 409
    with SessionLocal() as db:
        assert db.get(AssignmentItem, active_item_id) is not None
        assert db.get(AssignmentItem, merged_item_id) is not None


def test_content_import_accepted_scenario_isolated(
    isolated_import_fixture,
):
    fixture = isolated_import_fixture
    owner = fixture.create_parent("accepted-scenario")
    today = date.today()

    def create_batch(suffix: str) -> int:
        payload = unwrap(client.post(
            "/api/v1/import-batches",
            headers=owner.headers,
            json={
                "student_id": owner.student_id,
                "title": f"{fixture.marker}-{suffix}",
                "period_type": "custom",
                "start_date": today.isoformat(),
                "end_date": (today + timedelta(days=2)).isoformat(),
            },
        ))
        fixture.register_batch(payload["id"])
        return payload["id"]

    def signature(
        *,
        chapter: str,
        start: int,
        end: int,
        keywords: list[str],
        is_answer: bool,
        subject: str = "数学",
    ) -> str:
        return json.dumps({
            "subject": subject,
            "grade_hint": "四年级",
            "chapter": chapter,
            "question_start": start,
            "question_end": end,
            "question_count": end - start + 1,
            "keywords": keywords,
            "is_answer": is_answer,
        }, ensure_ascii=False)

    first_batch_id = create_batch("first")
    staged_root = upload_subdir("imports", str(first_batch_id))
    wrong_answer_path = staged_root / f"{fixture.marker}-wrong-answer.jpg"
    wrong_answer_path.write_bytes(b"wrong answer")
    with SessionLocal() as db:
        fraction_homework = ImportFile(
            import_batch_id=first_batch_id,
            file_name=f"tmp_{fixture.marker}-fraction-homework.jpg",
            file_type="image",
            file_url=f"https://staged.example/{fixture.marker}-fraction-homework.jpg",
            extracted_text="四年级数学第三单元分数计算第1至10题",
            parse_status="success",
            document_role="homework",
            recognized_title="四年级数学第三单元分数计算练习",
            recognition_status="success",
            match_status="not_required",
            content_signature_json=signature(
                chapter="第三单元",
                start=1,
                end=10,
                keywords=["分数", "计算"],
                is_answer=False,
            ),
            sort_order=0,
        )
        geometry_homework = ImportFile(
            import_batch_id=first_batch_id,
            file_name=f"tmp_{fixture.marker}-geometry-homework.jpg",
            file_type="image",
            file_url=f"https://staged.example/{fixture.marker}-geometry-homework.jpg",
            extracted_text="四年级数学第四单元图形面积第11至20题",
            parse_status="success",
            document_role="homework",
            recognized_title="四年级数学第四单元图形面积练习",
            recognition_status="success",
            match_status="not_required",
            content_signature_json=signature(
                chapter="第四单元",
                start=11,
                end=20,
                keywords=["图形", "面积"],
                is_answer=False,
            ),
            sort_order=1,
        )
        db.add_all([fraction_homework, geometry_homework])
        db.flush()
        matching_answer = ImportFile(
            import_batch_id=first_batch_id,
            file_name=f"tmp_{fixture.marker}-matching-answer.jpg",
            file_type="image",
            file_url=f"https://staged.example/{fixture.marker}-matching-answer.jpg",
            extracted_text="第三单元分数计算答案：1.A 至 10.C",
            parse_status="success",
            document_role="answer",
            recognition_status="success",
            content_signature_json=signature(
                chapter="第三单元",
                start=1,
                end=10,
                keywords=["分数", "计算"],
                is_answer=True,
            ),
            sort_order=2,
        )
        wrong_answer = ImportFile(
            import_batch_id=first_batch_id,
            file_name=f"tmp_{fixture.marker}-wrong-answer.jpg",
            file_type="image",
            file_url=str(wrong_answer_path),
            storage_path=str(wrong_answer_path),
            extracted_text="英语阅读答案第101至110题",
            parse_status="success",
            document_role="answer",
            recognition_status="success",
            content_signature_json=signature(
                subject="英语",
                chapter="阅读专项",
                start=101,
                end=110,
                keywords=["英语", "阅读"],
                is_answer=True,
            ),
            sort_order=3,
        )
        db.add_all([matching_answer, wrong_answer])
        db.commit()
        fraction_homework_id = fraction_homework.id
        geometry_homework_id = geometry_homework.id
        matching_answer_id = matching_answer.id
        wrong_answer_id = wrong_answer.id

    with SessionLocal() as db:
        match_batch_answers(db, first_batch_id)
    with SessionLocal() as db:
        matched = db.get(ImportFile, matching_answer_id)
        unmatched = db.get(ImportFile, wrong_answer_id)
        assert matched.match_status == "matched"
        assert matched.matched_homework_file_id == fraction_homework_id
        assert matched.matched_homework_file_id != geometry_homework_id
        assert unmatched.match_status == "unmatched"
        assert unmatched.matched_homework_file_id is None

    generated = unwrap(client.post(
        f"/api/v1/plans/from-import/{first_batch_id}/generate",
        headers=owner.headers,
    ))
    first_plan_id = generated["assignment_batch_id"]
    fixture.register_plan(first_plan_id)
    draft = unwrap(client.get(
        f"/api/v1/plans/{first_plan_id}/draft",
        headers=owner.headers,
    ))
    assert {item["title"] for item in draft["new_items"]} == {
        "四年级数学第三单元分数计算练习",
        "四年级数学第四单元图形面积练习",
    }
    assert all("tmp_" not in item["title"] for item in draft["new_items"])
    assert draft["can_confirm"] is False
    assert {row["code"] for row in draft["confirmation_blockers"]} == {
        "answer_unmatched"
    }

    blocked = client.post(
        f"/api/v1/plans/{first_plan_id}/confirm",
        headers=owner.headers,
        json={},
    )
    assert blocked.status_code == 409
    assert {row["code"] for row in blocked.json()["detail"]} == {
        "answer_unmatched"
    }
    deleted_wrong_answer = unwrap(client.delete(
        f"/api/v1/import-batches/files/{wrong_answer_id}",
        headers=owner.headers,
    ))
    assert deleted_wrong_answer == {"deleted_file_ids": [wrong_answer_id]}
    assert not wrong_answer_path.exists()
    with SessionLocal() as db:
        assert db.get(ImportFile, wrong_answer_id) is None
        preserved_answer = db.get(ImportFile, matching_answer_id)
        assert preserved_answer is not None
        assert preserved_answer.match_status == "matched"
        assert preserved_answer.matched_homework_file_id == fraction_homework_id
        assert db.get(ImportFile, fraction_homework_id) is not None
        assert db.get(ImportFile, geometry_homework_id) is not None

    confirmed = unwrap(client.post(
        f"/api/v1/plans/{first_plan_id}/confirm",
        headers=owner.headers,
        json={},
    ))
    assert confirmed == {"plan_id": first_plan_id, "status": "active"}
    with SessionLocal() as db:
        old_items = db.query(AssignmentItem).filter(
            AssignmentItem.assignment_batch_id == first_plan_id,
        ).order_by(AssignmentItem.id).all()
        old_tasks = db.query(DailyTask).filter(
            DailyTask.assignment_batch_id == first_plan_id,
        ).order_by(DailyTask.id).all()
        old_item_ids = [item.id for item in old_items]
        old_task_ids = [task.id for task in old_tasks]
        submission = Submission(
            daily_task_id=old_tasks[0].id,
            student_id=owner.student_id,
            submission_type="photo",
            status="corrected",
            student_note=f"{fixture.marker}-accepted-history",
            answer_text="历史答案",
        )
        db.add(submission)
        db.flush()
        correction = CorrectionResult(
            submission_id=submission.id,
            daily_task_id=old_tasks[0].id,
            completion_score=95,
            accuracy_score=90,
            confidence_score=0.92,
            summary=f"{fixture.marker}-accepted-correction",
        )
        db.add(correction)
        db.commit()
        submission_id = submission.id
        correction_id = correction.id

    second_batch_id = create_batch("second")
    with SessionLocal() as db:
        second_homework = ImportFile(
            import_batch_id=second_batch_id,
            file_name=f"tmp_{fixture.marker}-second-homework.jpg",
            file_type="image",
            file_url=f"https://staged.example/{fixture.marker}-second-homework.jpg",
            extracted_text="四年级数学第五单元小数加减练习",
            parse_status="success",
            document_role="homework",
            recognized_title="四年级数学第五单元小数加减练习",
            recognition_status="success",
            match_status="not_required",
            sort_order=0,
        )
        db.add(second_homework)
        db.commit()
        second_homework_id = second_homework.id
    second_generated = unwrap(client.post(
        f"/api/v1/plans/from-import/{second_batch_id}/generate",
        headers=owner.headers,
    ))
    second_plan_id = second_generated["assignment_batch_id"]
    fixture.register_plan(second_plan_id)
    merged = unwrap(client.post(
        f"/api/v1/plans/{second_plan_id}/confirm",
        headers=owner.headers,
        json={},
    ))
    assert merged == {"plan_id": first_plan_id, "status": "active"}
    with SessionLocal() as db:
        appended_item = db.query(AssignmentItem).filter(
            AssignmentItem.import_file_id == second_homework_id,
        ).one()
        assert appended_item.assignment_batch_id == first_plan_id
        assert [db.get(AssignmentItem, row_id).id for row_id in old_item_ids] == old_item_ids
        assert [db.get(DailyTask, row_id).id for row_id in old_task_ids] == old_task_ids
        assert db.get(Submission, submission_id).daily_task_id == old_task_ids[0]
        assert db.get(CorrectionResult, correction_id).submission_id == submission_id
        assert db.query(AssignmentItem).filter(
            AssignmentItem.assignment_batch_id == first_plan_id,
        ).count() == 3

    delete_batch_id = create_batch("delete-only-addition")
    delete_root = upload_subdir("imports", str(delete_batch_id))
    delete_path = delete_root / f"{fixture.marker}-delete-only-addition.jpg"
    delete_path.write_bytes(b"staged addition")
    with SessionLocal() as db:
        deletable_homework = ImportFile(
            import_batch_id=delete_batch_id,
            file_name=f"tmp_{fixture.marker}-delete-only-addition.jpg",
            file_type="image",
            file_url=str(delete_path),
            storage_path=str(delete_path),
            extracted_text="四年级数学第六单元统计练习",
            parse_status="success",
            document_role="homework",
            recognized_title="四年级数学第六单元统计练习",
            recognition_status="success",
            match_status="not_required",
            sort_order=0,
        )
        db.add(deletable_homework)
        db.commit()
        deletable_homework_id = deletable_homework.id
    delete_generated = unwrap(client.post(
        f"/api/v1/plans/from-import/{delete_batch_id}/generate",
        headers=owner.headers,
    ))
    delete_plan_id = delete_generated["assignment_batch_id"]
    fixture.register_plan(delete_plan_id)
    with SessionLocal() as db:
        deletable_item = db.query(AssignmentItem).filter(
            AssignmentItem.import_file_id == deletable_homework_id,
        ).one()
        deletable_item_id = deletable_item.id
    deleted_addition = unwrap(client.delete(
        f"/api/v1/plans/{delete_plan_id}/draft-items/{deletable_item_id}",
        headers=owner.headers,
    ))
    assert deleted_addition == {"deleted_file_ids": [deletable_homework_id]}
    assert not delete_path.exists()
    with SessionLocal() as db:
        assert db.get(AssignmentItem, deletable_item_id) is None
        assert db.get(ImportFile, deletable_homework_id) is None
        assert db.query(AssignmentItem).filter(
            AssignmentItem.assignment_batch_id == first_plan_id,
        ).count() == 3
        assert [db.get(AssignmentItem, row_id).id for row_id in old_item_ids] == old_item_ids
        assert [db.get(DailyTask, row_id).id for row_id in old_task_ids] == old_task_ids
        assert db.get(Submission, submission_id).student_note == (
            f"{fixture.marker}-accepted-history"
        )
        assert db.get(CorrectionResult, correction_id).summary == (
            f"{fixture.marker}-accepted-correction"
        )


def test_staged_delete_recomputes_minutes_before_merge(
    isolated_import_fixture,
):
    fixture = isolated_import_fixture
    owner = fixture.create_parent("delete-minutes")
    today = date.today()

    def create_batch_with_files(suffix: str, titles: list[str]) -> tuple[int, list[int]]:
        batch = unwrap(client.post(
            "/api/v1/import-batches",
            headers=owner.headers,
            json={
                "student_id": owner.student_id,
                "title": f"{fixture.marker}-{suffix}",
                "period_type": "custom",
                "start_date": today.isoformat(),
                "end_date": (today + timedelta(days=2)).isoformat(),
            },
        ))
        fixture.register_batch(batch["id"])
        file_ids: list[int] = []
        with SessionLocal() as db:
            for index, title in enumerate(titles):
                path = upload_subdir("imports", str(batch["id"])) / f"{suffix}-{index}.jpg"
                path.write_bytes(title.encode("utf-8"))
                source = ImportFile(
                    import_batch_id=batch["id"],
                    file_name=f"tmp_{fixture.marker}-{suffix}-{index}.jpg",
                    file_type="image",
                    file_url=str(path),
                    storage_path=str(path),
                    extracted_text=title,
                    parse_status="success",
                    document_role="homework",
                    recognized_title=title,
                    recognition_status="success",
                    match_status="not_required",
                    sort_order=index,
                )
                db.add(source)
                db.flush()
                file_ids.append(source.id)
            db.commit()
        return batch["id"], file_ids

    canonical_batch_id, _ = create_batch_with_files(
        "canonical",
        ["数学四年级基础练习"],
    )
    canonical_draft = unwrap(client.post(
        f"/api/v1/plans/from-import/{canonical_batch_id}/generate",
        headers=owner.headers,
    ))
    canonical_id = canonical_draft["assignment_batch_id"]
    fixture.register_plan(canonical_id)
    unwrap(client.post(
        f"/api/v1/plans/{canonical_id}/confirm",
        headers=owner.headers,
        json={},
    ))
    with SessionLocal() as db:
        canonical_minutes_before = db.get(
            AssignmentBatch,
            canonical_id,
        ).total_estimated_minutes
    assert canonical_minutes_before == 60

    staging_batch_id, staging_file_ids = create_batch_with_files(
        "staging",
        ["语文四年级阅读练习", "英语四年级阅读练习"],
    )
    staging_draft = unwrap(client.post(
        f"/api/v1/plans/from-import/{staging_batch_id}/generate",
        headers=owner.headers,
    ))
    staging_id = staging_draft["assignment_batch_id"]
    fixture.register_plan(staging_id)
    with SessionLocal() as db:
        deleted_item = db.query(AssignmentItem).filter(
            AssignmentItem.assignment_batch_id == staging_id,
            AssignmentItem.import_file_id == staging_file_ids[0],
        ).one()
        deleted_item_id = deleted_item.id
        assert db.get(AssignmentBatch, staging_id).total_estimated_minutes == 120

    unwrap(client.delete(
        f"/api/v1/plans/{staging_id}/draft-items/{deleted_item_id}",
        headers=owner.headers,
    ))
    with SessionLocal() as db:
        assert db.get(AssignmentBatch, staging_id).total_estimated_minutes == 60
        remaining_item = db.query(AssignmentItem).filter(
            AssignmentItem.assignment_batch_id == staging_id,
        ).one()
        assert remaining_item.import_file_id == staging_file_ids[1]

    merged = unwrap(client.post(
        f"/api/v1/plans/{staging_id}/confirm",
        headers=owner.headers,
        json={},
    ))
    assert merged == {"plan_id": canonical_id, "status": "active"}
    with SessionLocal() as db:
        assert db.get(AssignmentBatch, canonical_id).total_estimated_minutes == 120
        assert db.get(AssignmentBatch, staging_id).status == "merged"


def test_staged_delete_rolls_back_minutes_and_storage_on_commit_failure(
    isolated_import_fixture,
    monkeypatch,
):
    fixture = isolated_import_fixture
    owner = fixture.create_parent("delete-minute-compensation")
    batch = unwrap(client.post(
        "/api/v1/import-batches",
        headers=owner.headers,
        json={
            "student_id": owner.student_id,
            "title": f"{fixture.marker}-delete-compensation",
        },
    ))
    fixture.register_batch(batch["id"])
    path = upload_subdir("imports", str(batch["id"])) / "commit-failure.jpg"
    path.write_bytes(b"must-be-restored")
    with SessionLocal() as db:
        source = ImportFile(
            import_batch_id=batch["id"],
            file_name=f"tmp_{fixture.marker}-commit-failure.jpg",
            file_type="image",
            file_url=str(path),
            storage_path=str(path),
            extracted_text="数学练习",
            parse_status="success",
            document_role="homework",
            recognized_title="数学四年级练习",
            recognition_status="success",
            match_status="not_required",
        )
        db.add(source)
        plan = AssignmentBatch(
            student_id=owner.student_id,
            import_batch_id=batch["id"],
            title=f"{fixture.marker}-compensation-plan",
            status="pending_confirm",
            total_estimated_minutes=60,
        )
        db.add(plan)
        db.flush()
        item = AssignmentItem(
            assignment_batch_id=plan.id,
            subject="数学",
            title="数学四年级练习",
            import_file_id=source.id,
            estimated_minutes_total=60,
        )
        db.add(item)
        db.flush()
        task = DailyTask(
            student_id=owner.student_id,
            assignment_batch_id=plan.id,
            assignment_item_id=item.id,
            task_date=date.today(),
            subject="数学",
            title="数学四年级练习",
        )
        db.add(task)
        db.commit()
        file_id = source.id
        plan_id = plan.id
        item_id = item.id
        task_id = task.id
    fixture.register_plan(plan_id)

    def fail_commit(_session):
        raise RuntimeError("forced final commit failure")

    monkeypatch.setattr("sqlalchemy.orm.Session.commit", fail_commit)
    response = client.delete(
        f"/api/v1/plans/{plan_id}/draft-items/{item_id}",
        headers=owner.headers,
    )
    monkeypatch.undo()
    assert response.status_code == 500
    assert path.exists()
    assert path.read_bytes() == b"must-be-restored"
    with SessionLocal() as db:
        assert db.get(ImportFile, file_id) is not None
        assert db.get(AssignmentItem, item_id) is not None
        assert db.get(DailyTask, task_id) is not None
        assert db.get(AssignmentBatch, plan_id).total_estimated_minutes == 60


def test_plan_routes_enforce_family_access_without_side_effects(
    isolated_import_fixture,
):
    fixture = isolated_import_fixture
    owner = fixture.create_parent("plan-access-owner")
    outsider = fixture.create_parent("plan-access-outsider")
    today = date.today()
    batch = unwrap(client.post(
        "/api/v1/import-batches",
        headers=owner.headers,
        json={
            "student_id": owner.student_id,
            "title": f"{fixture.marker}-plan-access",
            "period_type": "daily",
            "start_date": today.isoformat(),
            "end_date": today.isoformat(),
        },
    ))
    fixture.register_batch(batch["id"])
    with SessionLocal() as db:
        source = ImportFile(
            import_batch_id=batch["id"],
            file_name=f"tmp_{fixture.marker}-access.jpg",
            file_type="image",
            file_url=f"https://staged.example/{fixture.marker}-access.jpg",
            extracted_text="数学作业",
            parse_status="success",
            document_role="homework",
            recognized_title="数学四年级单元练习",
            recognition_status="success",
            match_status="not_required",
        )
        db.add(source)
        db.commit()

    missing_generate = client.post(
        f"/api/v1/plans/from-import/{batch['id']}/generate",
    )
    with SessionLocal() as db:
        unexpected_plan = db.query(AssignmentBatch).filter(
            AssignmentBatch.import_batch_id == batch["id"],
        ).first()
        if unexpected_plan:
            fixture.register_plan(unexpected_plan.id)
    assert missing_generate.status_code == 401
    forbidden_generate = client.post(
        f"/api/v1/plans/from-import/{batch['id']}/generate",
        headers=outsider.headers,
    )
    assert forbidden_generate.status_code == 403
    missing_batch = client.post(
        "/api/v1/plans/from-import/999999999/generate",
        headers=owner.headers,
    )
    assert missing_batch.status_code == 404
    with SessionLocal() as db:
        assert db.query(AssignmentBatch).filter(
            AssignmentBatch.import_batch_id == batch["id"],
        ).count() == 0

    generated = unwrap(client.post(
        f"/api/v1/plans/from-import/{batch['id']}/generate",
        headers=owner.headers,
    ))
    plan_id = generated["assignment_batch_id"]
    fixture.register_plan(plan_id)
    with SessionLocal() as db:
        item_ids_before = [row.id for row in db.query(AssignmentItem).filter(
            AssignmentItem.assignment_batch_id == plan_id,
        )]
        task_ids_before = [row.id for row in db.query(DailyTask).filter(
            DailyTask.assignment_batch_id == plan_id,
        )]

    assert client.get(f"/api/v1/plans/{plan_id}/draft").status_code == 401
    assert client.get(
        f"/api/v1/plans/{plan_id}/draft",
        headers=outsider.headers,
    ).status_code == 403
    assert client.get(
        "/api/v1/plans/999999999/draft",
        headers=owner.headers,
    ).status_code == 404

    assert client.post(
        f"/api/v1/plans/{plan_id}/confirm",
        json={},
    ).status_code == 401
    assert client.post(
        f"/api/v1/plans/{plan_id}/confirm",
        headers=outsider.headers,
        json={},
    ).status_code == 403
    assert client.post(
        "/api/v1/plans/999999999/confirm",
        headers=owner.headers,
        json={},
    ).status_code == 404
    assert client.delete(
        "/api/v1/plans/999999999/draft-items/999999999",
        headers=owner.headers,
    ).status_code == 404

    with SessionLocal() as db:
        assert db.get(AssignmentBatch, plan_id).status == "pending_confirm"
        assert [row.id for row in db.query(AssignmentItem).filter(
            AssignmentItem.assignment_batch_id == plan_id,
        )] == item_ids_before
        assert [row.id for row in db.query(DailyTask).filter(
            DailyTask.assignment_batch_id == plan_id,
        )] == task_ids_before


@pytest.mark.parametrize(
    "invalid_status",
    ["rebalanced", "archived", "unexpected", "merged"],
)
def test_confirm_rejects_non_pending_plan_states_without_mutation(
    isolated_import_fixture,
    invalid_status,
):
    fixture = isolated_import_fixture
    owner = fixture.create_parent(f"invalid-confirm-{invalid_status}")
    start = date.today()
    with SessionLocal() as db:
        invalid_target = None
        if invalid_status == "merged":
            invalid_target = AssignmentBatch(
                student_id=owner.student_id,
                title=f"{fixture.marker}-inactive-target",
                period_type="custom",
                start_date=start,
                end_date=start + timedelta(days=2),
                status="pending_confirm",
            )
            db.add(invalid_target)
            db.flush()
        plan = AssignmentBatch(
            student_id=owner.student_id,
            target_assignment_batch_id=(
                invalid_target.id if invalid_target else None
            ),
            title=f"{fixture.marker}-{invalid_status}",
            period_type="custom",
            start_date=start,
            end_date=start + timedelta(days=2),
            status=invalid_status,
        )
        db.add(plan)
        db.flush()
        item = AssignmentItem(
            assignment_batch_id=plan.id,
            subject="数学",
            title=f"{fixture.marker}-immutable-item",
            status="draft",
        )
        db.add(item)
        db.flush()
        task = DailyTask(
            student_id=owner.student_id,
            assignment_batch_id=plan.id,
            assignment_item_id=item.id,
            task_date=start + timedelta(days=1),
            subject="数学",
            title=f"{fixture.marker}-immutable-task",
            status="todo",
        )
        db.add(task)
        db.flush()
        submission = Submission(
            daily_task_id=task.id,
            student_id=owner.student_id,
            status="corrected",
            student_note=f"{fixture.marker}-immutable-submission",
        )
        db.add(submission)
        db.flush()
        correction = CorrectionResult(
            submission_id=submission.id,
            daily_task_id=task.id,
            completion_score=91,
            confidence_score=0.9,
            summary=f"{fixture.marker}-immutable-correction",
        )
        db.add(correction)
        db.commit()
        plan_id = plan.id
        item_id = item.id
        task_id = task.id
        submission_id = submission.id
        correction_id = correction.id
        target_id = invalid_target.id if invalid_target else None
    fixture.register_plan(plan_id)
    if target_id:
        fixture.register_plan(target_id)

    response = client.post(
        f"/api/v1/plans/{plan_id}/confirm",
        headers=owner.headers,
        json={"adjustments": [{"id": item_id, "title": "不得修改"}]},
    )
    assert response.status_code == 409
    with SessionLocal() as db:
        saved_plan = db.get(AssignmentBatch, plan_id)
        saved_item = db.get(AssignmentItem, item_id)
        saved_task = db.get(DailyTask, task_id)
        saved_submission = db.get(Submission, submission_id)
        saved_correction = db.get(CorrectionResult, correction_id)
        assert saved_plan.status == invalid_status
        assert saved_item.assignment_batch_id == plan_id
        assert saved_item.status == "draft"
        assert saved_task.assignment_batch_id == plan_id
        assert saved_task.task_date == start + timedelta(days=1)
        assert saved_task.status == "todo"
        assert saved_submission.student_note == f"{fixture.marker}-immutable-submission"
        assert saved_correction.summary == f"{fixture.marker}-immutable-correction"
        if target_id:
            assert db.get(AssignmentBatch, target_id).status == "pending_confirm"


def test_confirm_active_and_valid_merged_plans_are_idempotent(
    isolated_import_fixture,
):
    fixture = isolated_import_fixture
    owner = fixture.create_parent("idempotent-confirm-status")
    today = date.today()
    with SessionLocal() as db:
        active = AssignmentBatch(
            student_id=owner.student_id,
            title=f"{fixture.marker}-active",
            status="active",
            start_date=today,
            end_date=today,
        )
        db.add(active)
        db.flush()
        merged = AssignmentBatch(
            student_id=owner.student_id,
            target_assignment_batch_id=active.id,
            title=f"{fixture.marker}-merged",
            status="merged",
            start_date=today,
            end_date=today,
        )
        db.add(merged)
        db.commit()
        active_id = active.id
        merged_id = merged.id
    fixture.register_plan(active_id)
    fixture.register_plan(merged_id)

    active_response = unwrap(client.post(
        f"/api/v1/plans/{active_id}/confirm",
        headers=owner.headers,
        json={},
    ))
    merged_response = unwrap(client.post(
        f"/api/v1/plans/{merged_id}/confirm",
        headers=owner.headers,
        json={},
    ))
    assert active_response == {"plan_id": active_id, "status": "active"}
    assert merged_response == {"plan_id": active_id, "status": "active"}


def test_draft_reresolves_exact_active_target_instead_of_stale_hint(
    isolated_import_fixture,
):
    fixture = isolated_import_fixture
    owner = fixture.create_parent("stale-draft-target")
    today = date.today()
    with SessionLocal() as db:
        exact = AssignmentBatch(
            student_id=owner.student_id,
            title=f"{fixture.marker}-exact-active",
            period_type="custom",
            start_date=today,
            end_date=today + timedelta(days=2),
            status="active",
        )
        stale = AssignmentBatch(
            student_id=owner.student_id,
            title=f"{fixture.marker}-stale-active",
            period_type="custom",
            start_date=today + timedelta(days=10),
            end_date=today + timedelta(days=12),
            status="active",
        )
        db.add_all([exact, stale])
        db.flush()
        staging = AssignmentBatch(
            student_id=owner.student_id,
            target_assignment_batch_id=stale.id,
            title=f"{fixture.marker}-staging",
            period_type="custom",
            start_date=today,
            end_date=today + timedelta(days=2),
            status="pending_confirm",
        )
        db.add(staging)
        db.flush()
        exact_item = AssignmentItem(
            assignment_batch_id=exact.id,
            subject="数学",
            title=f"{fixture.marker}-exact-existing",
            status="confirmed",
        )
        stale_item = AssignmentItem(
            assignment_batch_id=stale.id,
            subject="语文",
            title=f"{fixture.marker}-stale-existing",
            status="confirmed",
        )
        new_item = AssignmentItem(
            assignment_batch_id=staging.id,
            subject="英语",
            title=f"{fixture.marker}-new-item",
            status="draft",
        )
        db.add_all([exact_item, stale_item, new_item])
        db.commit()
        exact_id = exact.id
        stale_id = stale.id
        staging_id = staging.id
    for plan_id in (exact_id, stale_id, staging_id):
        fixture.register_plan(plan_id)

    draft = unwrap(client.get(
        f"/api/v1/plans/{staging_id}/draft",
        headers=owner.headers,
    ))
    assert draft["plan"]["target_assignment_batch_id"] == exact_id
    assert [item["title"] for item in draft["existing_items"]] == [
        f"{fixture.marker}-exact-existing"
    ]
    assert f"{fixture.marker}-stale-existing" not in {
        item["title"] for item in draft["existing_items"]
    }


def test_concurrent_same_range_confirmation_has_one_canonical_active_plan(
    isolated_import_fixture,
):
    fixture = isolated_import_fixture
    owner = fixture.create_parent("concurrent-confirm")
    today = date.today()

    def create_staging(suffix: str) -> int:
        batch = unwrap(client.post(
            "/api/v1/import-batches",
            headers=owner.headers,
            json={
                "student_id": owner.student_id,
                "title": f"{fixture.marker}-{suffix}",
                "period_type": "custom",
                "start_date": today.isoformat(),
                "end_date": (today + timedelta(days=2)).isoformat(),
            },
        ))
        fixture.register_batch(batch["id"])
        with SessionLocal() as db:
            db.add(ImportFile(
                import_batch_id=batch["id"],
                file_name=f"tmp_{fixture.marker}-{suffix}.jpg",
                file_type="image",
                file_url=f"https://staged.example/{fixture.marker}-{suffix}.jpg",
                extracted_text=f"{suffix}并发作业",
                parse_status="success",
                document_role="homework",
                recognized_title=f"{fixture.marker}-{suffix}-recognized",
                recognition_status="success",
                match_status="not_required",
            ))
            db.commit()
        generated = unwrap(client.post(
            f"/api/v1/plans/from-import/{batch['id']}/generate",
            headers=owner.headers,
        ))
        fixture.register_plan(generated["assignment_batch_id"])
        return generated["assignment_batch_id"]

    plan_ids = [create_staging("concurrent-a"), create_staging("concurrent-b")]
    barrier = Barrier(2)

    def confirm_in_real_connection(plan_id: int) -> tuple[int, int, str]:
        with SessionLocal() as db:
            connection_id = db.scalar(text("SELECT CONNECTION_ID()"))
            barrier.wait(timeout=10)
            result = confirm_plan(db, plan_id)
            return connection_id, result.id, result.status

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(confirm_in_real_connection, plan_id) for plan_id in plan_ids]
        results = [future.result(timeout=20) for future in futures]

    assert len({row[0] for row in results}) == 2
    assert len({row[1] for row in results}) == 1
    assert {row[2] for row in results} == {"active"}
    canonical_id = results[0][1]
    with SessionLocal() as db:
        plans = db.query(AssignmentBatch).filter(
            AssignmentBatch.id.in_(plan_ids),
        ).order_by(AssignmentBatch.id).all()
        assert [plan.status for plan in plans].count("active") == 1
        assert [plan.status for plan in plans].count("merged") == 1
        merged_plan = next(plan for plan in plans if plan.status == "merged")
        assert merged_plan.target_assignment_batch_id == canonical_id
        assert db.query(AssignmentItem).filter(
            AssignmentItem.assignment_batch_id == canonical_id,
        ).count() == 2
        assert db.query(DailyTask).filter(
            DailyTask.assignment_batch_id == canonical_id,
        ).count() == 2
        assert db.query(AssignmentItem).filter(
            AssignmentItem.assignment_batch_id == merged_plan.id,
        ).count() == 0
        assert db.query(DailyTask).filter(
            DailyTask.assignment_batch_id == merged_plan.id,
        ).count() == 0


def test_preloaded_second_confirmation_does_not_repeat_merge_or_minutes(
    isolated_import_fixture,
):
    fixture = isolated_import_fixture
    owner = fixture.create_parent("preloaded-double-confirm")
    today = date.today()
    with SessionLocal() as db:
        canonical = AssignmentBatch(
            student_id=owner.student_id,
            title=f"{fixture.marker}-canonical",
            period_type="custom",
            start_date=today,
            end_date=today + timedelta(days=2),
            status="active",
            total_estimated_minutes=60,
        )
        staging = AssignmentBatch(
            student_id=owner.student_id,
            title=f"{fixture.marker}-staging",
            period_type="custom",
            start_date=today,
            end_date=today + timedelta(days=2),
            status="pending_confirm",
            total_estimated_minutes=60,
        )
        db.add_all([canonical, staging])
        db.flush()
        old_item = AssignmentItem(
            assignment_batch_id=canonical.id,
            subject="数学",
            title=f"{fixture.marker}-old-item",
            estimated_minutes_total=60,
            status="confirmed",
        )
        new_item = AssignmentItem(
            assignment_batch_id=staging.id,
            subject="语文",
            title=f"{fixture.marker}-new-item",
            estimated_minutes_total=60,
            status="draft",
        )
        db.add_all([old_item, new_item])
        db.flush()
        old_task = DailyTask(
            student_id=owner.student_id,
            assignment_batch_id=canonical.id,
            assignment_item_id=old_item.id,
            task_date=today,
            subject="数学",
            title=f"{fixture.marker}-old-task",
        )
        new_task = DailyTask(
            student_id=owner.student_id,
            assignment_batch_id=staging.id,
            assignment_item_id=new_item.id,
            task_date=today,
            subject="语文",
            title=f"{fixture.marker}-new-task",
        )
        db.add_all([old_task, new_task])
        db.commit()
        canonical_id = canonical.id
        staging_id = staging.id
        old_item_id = old_item.id
        new_item_id = new_item.id
        old_task_id = old_task.id
        new_task_id = new_task.id
    fixture.register_plan(canonical_id)
    fixture.register_plan(staging_id)

    first_db = SessionLocal()
    second_db = SessionLocal()
    try:
        first_connection_id = first_db.scalar(text("SELECT CONNECTION_ID()"))
        second_connection_id = second_db.scalar(text("SELECT CONNECTION_ID()"))
        assert first_connection_id != second_connection_id
        first_preloaded_plan = first_db.get(AssignmentBatch, staging_id)
        second_preloaded_plan = second_db.get(AssignmentBatch, staging_id)
        assert first_preloaded_plan.status == "pending_confirm"
        assert second_preloaded_plan.status == "pending_confirm"

        first_result = confirm_plan(first_db, staging_id)
        assert first_result.id == canonical_id
        with SessionLocal() as db:
            after_first = {
                "minutes": db.get(AssignmentBatch, canonical_id).total_estimated_minutes,
                "staging_status": db.get(AssignmentBatch, staging_id).status,
                "item_owners": {
                    row.id: row.assignment_batch_id
                    for row in db.query(AssignmentItem).filter(
                        AssignmentItem.id.in_([old_item_id, new_item_id])
                    )
                },
                "task_owners": {
                    row.id: row.assignment_batch_id
                    for row in db.query(DailyTask).filter(
                        DailyTask.id.in_([old_task_id, new_task_id])
                    )
                },
            }
        assert after_first == {
            "minutes": 120,
            "staging_status": "merged",
            "item_owners": {old_item_id: canonical_id, new_item_id: canonical_id},
            "task_owners": {old_task_id: canonical_id, new_task_id: canonical_id},
        }

        second_result = confirm_plan(second_db, staging_id)
        assert second_result.id == canonical_id
        with SessionLocal() as db:
            assert db.get(AssignmentBatch, canonical_id).total_estimated_minutes == 120
            assert db.get(AssignmentBatch, staging_id).status == "merged"
            assert {
                row.id: row.assignment_batch_id
                for row in db.query(AssignmentItem).filter(
                    AssignmentItem.id.in_([old_item_id, new_item_id])
                )
            } == after_first["item_owners"]
            assert {
                row.id: row.assignment_batch_id
                for row in db.query(DailyTask).filter(
                    DailyTask.id.in_([old_task_id, new_task_id])
                )
            } == after_first["task_owners"]
    finally:
        first_db.close()
        second_db.close()


def test_preloaded_pending_delete_rechecks_active_plan_after_lock(
    isolated_import_fixture,
):
    fixture = isolated_import_fixture
    owner = fixture.create_parent("preloaded-delete-active")
    today = date.today()
    batch = unwrap(client.post(
        "/api/v1/import-batches",
        headers=owner.headers,
        json={
            "student_id": owner.student_id,
            "title": f"{fixture.marker}-delete-batch",
            "period_type": "custom",
            "start_date": today.isoformat(),
            "end_date": today.isoformat(),
        },
    ))
    fixture.register_batch(batch["id"])
    path = upload_subdir("imports", str(batch["id"])) / "preloaded-delete.jpg"
    path.write_bytes(b"must-remain-after-active")
    with SessionLocal() as db:
        source = ImportFile(
            import_batch_id=batch["id"],
            file_name=f"tmp_{fixture.marker}-preloaded-delete.jpg",
            file_type="image",
            file_url=str(path),
            storage_path=str(path),
            extracted_text="数学练习",
            parse_status="success",
            document_role="homework",
            recognized_title="数学四年级练习",
            recognition_status="success",
            match_status="not_required",
        )
        db.add(source)
        db.commit()
        file_id = source.id
    draft = unwrap(client.post(
        f"/api/v1/plans/from-import/{batch['id']}/generate",
        headers=owner.headers,
    ))
    plan_id = draft["assignment_batch_id"]
    fixture.register_plan(plan_id)
    with SessionLocal() as db:
        item = db.query(AssignmentItem).filter(
            AssignmentItem.assignment_batch_id == plan_id,
        ).one()
        task = db.query(DailyTask).filter(
            DailyTask.assignment_batch_id == plan_id,
        ).one()
        item_id = item.id
        task_id = task.id
        plan = db.get(AssignmentBatch, plan_id)
        plan.import_batch_id = None

        history_plan = AssignmentBatch(
            student_id=owner.student_id,
            title=f"{fixture.marker}-history-plan",
            period_type="custom",
            start_date=today + timedelta(days=20),
            end_date=today + timedelta(days=20),
            status="active",
        )
        db.add(history_plan)
        db.flush()
        history_item = AssignmentItem(
            assignment_batch_id=history_plan.id,
            subject="语文",
            title=f"{fixture.marker}-history-item",
            status="confirmed",
        )
        db.add(history_item)
        db.flush()
        history_task = DailyTask(
            student_id=owner.student_id,
            assignment_batch_id=history_plan.id,
            assignment_item_id=history_item.id,
            task_date=today + timedelta(days=20),
            subject="语文",
            title=f"{fixture.marker}-history-task",
            status="corrected",
        )
        db.add(history_task)
        db.flush()
        history_submission = Submission(
            daily_task_id=history_task.id,
            student_id=owner.student_id,
            status="corrected",
            student_note=f"{fixture.marker}-history-note",
        )
        db.add(history_submission)
        db.flush()
        history_correction = CorrectionResult(
            submission_id=history_submission.id,
            daily_task_id=history_task.id,
            completion_score=94,
            confidence_score=0.92,
            summary=f"{fixture.marker}-history-summary",
        )
        db.add(history_correction)
        db.commit()
        history_plan_id = history_plan.id
        history_ids = (
            history_item.id,
            history_task.id,
            history_submission.id,
            history_correction.id,
        )
    fixture.register_plan(history_plan_id)

    preloaded = Event()
    allow_delete = Event()

    def run_delete_after_preload():
        with SessionLocal() as db:
            connection_id = db.scalar(text("SELECT CONNECTION_ID()"))
            preloaded_plan = db.get(AssignmentBatch, plan_id)
            assert preloaded_plan.status == "pending_confirm"
            user = db.get(User, owner.user_id)
            preloaded.set()
            assert allow_delete.wait(timeout=20)
            assert preloaded_plan.status == "pending_confirm"
            try:
                delete_staged_import_file(db, user, file_id)
            except Exception as exc:
                return connection_id, exc
            return connection_id, None

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(run_delete_after_preload)
        assert preloaded.wait(timeout=10)
        with SessionLocal() as confirm_db:
            confirm_connection_id = confirm_db.scalar(text("SELECT CONNECTION_ID()"))
            confirmed = confirm_plan(confirm_db, plan_id)
            assert confirmed.id == plan_id
            assert confirmed.status == "active"
        allow_delete.set()
        delete_connection_id, delete_error = future.result(timeout=20)

    assert delete_connection_id != confirm_connection_id
    assert isinstance(delete_error, StagedImportDeleteError)
    assert delete_error.status_code == 409
    assert delete_error.detail == "Active import files cannot be deleted"
    assert path.exists()
    assert path.read_bytes() == b"must-remain-after-active"
    with SessionLocal() as db:
        assert db.get(AssignmentBatch, plan_id).status == "active"
        assert db.get(ImportFile, file_id) is not None
        assert db.get(AssignmentItem, item_id) is not None
        assert db.get(DailyTask, task_id) is not None
        history_item_id, history_task_id, submission_id, correction_id = history_ids
        assert db.get(AssignmentItem, history_item_id) is not None
        assert db.get(DailyTask, history_task_id).status == "corrected"
        assert db.get(Submission, submission_id).student_note == (
            f"{fixture.marker}-history-note"
        )
        assert db.get(CorrectionResult, correction_id).summary == (
            f"{fixture.marker}-history-summary"
        )


def test_confirm_generate_delete_share_student_lock_without_deadlock(
    isolated_import_fixture,
):
    fixture = isolated_import_fixture
    owner = fixture.create_parent("student-lock-interleave")
    today = date.today()

    def create_ready_batch(suffix: str, day_offset: int, local: bool) -> tuple[int, int]:
        target_date = today + timedelta(days=day_offset)
        batch = unwrap(client.post(
            "/api/v1/import-batches",
            headers=owner.headers,
            json={
                "student_id": owner.student_id,
                "title": f"{fixture.marker}-{suffix}",
                "period_type": "custom",
                "start_date": target_date.isoformat(),
                "end_date": target_date.isoformat(),
            },
        ))
        fixture.register_batch(batch["id"])
        with SessionLocal() as db:
            if local:
                path = upload_subdir("imports", str(batch["id"])) / f"{suffix}.jpg"
                path.write_bytes(suffix.encode("utf-8"))
                file_url = str(path)
                storage_path = str(path)
            else:
                file_url = f"https://staged.example/{fixture.marker}-{suffix}.jpg"
                storage_path = None
            source = ImportFile(
                import_batch_id=batch["id"],
                file_name=f"tmp_{fixture.marker}-{suffix}.jpg",
                file_type="image",
                file_url=file_url,
                storage_path=storage_path,
                extracted_text=f"{suffix}作业",
                parse_status="success",
                document_role="homework",
                recognized_title=f"{fixture.marker}-{suffix}-recognized",
                recognition_status="success",
                match_status="not_required",
            )
            db.add(source)
            db.commit()
            file_id = source.id
        return batch["id"], file_id

    confirm_batch_id, _ = create_ready_batch("confirm", 0, False)
    confirm_draft = unwrap(client.post(
        f"/api/v1/plans/from-import/{confirm_batch_id}/generate",
        headers=owner.headers,
    ))
    confirm_plan_id = confirm_draft["assignment_batch_id"]
    fixture.register_plan(confirm_plan_id)

    generate_batch_id, _ = create_ready_batch("generate", 10, False)

    delete_batch_id, delete_file_id = create_ready_batch("delete", 20, True)
    delete_draft = unwrap(client.post(
        f"/api/v1/plans/from-import/{delete_batch_id}/generate",
        headers=owner.headers,
    ))
    delete_plan_id = delete_draft["assignment_batch_id"]
    fixture.register_plan(delete_plan_id)

    barrier = Barrier(3)

    def run_confirm() -> tuple[str, int, int]:
        with SessionLocal() as db:
            connection_id = db.scalar(text("SELECT CONNECTION_ID()"))
            barrier.wait(timeout=10)
            result = confirm_plan(db, confirm_plan_id)
            return "confirm", connection_id, result.id

    def run_generate() -> tuple[str, int, int]:
        with SessionLocal() as db:
            connection_id = db.scalar(text("SELECT CONNECTION_ID()"))
            barrier.wait(timeout=10)
            result = generate_plan_from_import(db, generate_batch_id)
            return "generate", connection_id, result.id

    def run_delete() -> tuple[str, int, list[int]]:
        with SessionLocal() as db:
            connection_id = db.scalar(text("SELECT CONNECTION_ID()"))
            user = db.get(User, owner.user_id)
            barrier.wait(timeout=10)
            result = delete_staged_import_file(db, user, delete_file_id)
            return "delete", connection_id, result

    with SessionLocal() as lock_db:
        lock_db.scalar(
            select(Student)
            .where(Student.id == owner.student_id)
            .with_for_update()
        )
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [
                executor.submit(run_confirm),
                executor.submit(run_generate),
                executor.submit(run_delete),
            ]
            done_before_release, _ = wait(futures, timeout=1)
            lock_db.commit()
            results = [future.result(timeout=20) for future in futures]

    assert done_before_release == set()
    assert len({row[1] for row in results}) == 3
    generated_plan_id = next(row[2] for row in results if row[0] == "generate")
    fixture.register_plan(generated_plan_id)
    assert next(row[2] for row in results if row[0] == "confirm") == confirm_plan_id
    assert next(row[2] for row in results if row[0] == "delete") == [delete_file_id]
    with SessionLocal() as db:
        assert db.get(AssignmentBatch, confirm_plan_id).status == "active"
        assert db.get(AssignmentBatch, generated_plan_id).status == "pending_confirm"
        assert db.get(ImportFile, delete_file_id) is None
        assert db.query(AssignmentItem).filter(
            AssignmentItem.assignment_batch_id == delete_plan_id,
        ).count() == 0


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
    assert first_context["students"] == []

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
    assert len(student_context["students"]) == 1
    joined_student_id = student_context["students"][0]["id"]
    assert any(member["user_id"] == student_login["user"]["id"] and member["relation"] == "student" for member in student_context["members"])

    with SessionLocal() as db:
        bound_student = db.get(Student, joined_student_id)
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
    student = create_joined_student_for_parent(headers, "task-stage")
    student_id = student["id"]
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


def test_imported_files_generate_one_assignment_item_per_file_with_preview_url(
    isolated_import_fixture,
):
    fixture = isolated_import_fixture
    owner = fixture.create_parent("legacy-recognized-title")
    headers = owner.headers
    student_id = owner.student_id
    today = date.today()

    batch = unwrap(client.post("/api/v1/import-batches", headers=headers, json={
        "student_id": student_id,
        "title": f"{fixture.marker}-legacy-recognized-title",
        "period_type": "daily",
        "start_date": today.isoformat(),
        "end_date": today.isoformat(),
        "raw_text": ""
    }))
    fixture.register_batch(batch["id"])

    with SessionLocal() as db:
        db.add(ImportFile(
            import_batch_id=batch["id"],
            file_name="数学周测卷.pdf",
            file_type="pdf",
            file_url="/tmp/math.pdf",
            extracted_text="数学第二周巩固练习，口算和应用题。",
            parse_status="success",
            document_role="homework",
            recognized_title="数学第二周巩固练习",
            recognition_status="success",
            content_signature_json='{"subject":"数学","kind":"周测"}',
            match_status="not_required",
            sort_order=0,
        ))
        db.add(ImportFile(
            import_batch_id=batch["id"],
            file_name="语文阅读.docx",
            file_type="docx",
            file_url="/tmp/chinese.docx",
            extracted_text="语文阅读理解专项练习。",
            parse_status="success",
            document_role="homework",
            recognized_title="语文阅读理解专项练习",
            recognition_status="success",
            content_signature_json='{"subject":"语文","kind":"阅读"}',
            match_status="not_required",
            sort_order=1,
        ))
        db.commit()

    plan = unwrap(client.post(f"/api/v1/plans/from-import/{batch['id']}/generate", headers=headers))
    fixture.register_plan(plan["assignment_batch_id"])
    draft = unwrap(client.get(f"/api/v1/plans/{plan['assignment_batch_id']}/draft", headers=headers))

    assert len(draft["assignment_items"]) == 2
    assert [item["title"] for item in draft["assignment_items"]] == [
        "数学第二周巩固练习",
        "语文阅读理解专项练习",
    ]
    assert {item["subject"] for item in draft["assignment_items"]} == {"数学", "语文"}
    assert all(item["total_quantity"] == 1 for item in draft["assignment_items"])
    assert all(item["unit"] == "份" for item in draft["assignment_items"])
    assert all(item["source_file"]["preview_url"].endswith("/preview") for item in draft["assignment_items"])
    assert [item["source_file"]["display_name"] for item in draft["assignment_items"]] == [
        "数学第二周巩固练习",
        "语文阅读理解专项练习",
    ]
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
    student = create_joined_student_for_parent(owner_headers, "page-owner")
    student_id = student["id"]
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
