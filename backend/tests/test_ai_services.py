from datetime import date
import json
from uuid import uuid4

from backend.app.core.config import Settings
from backend.app.core.database import SessionLocal, init_db
from backend.app.models import AssignmentBatch, AssignmentItem, CorrectionResult, DailyTask, Family, Student, Submission, SubmissionMedia, User
from backend.app.services.ai_config import api_key_for, service_is_configured
from backend.app.services.asr_service import transcribe_audio_url
from backend.app.services.correction_ai_service import build_ai_correction_payload, classify_video_strategy, normalize_correction_payload, parse_correction_content
from backend.app.services.correction_service import MissingHomeworkMediaError
from backend.app.services.document_extract_service import extract_text_from_document
from backend.app.services.media_processing_service import extract_video_frames, prepare_audio_url
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
        assert submission.processing_stage == "failed"
        assert submission.processing_message == "批改服务暂时不可用，请稍后重试。"
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


def test_correction_worker_reports_missing_homework_media(monkeypatch):
    submission_id, task_id = create_correction_submission()

    def fail_correction(db, submission):
        raise MissingHomeworkMediaError("no media")

    monkeypatch.setattr("backend.app.worker.tasks.correct_homework.create_correction", fail_correction)
    response = run_homework_correction.run(submission_id)

    assert response == {"ok": False, "error": "missing_homework_media"}
    with SessionLocal() as db:
        submission = db.get(Submission, submission_id)
        assert submission.error_code == "missing_homework_media"
        assert submission.error_message == "未找到作业图片或视频，请重新上传后提交。"


def test_normalize_correction_clamps_scores_and_marks_low_confidence_for_review():
    payload = normalize_correction_payload({
        "completion_score": "120",
        "accuracy_score": -3,
        "confidence_score": 0.42,
        "summary": "识别完成",
        "needs_review": False,
        "questions": [{"question_no": 1, "is_correct": None, "confidence_score": 42}],
    })

    assert payload["completion_score"] == 100
    assert payload["accuracy_score"] == 0
    assert payload["confidence_score"] == 0.42
    assert payload["needs_review"] is True
    assert payload["review_reason"]
    assert payload["questions"][0]["confidence_score"] == 0.42
    assert payload["questions"][0]["recognized_answer"] is None


def test_normalize_correction_keeps_subquestion_leaves_and_annotations():
    payload = normalize_correction_payload({
        "completion_score": 80,
        "accuracy_score": 75,
        "confidence_score": 0.9,
        "questions": [
            {"source_image_index": 1, "question_no": "2(1)", "is_correct": True, "confidence_score": 0.9},
            {"source_image_index": 1, "question_no": "2(2)", "is_correct": False, "confidence_score": 0.9, "annotations": [{"kind": "error_circle", "x": 0.2, "y": 0.3, "width": 0.2, "height": 0.1, "confidence": 0.9}]},
        ],
    })

    assert len(payload["questions"]) == 2
    assert payload["questions"][0]["question_no"] == "2"
    assert payload["questions"][0]["subquestion_no"] == "1"
    assert payload["questions"][1]["question_no"] == "2"
    assert payload["questions"][1]["subquestion_no"] == "2"
    assert payload["questions"][1]["is_correct"] is False
    assert payload["questions"][1]["annotations"][0]["kind"] == "error_circle"


def test_normalize_correction_marks_missing_global_question_for_review():
    payload = normalize_correction_payload({
        "completion_score": 80,
        "accuracy_score": 75,
        "confidence_score": 0.9,
        "questions": [
            {
                "section_no": "一",
                "question_no": str(number),
                "is_correct": True,
            }
            for number in range(1, 15)
            if number != 2
        ],
    })

    assert payload["needs_review"] is True
    assert payload["review_reason"] == "未生成第 2 题批改结果"


def test_parse_correction_content_accepts_markdown_json_fence():
    parsed = parse_correction_content('```json\n{"completion_score": 90, "confidence_score": 0.9, "summary": "完成", "questions": []}\n```')
    assert parsed["completion_score"] == 90


def test_video_strategy_uses_task_type_and_title():
    assert classify_video_strategy(DailyTask(task_type="recitation", title="古诗背诵")) == "speech"
    assert classify_video_strategy(DailyTask(task_type="written", title="展示计算过程")) == "visual"
    assert classify_video_strategy(DailyTask(task_type="mixed", title="综合实践")) == "mixed"


def test_extract_video_frames_uses_ffmpeg_and_respects_limit(tmp_path, monkeypatch):
    video = tmp_path / "homework.mp4"
    video.write_bytes(b"video")
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        output_pattern = command[-1]
        output_dir = __import__("pathlib").Path(output_pattern).parent
        for index in range(1, 6):
            (output_dir / f"frame-{index:03d}.jpg").write_bytes(b"image")

    monkeypatch.setattr("backend.app.services.media_processing_service.subprocess.run", fake_run)
    frames = extract_video_frames(str(video), max_frames=3)

    assert len(frames) == 3
    assert "-frames:v" in captured["command"]
    assert captured["command"][captured["command"].index("-frames:v") + 1] == "3"


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
        submission = Submission(
            daily_task_id=task.id,
            student_id=student.id,
            submission_type="photo",
        )
        db.add(submission)
        db.flush()
        db.add(SubmissionMedia(submission_id=submission.id, media_type="image", file_url=str(image_path)))
        db.commit()
        submission_id = submission.id

    with SessionLocal() as db:
        submission = db.get(Submission, submission_id)
        build_ai_correction_payload(db, submission)

    messages = captured_payload["json"]["messages"]
    assert [message["role"] for message in messages] == ["system", "user"]
    prompt_text = messages[0]["content"]
    assert "每个叶子小题独立返回一条 questions 记录" in prompt_text
    assert "不要把 (1)(2)(3) 合并" in prompt_text
    assert "source_image_index" in prompt_text
    assert "0 到 1" in prompt_text

    content = messages[1]["content"]
    untrusted_data = json.loads(content[0]["text"])["untrusted_data"]
    assert untrusted_data["assignment_text"] == "数学口算20道，第1页到第2页"
    assert untrusted_data["reference_answer"] == "1.A 2.B 3.C"
    assert content[1]["text"] == "学生作业照片 1"
    assert content[2]["type"] == "image_url"
