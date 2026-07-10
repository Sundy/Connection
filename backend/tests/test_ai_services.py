from datetime import date
from uuid import uuid4

from backend.app.core.config import Settings
from backend.app.core.database import SessionLocal, init_db
from backend.app.models import AssignmentBatch, AssignmentItem, DailyTask, Family, Student, Submission, SubmissionMedia, User
from backend.app.services.ai_config import api_key_for, service_is_configured
from backend.app.services.asr_service import transcribe_audio_url
from backend.app.services.correction_ai_service import build_ai_correction_payload
from backend.app.services.document_extract_service import extract_text_from_document
from backend.app.services.media_processing_service import prepare_audio_url
from backend.app.services.oss_service import build_public_url, oss_is_configured


init_db()


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
            answer_text="1.A 2.B 3.C",
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
        submission = Submission(daily_task_id=task.id, student_id=student.id, submission_type="photo")
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
