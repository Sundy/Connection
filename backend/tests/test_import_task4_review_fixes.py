import json
import inspect
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.core.config import Settings
from backend.app.core.database import SessionLocal
from backend.app.models import (
    AssignmentBatch,
    AssignmentItem,
    CorrectionResult,
    DailyTask,
    Family,
    FamilyMember,
    ImportBatch,
    ImportFile,
    QuestionResult,
    Student,
    StudySession,
    Submission,
    SubmissionMedia,
    User,
)
from backend.app.services.answer_matching_service import match_batch_answers
from backend.app.services.import_file_service import (
    StagedImportDeleteError,
    delete_staged_import_file,
    import_file_display_name,
    import_file_payload,
)
import backend.app.services.import_file_service as import_file_service
from backend.app.services.local_file_service import upload_root, upload_subdir
from backend.app.services.oss_service import delete_oss_url, validate_import_oss_url
from backend.app.worker.tasks.parse_files import parse_import_file


client = TestClient(app)


@pytest.fixture
def task4_fix_fixture():
    marker = f"task4-fix-{uuid4().hex}"
    registered_paths: set[Path] = set()

    with SessionLocal() as db:
        user = User(
            openid=f"mock-openid-{marker}",
            role="parent",
            nickname=marker,
        )
        db.add(user)
        db.flush()
        family = Family(name=marker, created_by=user.id)
        db.add(family)
        db.flush()
        member = FamilyMember(
            family_id=family.id,
            user_id=user.id,
            relation="guardian",
            status="active",
        )
        student = Student(family_id=family.id, name=marker, grade="四年级")
        db.add_all([member, student])
        db.commit()
        ids = SimpleNamespace(
            user=user.id,
            family=family.id,
            student=student.id,
        )

    def create_batch(suffix: str) -> int:
        with SessionLocal() as db:
            batch = ImportBatch(
                family_id=ids.family,
                student_id=ids.student,
                title=f"{marker}-{suffix}",
                created_by=ids.user,
                status="uploaded",
            )
            db.add(batch)
            db.commit()
            return batch.id

    def valid_path(batch_id: int, name: str, content: bytes = b"content") -> Path:
        path = upload_subdir("imports", str(batch_id)) / name
        path.write_bytes(content)
        registered_paths.add(path)
        return path

    fixture = SimpleNamespace(
        marker=marker,
        ids=ids,
        create_batch=create_batch,
        valid_path=valid_path,
        register_path=lambda path: registered_paths.add(Path(path)),
        headers={"Authorization": f"Bearer dev-token-{ids.user}"},
    )
    try:
        yield fixture
    finally:
        with SessionLocal() as db:
            batch_ids = list(db.scalars(
                db.query(ImportBatch.id).filter(ImportBatch.created_by == ids.user).statement
            ))
            plan_ids = list(db.scalars(
                db.query(AssignmentBatch.id).filter(
                    AssignmentBatch.student_id == ids.student
                ).statement
            ))
            file_ids = list(db.scalars(
                db.query(ImportFile.id).filter(
                    ImportFile.import_batch_id.in_(batch_ids)
                ).statement
            )) if batch_ids else []
            item_ids = list(db.scalars(
                db.query(AssignmentItem.id).filter(
                    AssignmentItem.assignment_batch_id.in_(plan_ids)
                ).statement
            )) if plan_ids else []
            task_ids = list(db.scalars(
                db.query(DailyTask.id).filter(
                    DailyTask.assignment_batch_id.in_(plan_ids)
                ).statement
            )) if plan_ids else []
            session_ids = list(db.scalars(
                db.query(StudySession.id).filter(
                    StudySession.daily_task_id.in_(task_ids)
                ).statement
            )) if task_ids else []
            submission_ids = list(db.scalars(
                db.query(Submission.id).filter(
                    Submission.daily_task_id.in_(task_ids)
                ).statement
            )) if task_ids else []
            media_ids = list(db.scalars(
                db.query(SubmissionMedia.id).filter(
                    SubmissionMedia.submission_id.in_(submission_ids)
                ).statement
            )) if submission_ids else []
            correction_ids = list(db.scalars(
                db.query(CorrectionResult.id).filter(
                    CorrectionResult.daily_task_id.in_(task_ids)
                ).statement
            )) if task_ids else []
            question_ids = list(db.scalars(
                db.query(QuestionResult.id).filter(
                    QuestionResult.correction_result_id.in_(correction_ids)
                ).statement
            )) if correction_ids else []

            def delete_exact(model, row_ids):
                if row_ids:
                    db.query(model).filter(model.id.in_(row_ids)).delete(
                        synchronize_session=False
                    )
                    db.flush()

            delete_exact(QuestionResult, question_ids)
            delete_exact(CorrectionResult, correction_ids)
            delete_exact(SubmissionMedia, media_ids)
            delete_exact(Submission, submission_ids)
            delete_exact(StudySession, session_ids)
            delete_exact(DailyTask, task_ids)
            delete_exact(AssignmentItem, item_ids)
            delete_exact(AssignmentBatch, plan_ids)
            if file_ids:
                db.query(ImportFile).filter(ImportFile.id.in_(file_ids)).update(
                    {"matched_homework_file_id": None},
                    synchronize_session=False,
                )
                db.flush()
            delete_exact(ImportFile, file_ids)
            delete_exact(ImportBatch, batch_ids)
            delete_exact(Student, [ids.student])
            delete_exact(FamilyMember, [
                row.id for row in db.query(FamilyMember).filter(
                    FamilyMember.family_id == ids.family
                )
            ])
            delete_exact(Family, [ids.family])
            delete_exact(User, [ids.user])
            db.commit()

        for path in registered_paths:
            if path.is_file():
                path.unlink()

        for batch_id in batch_ids:
            batch_root = upload_root() / "imports" / str(batch_id)
            backup_root = batch_root / ".delete-backups"
            if backup_root.is_dir():
                for backup_dir in backup_root.iterdir():
                    if backup_dir.is_dir():
                        for backup_file in backup_dir.iterdir():
                            if backup_file.is_file():
                                backup_file.unlink()
                        backup_dir.rmdir()
                backup_root.rmdir()
            if batch_root.is_dir():
                try:
                    batch_root.rmdir()
                except OSError:
                    pass

        with SessionLocal() as db:
            marker_rows = (
                db.query(User).filter(User.openid == f"mock-openid-{marker}").count()
                + db.query(Family).filter(Family.id == ids.family).count()
                + db.query(Student).filter(Student.id == ids.student).count()
                + db.query(ImportBatch).filter(
                    ImportBatch.title.contains(marker)
                ).count()
                + db.query(AssignmentBatch).filter(
                    AssignmentBatch.title.contains(marker)
                ).count()
                + db.query(ImportFile).filter(
                    ImportFile.file_name.contains(marker)
                ).count()
            )
            assert marker_rows == 0


