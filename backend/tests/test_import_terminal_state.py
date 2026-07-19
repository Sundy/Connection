import threading
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.core.database import SessionLocal
from backend.app.main import app
from backend.app.models import (
    AssignmentBatch,
    AssignmentItem,
    DailyTask,
    ImportBatch,
    ImportFile,
)
from backend.tests.test_import_task4_review_fixes import task4_fix_fixture


client = TestClient(app)


def _add_plan_and_file(fixture, *, plan_status: str = "pending_confirm") -> tuple[int, int, int]:
    batch_id = fixture.create_batch(f"terminal-{plan_status}")
    storage_path = fixture.valid_path(batch_id, "existing.pdf", b"existing")
    with SessionLocal() as db:
        batch = db.get(ImportBatch, batch_id)
        batch.start_date = date(2026, 7, 20)
        batch.end_date = date(2026, 7, 21)
        file = ImportFile(
            import_batch_id=batch_id,
            file_name="existing.pdf",
            file_type="pdf",
            file_url=str(storage_path),
            storage_path=str(storage_path),
            file_size=8,
            document_role="homework",
            parse_status="success",
            recognition_status="success",
            recognized_title="数学单元练习",
            match_status="not_required",
        )
        plan = AssignmentBatch(
            student_id=fixture.ids.student,
            import_batch_id=batch_id,
            title="数学练习计划",
            period_type="custom",
            start_date=batch.start_date,
            end_date=batch.end_date,
            status=plan_status,
            total_estimated_minutes=60,
        )
        db.add_all([file, plan])
        db.flush()
        item = AssignmentItem(
            assignment_batch_id=plan.id,
            import_file_id=file.id,
            subject="数学",
            title="数学单元练习",
            source_text="数学练习",
            total_quantity=1,
            unit="份",
            estimated_minutes_total=60,
            status="draft" if plan_status == "pending_confirm" else "confirmed",
        )
        db.add(item)
        db.flush()
        db.add(DailyTask(
            student_id=fixture.ids.student,
            assignment_batch_id=plan.id,
            assignment_item_id=item.id,
            task_date=batch.start_date,
            subject="数学",
            title="完成数学练习",
            planned_quantity=1,
            unit="份",
            estimated_minutes=60,
        ))
        if plan_status != "pending_confirm":
            batch.status = "confirmed"
        db.commit()
        return batch_id, file.id, plan.id


def _assert_immutable_response(response) -> None:
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail == {
        "code": "import_batch_immutable",
        "message": "该批作业已确认，不能再修改",
    }


def test_confirmed_import_rejects_upload_patch_parse_and_generate_without_changes(
    task4_fix_fixture,
    monkeypatch,
):
    fixture = task4_fix_fixture
    batch_id, file_id, plan_id = _add_plan_and_file(fixture, plan_status="active")
    batch_dir = fixture.valid_path(batch_id, "sentinel.txt", b"sentinel").parent
    paths_before = sorted(path.name for path in batch_dir.iterdir())
    dispatched = []
    monkeypatch.setattr(
        "backend.app.api.routers.imports.parse_import_file.delay",
        lambda *args: dispatched.append(args),
    )

    responses = [
        client.post(
            f"/api/v1/import-batches/{batch_id}/files",
            headers=fixture.headers,
            files={"file": ("secret.pdf", b"new-content", "application/pdf")},
            data={"file_type": "pdf", "document_role": "homework"},
        ),
        client.patch(
            f"/api/v1/import-batches/{batch_id}",
            headers=fixture.headers,
            json={"raw_text": "不应保存"},
        ),
        client.post(
            f"/api/v1/import-batches/{batch_id}/parse",
            headers=fixture.headers,
        ),
        client.post(
            f"/api/v1/plans/from-import/{batch_id}/generate",
            headers=fixture.headers,
        ),
    ]

    for response in responses:
        _assert_immutable_response(response)
    assert dispatched == []
    assert sorted(path.name for path in batch_dir.iterdir()) == paths_before
    with SessionLocal() as db:
        batch = db.get(ImportBatch, batch_id)
        assert batch.status == "confirmed"
        assert batch.raw_text is None
        assert db.query(ImportFile).filter(ImportFile.import_batch_id == batch_id).count() == 1
        assert db.get(ImportFile, file_id).parse_status == "success"
        assert db.get(AssignmentBatch, plan_id).status == "active"


