from datetime import date
from uuid import uuid4

from backend.app.core.config import Settings
from backend.app.core.database import SessionLocal, init_db
from backend.app.models import AssignmentBatch, AssignmentItem, CorrectionResult, DailyTask, Family, Student, Submission, SubmissionMedia, User
from backend.app.services.ai_config import api_key_for, service_is_configured
from backend.app.services.asr_service import transcribe_audio_url
from backend.app.services.correction_ai_service import build_ai_correction_payload
from backend.app.services.document_extract_service import extract_text_from_document
from backend.app.services.media_processing_service import prepare_audio_url
from backend.app.services.oss_service import (
    build_import_object_key,
    build_public_url,
    build_submission_object_key,
    oss_is_configured,
    signed_download_url,
)
from backend.app.worker.tasks.correct_homework import run_homework_correction


init_db()


def create_correction_submission(*, status="uploaded"):
    with SessionLocal() as db:
        user = User(openid=f"correction-{uuid4().hex}", role="parent", nickname="家长")
        db.add(user)
        db.flush()
        family = Family(name="批改测试家庭", created_by=user.id)
        db.add(family)
        db.flush()
        student = Student(family_id=family.id, name="测试学生", grade="四年级")
        db.add(student)
        db.flush()
        plan = AssignmentBatch(student_id=student.id, title="批改测试计划")
        db.add(plan)
        db.flush()
        item = AssignmentItem(assignment_batch_id=plan.id, subject="数学", title="口算")
        db.add(item)
        db.flush()
        task = DailyTask(
            student_id=student.id,
            assignment_batch_id=plan.id,
            assignment_item_id=item.id,
            task_date=date.today(),
            subject="数学",
            title="口算",
            status="correcting",
        )
        db.add(task)
        db.flush()
        submission = Submission(
            daily_task_id=task.id,
            student_id=student.id,
            submission_type="photo",
            status=status,
        )
        db.add(submission)
        db.commit()
        return submission.id, task.id


def test_correction_failure_is_persisted_without_mock_result(monkeypatch):
    submission_id, task_id = create_correction_submission()

    def fail_correction(db, submission):
        raise RuntimeError("upstream secret detail")

    monkeypatch.setattr("backend.app.worker.tasks.correct_homework.create_correction", fail_correction, raising=False)
    response = run_homework_correction.run(submission_id)

    assert response["ok"] is False
    with SessionLocal() as db:
        submission = db.get(Submission, submission_id)
        task = db.get(DailyTask, task_id)
        assert submission.status == "failed"
        assert submission.error_code == "correction_failed"
        assert submission.error_message == "批改服务暂时不可用，请稍后重试。"
        assert task.status == "failed"
        assert db.query(CorrectionResult).filter(CorrectionResult.submission_id == submission_id).count() == 0


def test_correction_worker_is_idempotent_for_terminal_submission():
    submission_id, task_id = create_correction_submission(status="corrected")
    with SessionLocal() as db:
        result = CorrectionResult(submission_id=submission_id, daily_task_id=task_id, completion_score=90, confidence_score=0.9)
        db.add(result)
        db.commit()
        result_id = result.id

    response = run_homework_correction.run(submission_id)

    assert response == {"ok": True, "correction_result_id": result_id, "status": "corrected"}
    with SessionLocal() as db:
        assert db.query(CorrectionResult).filter(CorrectionResult.submission_id == submission_id).count() == 1


def test_service_configuration_uses_shared_dashscope_key_when_specific_key_missing():
    settings = Settings(dashscope_api_key="shared-key", llm_api_key="", ocr_api_key="")

    assert api_key_for(settings, "llm") == "shared-key"
    assert api_key_for(settings, "ocr") == "shared-key"
    assert service_is_configured(settings, "llm") is True
    assert service_is_configured(settings, "ocr") is True


def test_service_configuration_treats_placeholder_keys_as_disabled():
    settings = Settings(dashscope_api_key="请替换为你的DashScope API Key")

    assert service_is_configured(settings, "llm") is False
    assert service_is_configured(settings, "ocr") is False


def test_asr_openai_compatible_requires_public_audio_url():
    settings = Settings(dashscope_api_key="shared-key", asr_provider="qwen")

    assert transcribe_audio_url("/tmp/local.wav", settings=settings) == ""


def test_oss_configuration_uses_existing_aliyun_env_names():
    settings = Settings(
        aliyun_access_key_id="id",
        aliyun_access_key_secret="secret",
        aliyun_oss_endpoint="oss-cn-shenzhen.aliyuncs.com",
        aliyun_oss_bucket="aceflow-connection",
    )

    assert oss_is_configured(settings) is True
    assert build_public_url(settings, "connection/test.wav") == "https://aceflow-connection.oss-cn-shenzhen.aliyuncs.com/connection/test.wav"


def test_oss_object_keys_use_readable_business_directories():
    import_key = build_import_object_key(16, "数学周测卷.pdf")
    submission_key = build_submission_object_key(31, "homework", "answer.jpg")

    assert "/imports/" in import_key
    assert "/batch-16/" in import_key
    assert import_key.endswith(".pdf")
    assert "/submissions/" in submission_key
    assert "/submission-31/homework/" in submission_key
    assert submission_key.endswith(".jpg")