def test_parse_empty_content_never_uses_file_name(
    task4_fix_fixture,
    monkeypatch,
):
    fixture = task4_fix_fixture
    batch_id = fixture.create_batch("content-only")
    path = fixture.valid_path(batch_id, "opaque.bin", b"")
    with SessionLocal() as db:
        item = ImportFile(
            import_batch_id=batch_id,
            file_name="数学四年级答案-绝不能作为内容.txt",
            file_type="file",
            file_url=str(path),
            storage_path=str(path),
            document_role="homework",
            parse_status="queued",
            recognition_status="queued",
        )
        db.add(item)
        db.commit()
        file_id = item.id

    analyzed_texts: list[str] = []
    monkeypatch.setattr(
        "backend.app.worker.tasks.parse_files.extract_text_from_file",
        lambda *_args: "",
    )
    monkeypatch.setattr(
        "backend.app.worker.tasks.parse_files.extract_text_from_document",
        lambda *_args: "",
    )
    monkeypatch.setattr(
        "backend.app.worker.tasks.parse_files.analyze_import_content",
        lambda text, _role: analyzed_texts.append(text),
    )

    with pytest.raises(ValueError, match="未提取到可识别内容"):
        parse_import_file.run(file_id)

    assert analyzed_texts == []
    with SessionLocal() as db:
        saved = db.get(ImportFile, file_id)
        assert saved.parse_status == "failed"
        assert saved.recognition_status == "failed"
        assert saved.recognized_title is None
        assert "未提取到可识别内容" in saved.parse_error
        assert db.get(ImportBatch, batch_id).status == "parsed"