def test_confirmed_import_read_contract_exposes_canonical_plan(task4_fix_fixture):
    fixture = task4_fix_fixture
    batch_id, _file_id, plan_id = _add_plan_and_file(fixture, plan_status="active")

    response = client.get(
        f"/api/v1/import-batches/{batch_id}",
        headers=fixture.headers,
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["can_edit"] is False
    assert data["read_only"] is True
    assert data["canonical_plan_id"] == plan_id


def test_storage_failure_response_never_leaks_internal_location(task4_fix_fixture, monkeypatch):
    fixture = task4_fix_fixture
    batch_id = fixture.create_batch("storage-leak")
    storage_path = fixture.valid_path(batch_id, "leak.pdf", b"leak")
    secret = "/private/secret/import.pdf oss://bucket/private-key https://internal-oss:9000"
    with SessionLocal() as db:
        item = ImportFile(
            import_batch_id=batch_id,
            file_name="leak.pdf",
            file_type="pdf",
            file_url=str(storage_path),
            storage_path=str(storage_path),
            file_size=4,
            document_role="homework",
            parse_status="success",
            recognition_status="success",
            recognized_title="数学练习",
        )
        db.add(item)
        db.commit()
        file_id = item.id

    monkeypatch.setattr(
        "backend.app.services.import_file_service._prepare_storage_snapshot",
        lambda _items: (_ for _ in ()).throw(RuntimeError(secret)),
    )
    response = client.delete(
        f"/api/v1/import-batches/files/{file_id}",
        headers=fixture.headers,
    )

    assert response.status_code == 502
    rendered = response.text
    assert secret not in rendered
    assert "/private/secret" not in rendered
    assert "private-key" not in rendered
    assert "internal-oss" not in rendered
    assert response.json()["detail"] == {
        "code": "import_storage_backup_failed",
        "message": "暂时无法删除文件，请稍后重试",
    }
    with SessionLocal() as db:
        assert db.get(ImportFile, file_id) is not None
        assert db.get(ImportBatch, batch_id).status == "uploaded"


def test_confirm_lock_wins_then_upload_waits_and_returns_immutable_without_side_effects(
    task4_fix_fixture,
    monkeypatch,
):
    fixture = task4_fix_fixture
    batch_id, _file_id, plan_id = _add_plan_and_file(fixture)
    batch_dir = Path(fixture.valid_path(batch_id, "sentinel.txt", b"sentinel")).parent
    paths_before = sorted(path.name for path in batch_dir.iterdir())
    attempting_lock = threading.Event()
    storage_called = threading.Event()
    real_lock = __import__(
        "backend.app.services.import_lock_service",
        fromlist=["lock_import_batch_files"],
    ).lock_import_batch_files

    def observed_lock(db, locked_batch_id):
        if locked_batch_id == batch_id:
            attempting_lock.set()
        return real_lock(db, locked_batch_id)

    monkeypatch.setattr(
        "backend.app.services.import_state_service.lock_import_batch_files",
        observed_lock,
    )
    monkeypatch.setattr(
        "backend.app.api.routers.imports.upload_file_to_oss",
        lambda *_args: storage_called.set() or "",
    )

    first = SessionLocal()
    real_lock(first, batch_id)
    outcome = {}

    def upload():
        outcome["response"] = client.post(
            f"/api/v1/import-batches/{batch_id}/files",
            headers=fixture.headers,
            files={"file": ("late.pdf", b"late", "application/pdf")},
            data={"file_type": "pdf", "document_role": "homework"},
        )

    thread = threading.Thread(target=upload)
    thread.start()
    try:
        assert attempting_lock.wait(timeout=5), "上传线程应已尝试获取批次锁"
        assert storage_called.is_set() is False
        plan = first.get(AssignmentBatch, plan_id)
        plan.status = "active"
        first.get(ImportBatch, batch_id).status = "confirmed"
        first.commit()
    finally:
        first.close()
    thread.join(timeout=10)
    assert thread.is_alive() is False
    _assert_immutable_response(outcome["response"])
    assert storage_called.is_set() is False
    assert sorted(path.name for path in batch_dir.iterdir()) == paths_before
    with SessionLocal() as db:
        assert db.query(ImportFile).filter(ImportFile.import_batch_id == batch_id).count() == 1