def test_private_oss_url_is_converted_to_signed_download_url(monkeypatch):
    settings = Settings(
        aliyun_access_key_id="id",
        aliyun_access_key_secret="secret",
        aliyun_oss_endpoint="oss-cn-shenzhen.aliyuncs.com",
        aliyun_oss_bucket="aceflow-connection",
        aliyun_oss_signed_url_expires_seconds=600,
    )
    url = "https://aceflow-connection.oss-cn-shenzhen.aliyuncs.com/connection/imports/2026-07-10/batch-99/paper.pdf"
    captured = {}

    class FakeAuth:
        def __init__(self, key_id, key_secret):
            captured["auth"] = (key_id, key_secret)

    class FakeBucket:
        def __init__(self, auth, endpoint, bucket):
            captured["bucket"] = (endpoint, bucket)

        def sign_url(self, method, key, expires, slash_safe=True):
            captured["sign"] = (method, key, expires, slash_safe)
            return f"https://signed.example.com/{key}?signature=test"

    import backend.app.services.oss_service as oss_service

    monkeypatch.setattr(oss_service.oss2, "Auth", FakeAuth)
    monkeypatch.setattr(oss_service.oss2, "Bucket", FakeBucket)

    signed = signed_download_url(url, settings)

    assert signed == "https://signed.example.com/connection/imports/2026-07-10/batch-99/paper.pdf?signature=test"
    assert captured["auth"] == ("id", "secret")
    assert captured["bucket"] == ("https://oss-cn-shenzhen.aliyuncs.com", "aceflow-connection")
    assert captured["sign"] == ("GET", "connection/imports/2026-07-10/batch-99/paper.pdf", 600, True)


def test_extract_text_from_plain_document(tmp_path):
    file_path = tmp_path / "homework.txt"
    file_path.write_text("口算100道，背20个英语单词", encoding="utf-8")

    assert extract_text_from_document(str(file_path), "file") == "口算100道，背20个英语单词"


def test_prepare_local_audio_uploads_to_oss(monkeypatch, tmp_path):
    audio_path = tmp_path / "reading.mp3"
    audio_path.write_bytes(b"fake-audio")

    def fake_upload(file_path, object_key=None):
        return f"https://oss.example.com/{object_key}"

    monkeypatch.setattr("backend.app.services.media_processing_service.upload_file_to_oss", fake_upload)

    assert prepare_audio_url(str(audio_path), "audio").endswith("/asr/reading.mp3")


def test_ai_correction_prompt_includes_assignment_content_and_optional_answer(monkeypatch, tmp_path):
    from backend.app.core.config import settings

    monkeypatch.setattr(settings, "dashscope_api_key", "test-key")
    monkeypatch.setattr(settings, "vision_provider", "qwen")
    monkeypatch.setattr(settings, "vision_base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setattr(settings, "vision_model", "qwen-vl-plus")

    image_path = tmp_path / "page.jpg"
    image_path.write_bytes(
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00\x01\x00\x01\x00\x00\xff\xd9"
    )

    captured_payload = {}

    class DummyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "{\"completion_score\": 90, \"questions\": []}"}}]}

    def fake_post(url, headers, json, timeout):
        captured_payload["json"] = json
        return DummyResponse()

    monkeypatch.setattr("backend.app.services.correction_ai_service.httpx.post", fake_post)

    with SessionLocal() as db:
        user = User(openid=f"ai-prompt-{uuid4().hex}", role="parent", nickname="家长")
        db.add(user)
        db.flush()
        family = Family(name="AI测试家庭", created_by=user.id)
        db.add(family)
        db.flush()
        student = Student(family_id=family.id, name="测试学生", grade="四年级")
        db.add(student)
        db.flush()
        plan = AssignmentBatch(student_id=student.id, title="AI测试计划")
        db.add(plan)
        db.flush()
        item = AssignmentItem(
            assignment_batch_id=plan.id,
            subject="数学",
            title="数学口算",
            source_text="数学口算20道，第1页到第2页",
        )
        db.add(item)
        db.flush()
        task = DailyTask(
            student_id=student.id,
            assignment_batch_id=plan.id,
            assignment_item_id=item.id,
            task_date=date.today(),
            subject="数学",
            title="数学：完成20道",
        )
        db.add(task)
        db.flush()
        submission = Submission(
            daily_task_id=task.id,
            student_id=student.id,
            submission_type="photo",
            answer_text="1.A 2.B 3.C",
        )
        db.add(submission)
        db.flush()
        db.add(SubmissionMedia(submission_id=submission.id, media_type="image", file_url=str(image_path)))
        db.commit()
        submission_id = submission.id

    with SessionLocal() as db:
        submission = db.get(Submission, submission_id)
        build_ai_correction_payload(db, submission)

    prompt_text = captured_payload["json"]["messages"][0]["content"][0]["text"]
    assert "数学口算20道，第1页到第2页" in prompt_text
    assert "1.A 2.B 3.C" in prompt_text