def test_legacy_null_role_is_homework_for_display_payload_and_matching(
    task4_fix_fixture,
):
    fixture = task4_fix_fixture
    batch_id = fixture.create_batch("legacy-role")
    signature = json.dumps({
        "subject": "数学",
        "grade_hint": "四年级",
        "chapter": "第一单元",
        "question_start": 1,
        "question_end": 10,
        "question_count": 10,
        "keywords": ["口算"],
        "is_answer": False,
    }, ensure_ascii=False)
    answer_signature = json.loads(signature)
    answer_signature["is_answer"] = True

    with SessionLocal() as db:
        homework = ImportFile(
            import_batch_id=batch_id,
            file_name="legacy.jpg",
            file_type="image",
            file_url="legacy.jpg",
            document_role=None,
            parse_status="success",
            recognition_status="success",
            recognized_title="四年级数学第一单元口算",
            content_signature_json=signature,
        )
        answer = ImportFile(
            import_batch_id=batch_id,
            file_name="answer.jpg",
            file_type="image",
            file_url="answer.jpg",
            document_role="answer",
            parse_status="success",
            recognition_status="success",
            content_signature_json=json.dumps(answer_signature, ensure_ascii=False),
        )
        db.add_all([homework, answer])
        db.commit()
        homework_id = homework.id
        answer_id = answer.id

    legacy = ImportFile(
        id=homework_id,
        import_batch_id=batch_id,
        file_name="legacy.jpg",
        file_type="image",
        file_url="legacy.jpg",
        document_role=None,
        recognition_status="pending",
    )
    assert import_file_display_name(legacy, 1) == "正在识别第 1 份作业"
    payload = import_file_payload(legacy, 1)
    assert payload["document_role"] == "homework"
    assert payload["display_name"] == "正在识别第 1 份作业"

    with SessionLocal() as db:
        match_batch_answers(db, batch_id)
    with SessionLocal() as db:
        saved = db.get(ImportFile, answer_id)
        assert saved.match_status == "matched"
        assert saved.matched_homework_file_id == homework_id


def test_answer_matching_supports_flush_only_mode(
    task4_fix_fixture,
    monkeypatch,
):
    batch_id = task4_fix_fixture.create_batch("flush-only")
    with SessionLocal() as db:
        monkeypatch.setattr(
            db,
            "commit",
            lambda: pytest.fail("flush-only matching must not commit"),
        )
        assert match_batch_answers(db, batch_id, commit=False) == []


def _create_staged_pair(fixture, suffix: str):
    batch_id = fixture.create_batch(suffix)
    homework_path = fixture.valid_path(batch_id, f"{suffix}-homework.jpg", b"homework")
    answer_path = fixture.valid_path(batch_id, f"{suffix}-answer.jpg", b"answer")
    homework_url = f"https://mock-oss.invalid/{fixture.marker}/{suffix}-homework.jpg"
    answer_url = f"https://mock-oss.invalid/{fixture.marker}/{suffix}-answer.jpg"
    with SessionLocal() as db:
        homework = ImportFile(
            import_batch_id=batch_id,
            file_name=f"{fixture.marker}-{suffix}-homework.jpg",
            file_type="image",
            file_url=homework_url,
            storage_path=str(homework_path),
            document_role="homework",
            recognized_title="四年级数学第一单元练习",
            parse_status="success",
            recognition_status="success",
            match_status="not_required",
        )
        db.add(homework)
        db.flush()
        answer = ImportFile(
            import_batch_id=batch_id,
            file_name=f"{fixture.marker}-{suffix}-answer.jpg",
            file_type="image",
            file_url=answer_url,
            storage_path=str(answer_path),
            document_role="answer",
            parse_status="success",
            recognition_status="success",
            match_status="matched",
            matched_homework_file_id=homework.id,
        )
        db.add(answer)
        db.flush()
        plan = AssignmentBatch(
            student_id=fixture.ids.student,
            import_batch_id=batch_id,
            title=f"{fixture.marker}-{suffix}-plan",
            status="pending_confirm",
        )
        db.add(plan)
        db.flush()
        item = AssignmentItem(
            assignment_batch_id=plan.id,
            subject="数学",
            title="第一单元练习",
            import_file_id=homework.id,
        )
        db.add(item)
        db.flush()
        task = DailyTask(
            student_id=fixture.ids.student,
            assignment_batch_id=plan.id,
            assignment_item_id=item.id,
            task_date=datetime.now(UTC).date(),
            subject="数学",
            title="第一单元练习",
        )
        db.add(task)
        db.commit()
        return SimpleNamespace(
            batch_id=batch_id,
            homework_id=homework.id,
            answer_id=answer.id,
            plan_id=plan.id,
            item_id=item.id,
            task_id=task.id,
            homework_path=homework_path,
            answer_path=answer_path,
            homework_url=homework_url,
            answer_url=answer_url,
        )


@pytest.mark.parametrize("escape_kind", ["traversal", "absolute", "symlink"])
def test_staged_delete_rejects_local_path_escape(
    task4_fix_fixture,
    tmp_path,
    escape_kind,
):
    fixture = task4_fix_fixture
    batch_id = fixture.create_batch(f"escape-{escape_kind}")
    batch_root = upload_subdir("imports", str(batch_id))
    outside = tmp_path / f"{escape_kind}-outside.jpg"
    outside.write_bytes(b"must-remain")
    if escape_kind == "traversal":
        storage_path = str(batch_root / ".." / outside.name)
        traversal_target = batch_root.parent / outside.name
        traversal_target.write_bytes(b"must-remain")
        fixture.register_path(traversal_target)
        protected_path = traversal_target
    elif escape_kind == "absolute":
        storage_path = str(outside.resolve())
        protected_path = outside
    else:
        link = batch_root / "escape-link.jpg"
        link.symlink_to(outside)
        fixture.register_path(link)
        storage_path = str(link)
        protected_path = outside

    with SessionLocal() as db:
        item = ImportFile(
            import_batch_id=batch_id,
            file_name=f"{fixture.marker}-{escape_kind}.jpg",
            file_type="image",
            file_url=storage_path,
            storage_path=storage_path,
            document_role="homework",
        )
        db.add(item)
        db.commit()
        file_id = item.id

    with SessionLocal() as db:
        user = db.get(User, fixture.ids.user)
        with pytest.raises(StagedImportDeleteError, match="outside import storage root"):
            delete_staged_import_file(db, user, file_id)

    assert protected_path.exists()
    with SessionLocal() as db:
        assert db.get(ImportFile, file_id) is not None


def test_delete_oss_url_rejects_foreign_bucket_without_sdk_call(monkeypatch):
    config = Settings(
        aliyun_access_key_id="test-id",
        aliyun_access_key_secret="test-secret",
        aliyun_oss_endpoint="oss-cn-shenzhen.aliyuncs.com",
        aliyun_oss_bucket="owned-bucket",
    )
    sdk_calls: list[str] = []
    monkeypatch.setattr(
        "backend.app.services.oss_service.oss2",
        SimpleNamespace(Auth=lambda *_args: sdk_calls.append("auth")),
    )

    with pytest.raises(ValueError, match="not owned"):
        delete_oss_url(
            "https://foreign-bucket.oss-cn-shenzhen.aliyuncs.com/path/file.jpg",
            config,
        )
    with pytest.raises(ValueError, match="outside the owned import batch prefix"):
        validate_import_oss_url(
            "https://owned-bucket.oss-cn-shenzhen.aliyuncs.com/connection/submissions/file.jpg",
            123,
            config,
        )

    assert sdk_calls == []


def _install_memory_oss(monkeypatch, urls: set[str], fail_url: str | None = None):
    backups: dict[str, str] = {}
    discarded: list[str] = []

    def create_backup(url: str, _batch_id: int):
        assert url in urls
        backups[url] = url
        return url

    def delete_original(url: str):
        if url == fail_url:
            raise RuntimeError("second object delete failed")
        urls.remove(url)

    def restore(backup: str):
        urls.add(backup)

    def discard(backup: str):
        discarded.append(backup)
        backups.pop(backup, None)

    monkeypatch.setattr(
        "backend.app.services.import_file_service.validate_import_oss_url",
        lambda url, _batch_id: url,
    )

    monkeypatch.setattr(
        "backend.app.services.import_file_service.create_oss_delete_backup",
        create_backup,
        raising=False,
    )
    monkeypatch.setattr(
        "backend.app.services.import_file_service.delete_oss_url",
        delete_original,
    )
    monkeypatch.setattr(
        "backend.app.services.import_file_service.restore_oss_delete_backup",
        restore,
        raising=False,
    )
    monkeypatch.setattr(
        "backend.app.services.import_file_service.discard_oss_delete_backup",
        discard,
        raising=False,
    )
    return backups, discarded


def test_pair_second_storage_failure_restores_every_object_and_row(
    task4_fix_fixture,
    monkeypatch,
):
    fixture = task4_fix_fixture
    pair = _create_staged_pair(fixture, "second-failure")
    urls = {pair.homework_url, pair.answer_url}
    _install_memory_oss(monkeypatch, urls, fail_url=pair.answer_url)

    with SessionLocal() as db:
        user = db.get(User, fixture.ids.user)
        with pytest.raises(StagedImportDeleteError, match="delete staged file storage"):
            delete_staged_import_file(db, user, pair.homework_id)

    assert urls == {pair.homework_url, pair.answer_url}
    assert pair.homework_path.read_bytes() == b"homework"
    assert pair.answer_path.read_bytes() == b"answer"
    with SessionLocal() as db:
        assert db.get(ImportFile, pair.homework_id) is not None
        assert db.get(ImportFile, pair.answer_id) is not None
        assert db.get(AssignmentItem, pair.item_id) is not None
        assert db.get(DailyTask, pair.task_id) is not None


def test_db_commit_failure_rolls_back_rows_and_restores_storage(
    task4_fix_fixture,
    monkeypatch,
):
    fixture = task4_fix_fixture
    pair = _create_staged_pair(fixture, "commit-failure")
    urls = {pair.homework_url, pair.answer_url}
    _install_memory_oss(monkeypatch, urls)

    with SessionLocal() as db:
        user = db.get(User, fixture.ids.user)
        monkeypatch.setattr(
            db,
            "commit",
            lambda: (_ for _ in ()).throw(RuntimeError("injected commit failure")),
        )
        with pytest.raises(StagedImportDeleteError, match="database"):
            delete_staged_import_file(db, user, pair.homework_id)

    assert urls == {pair.homework_url, pair.answer_url}
    assert pair.homework_path.read_bytes() == b"homework"
    assert pair.answer_path.read_bytes() == b"answer"
    with SessionLocal() as db:
        assert db.get(ImportFile, pair.homework_id) is not None
        assert db.get(ImportFile, pair.answer_id) is not None
        assert db.get(AssignmentItem, pair.item_id) is not None
        assert db.get(DailyTask, pair.task_id) is not None


def test_delete_locks_linearize_concurrent_plan_activation(
    task4_fix_fixture,
    monkeypatch,
):
    fixture = task4_fix_fixture
    pair = _create_staged_pair(fixture, "locking")
    urls = {pair.homework_url, pair.answer_url}
    _install_memory_oss(monkeypatch, urls)
    deletion_at_storage = threading.Event()
    allow_deletion = threading.Event()
    activation_ready = threading.Event()
    activation_done = threading.Event()
    thread_errors: list[Exception] = []
    original_delete_storage = import_file_service._delete_storage

    def paused_delete_storage(snapshot):
        deletion_at_storage.set()
        if not allow_deletion.wait(timeout=5):
            raise RuntimeError("test timed out waiting to continue deletion")
        original_delete_storage(snapshot)

    monkeypatch.setattr(import_file_service, "_delete_storage", paused_delete_storage)

    def delete_in_connection():
        try:
            with SessionLocal() as db:
                user = db.get(User, fixture.ids.user)
                delete_staged_import_file(db, user, pair.homework_id)
        except Exception as exc:  # pragma: no cover - asserted below
            thread_errors.append(exc)

    def activate_in_connection():
        try:
            with SessionLocal() as db:
                plan = db.get(AssignmentBatch, pair.plan_id)
                plan.status = "active"
                activation_ready.set()
                db.commit()
        except Exception as exc:  # pragma: no cover - asserted below
            thread_errors.append(exc)
        finally:
            activation_done.set()

    delete_thread = threading.Thread(target=delete_in_connection)
    activate_thread = threading.Thread(target=activate_in_connection)
    delete_thread.start()
    assert deletion_at_storage.wait(timeout=5)
    activate_thread.start()
    try:
        assert activation_ready.wait(timeout=5)
        assert not activation_done.wait(timeout=0.5)
    finally:
        allow_deletion.set()
        delete_thread.join(timeout=10)
        activate_thread.join(timeout=10)

    assert thread_errors == []
    assert activation_done.is_set()
    with SessionLocal() as db:
        assert db.get(ImportFile, pair.homework_id) is None
        assert db.get(ImportFile, pair.answer_id) is None
        assert db.get(AssignmentItem, pair.item_id) is None
        assert db.get(DailyTask, pair.task_id) is None
        assert db.get(AssignmentBatch, pair.plan_id).status == "active"


def test_parse_claims_null_and_stale_rows_once(
    task4_fix_fixture,
    monkeypatch,
):
    fixture = task4_fix_fixture
    batch_id = fixture.create_batch("parse-lease")
    now = datetime.now(UTC).replace(tzinfo=None)
    with SessionLocal() as db:
        legacy = ImportFile(
            import_batch_id=batch_id,
            file_name=f"{fixture.marker}-legacy.jpg",
            file_type="image",
            file_url="legacy.jpg",
            parse_status=None,
            recognition_status=None,
        )
        stale = ImportFile(
            import_batch_id=batch_id,
            file_name=f"{fixture.marker}-stale.jpg",
            file_type="image",
            file_url="stale.jpg",
            parse_status="queued",
            recognition_status="queued",
            updated_at=now - timedelta(hours=1),
        )
        fresh = ImportFile(
            import_batch_id=batch_id,
            file_name=f"{fixture.marker}-fresh.jpg",
            file_type="image",
            file_url="fresh.jpg",
            parse_status="queued",
            recognition_status="queued",
            updated_at=now,
        )
        db.add_all([legacy, stale, fresh])
        db.commit()
        db.query(ImportFile).filter(ImportFile.id == legacy.id).update({
            "parse_status": None,
            "recognition_status": None,
        })
        db.commit()
        expected = [legacy.id, stale.id]

    dispatched: list[int] = []
    monkeypatch.setattr(
        "backend.app.api.routers.imports.parse_import_file.delay",
        lambda file_id: dispatched.append(file_id),
    )
    first = client.post(
        f"/api/v1/import-batches/{batch_id}/parse",
        headers=fixture.headers,
    )
    second = client.post(
        f"/api/v1/import-batches/{batch_id}/parse",
        headers=fixture.headers,
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert dispatched == expected
    with SessionLocal() as db:
        assert db.get(ImportFile, expected[0]).parse_status == "queued"
        assert db.get(ImportFile, expected[1]).parse_status == "queued"


def test_concurrent_parse_requests_dispatch_each_file_once(
    task4_fix_fixture,
    monkeypatch,
):
    fixture = task4_fix_fixture
    batch_id = fixture.create_batch("parse-concurrent")
    with SessionLocal() as db:
        item = ImportFile(
            import_batch_id=batch_id,
            file_name=f"{fixture.marker}-concurrent.jpg",
            file_type="image",
            file_url="concurrent.jpg",
            parse_status="pending",
            recognition_status="pending",
        )
        db.add(item)
        db.commit()
        file_id = item.id

    dispatched: list[int] = []
    dispatch_lock = threading.Lock()

    def record_dispatch(claimed_id: int):
        with dispatch_lock:
            dispatched.append(claimed_id)

    monkeypatch.setattr(
        "backend.app.api.routers.imports.parse_import_file.delay",
        record_dispatch,
    )
    barrier = threading.Barrier(3)
    statuses: list[int] = []

    def request_parse():
        barrier.wait()
        response = TestClient(app).post(
            f"/api/v1/import-batches/{batch_id}/parse",
            headers=fixture.headers,
        )
        statuses.append(response.status_code)

    threads = [threading.Thread(target=request_parse) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=10)

    assert statuses == [200, 200]
    assert dispatched == [file_id]


def test_parse_broker_failure_releases_claim_and_returns_503(
    task4_fix_fixture,
    monkeypatch,
):
    fixture = task4_fix_fixture
    batch_id = fixture.create_batch("broker-failure")
    with SessionLocal() as db:
        item = ImportFile(
            import_batch_id=batch_id,
            file_name=f"{fixture.marker}-broker.jpg",
            file_type="image",
            file_url="broker.jpg",
            parse_status="pending",
            recognition_status="pending",
        )
        db.add(item)
        db.commit()
        file_id = item.id

    monkeypatch.setattr(
        "backend.app.api.routers.imports.parse_import_file.delay",
        lambda _file_id: (_ for _ in ()).throw(RuntimeError("broker unavailable")),
    )
    response = TestClient(app, raise_server_exceptions=False).post(
        f"/api/v1/import-batches/{batch_id}/parse",
        headers=fixture.headers,
    )

    assert response.status_code == 503
    with SessionLocal() as db:
        item = db.get(ImportFile, file_id)
        batch = db.get(ImportBatch, batch_id)
        assert item.parse_status == "failed"
        assert item.recognition_status == "failed"
        assert batch.status == "parsed"


def test_unauthorized_parse_dispatches_nothing(task4_fix_fixture, monkeypatch):
    batch_id = task4_fix_fixture.create_batch("unauthorized")
    dispatched: list[int] = []
    monkeypatch.setattr(
        "backend.app.api.routers.imports.parse_import_file.delay",
        lambda file_id: dispatched.append(file_id),
    )

    response = client.post(f"/api/v1/import-batches/{batch_id}/parse")

    assert response.status_code == 401
    assert dispatched == []


def test_worker_completion_waits_for_legacy_null_state(
    task4_fix_fixture,
    monkeypatch,
):
    fixture = task4_fix_fixture
    batch_id = fixture.create_batch("worker-convergence")
    first_path = fixture.valid_path(batch_id, "first.txt", b"first")
    legacy_path = fixture.valid_path(batch_id, "legacy.txt", b"legacy")
    with SessionLocal() as db:
        batch = db.get(ImportBatch, batch_id)
        batch.status = "parsing"
        first = ImportFile(
            import_batch_id=batch_id,
            file_name="first.txt",
            file_type="file",
            file_url=str(first_path),
            storage_path=str(first_path),
            parse_status="queued",
            recognition_status="queued",
        )
        legacy = ImportFile(
            import_batch_id=batch_id,
            file_name="legacy.txt",
            file_type="file",
            file_url=str(legacy_path),
            storage_path=str(legacy_path),
            parse_status=None,
            recognition_status=None,
        )
        db.add_all([first, legacy])
        db.commit()
        db.query(ImportFile).filter(ImportFile.id == legacy.id).update({
            "parse_status": None,
            "recognition_status": None,
        })
        db.commit()
        first_id = first.id
        legacy_id = legacy.id

    monkeypatch.setattr(
        "backend.app.worker.tasks.parse_files.extract_text_from_file",
        lambda *_args: "数学四年级第一单元练习",
    )
    monkeypatch.setattr(
        "backend.app.worker.tasks.parse_files.analyze_import_content",
        lambda _text, _role: {
            "recognized_title": "数学四年级第一单元练习",
            "recognition_status": "success",
            "signature": {"content_summary": "第一单元"},
        },
    )
    parse_import_file.run(first_id)

    with SessionLocal() as db:
        assert db.get(ImportBatch, batch_id).status == "parsing"

    parse_import_file.run(legacy_id)

    with SessionLocal() as db:
        assert db.get(ImportBatch, batch_id).status == "parsed"


def test_storage_restore_failure_reports_original_and_restore_errors(
    task4_fix_fixture,
    monkeypatch,
):
    fixture = task4_fix_fixture
    pair = _create_staged_pair(fixture, "restore-failure")
    urls = {pair.homework_url, pair.answer_url}
    _install_memory_oss(monkeypatch, urls, fail_url=pair.answer_url)
    monkeypatch.setattr(
        "backend.app.services.import_file_service.restore_oss_delete_backup",
        lambda _backup: (_ for _ in ()).throw(RuntimeError("restore unavailable")),
    )

    with SessionLocal() as db:
        user = db.get(User, fixture.ids.user)
        with pytest.raises(StagedImportDeleteError) as captured:
            delete_staged_import_file(db, user, pair.homework_id)

    assert "Failed to delete staged file storage" in captured.value.detail
    assert "second object delete failed" in captured.value.detail
    assert "restoration failed" in captured.value.detail
    assert "restore unavailable" in captured.value.detail
    with SessionLocal() as db:
        assert db.get(ImportFile, pair.homework_id) is not None
        assert db.get(ImportFile, pair.answer_id) is not None


def test_staged_delete_rejects_existing_study_history(
    task4_fix_fixture,
    monkeypatch,
):
    fixture = task4_fix_fixture
    pair = _create_staged_pair(fixture, "history")
    with SessionLocal() as db:
        db.add(StudySession(
            daily_task_id=pair.task_id,
            student_id=fixture.ids.student,
            status="completed",
        ))
        db.commit()
    deleted_urls: list[str] = []
    monkeypatch.setattr(
        "backend.app.services.import_file_service.delete_oss_url",
        lambda url: deleted_urls.append(url),
    )

    with SessionLocal() as db:
        user = db.get(User, fixture.ids.user)
        with pytest.raises(StagedImportDeleteError, match="study history"):
            delete_staged_import_file(db, user, pair.homework_id)

    assert deleted_urls == []
    assert pair.homework_path.exists()
    assert pair.answer_path.exists()


def test_answer_only_deletion_rematches_remaining_answer(
    task4_fix_fixture,
):
    fixture = task4_fix_fixture
    batch_id = fixture.create_batch("answer-rematch")
    homework_path = fixture.valid_path(batch_id, "rematch-homework.jpg")
    first_path = fixture.valid_path(batch_id, "rematch-first-answer.jpg")
    second_path = fixture.valid_path(batch_id, "rematch-second-answer.jpg")
    signature = {
        "subject": "数学",
        "grade_hint": "四年级",
        "chapter": "第一单元",
        "question_start": 1,
        "question_end": 10,
        "question_count": 10,
        "keywords": ["口算"],
    }
    with SessionLocal() as db:
        homework = ImportFile(
            import_batch_id=batch_id,
            file_name="homework.jpg",
            file_type="image",
            file_url=str(homework_path),
            storage_path=str(homework_path),
            document_role="homework",
            recognition_status="success",
            content_signature_json=json.dumps({**signature, "is_answer": False}),
        )
        db.add(homework)
        db.flush()
        first = ImportFile(
            import_batch_id=batch_id,
            file_name="first.jpg",
            file_type="image",
            file_url=str(first_path),
            storage_path=str(first_path),
            document_role="answer",
            recognition_status="success",
            match_status="matched",
            matched_homework_file_id=homework.id,
            content_signature_json=json.dumps({**signature, "is_answer": True}),
        )
        second = ImportFile(
            import_batch_id=batch_id,
            file_name="second.jpg",
            file_type="image",
            file_url=str(second_path),
            storage_path=str(second_path),
            document_role="answer",
            recognition_status="success",
            match_status="unmatched",
            content_signature_json=json.dumps({**signature, "is_answer": True}),
        )
        db.add_all([first, second])
        db.commit()
        homework_id = homework.id
        first_id = first.id
        second_id = second.id

    with SessionLocal() as db:
        user = db.get(User, fixture.ids.user)
        assert delete_staged_import_file(db, user, first_id) == [first_id]

    with SessionLocal() as db:
        assert db.get(ImportFile, first_id) is None
        remaining = db.get(ImportFile, second_id)
        assert remaining.match_status == "matched"
        assert remaining.matched_homework_file_id == homework_id


def test_upload_commit_failure_removes_new_local_and_oss_objects(
    task4_fix_fixture,
    monkeypatch,
):
    fixture = task4_fix_fixture
    batch_id = fixture.create_batch("upload-compensation")
    batch_root = upload_subdir("imports", str(batch_id))
    before_paths = set(batch_root.iterdir())
    remote_objects: set[str] = set()

    def fake_upload(_path: str, object_key: str):
        url = f"https://mock-oss.invalid/{object_key}"
        remote_objects.add(url)
        return url

    monkeypatch.setattr(
        "backend.app.api.routers.imports.upload_file_to_oss",
        fake_upload,
    )
    monkeypatch.setattr(
        "backend.app.api.routers.imports.delete_oss_url",
        lambda url: remote_objects.remove(url),
        raising=False,
    )
    monkeypatch.setattr(
        "backend.app.api.routers.imports._commit_import_upload",
        lambda _db: (_ for _ in ()).throw(RuntimeError("injected upload commit failure")),
        raising=False,
    )

    response = TestClient(app, raise_server_exceptions=False).post(
        f"/api/v1/import-batches/{batch_id}/files",
        headers=fixture.headers,
        data={"file_type": "image", "document_role": "homework"},
        files={"file": ("upload.jpg", b"upload-content", "image/jpeg")},
    )

    assert response.status_code == 500
    assert remote_objects == set()
    assert set(batch_root.iterdir()) == before_paths
    with SessionLocal() as db:
        assert db.query(ImportFile).filter(
            ImportFile.import_batch_id == batch_id
        ).count() == 0
    from backend.app.api.routers.imports import upload_import_file

    assert inspect.iscoroutinefunction(upload_import_file) is False
