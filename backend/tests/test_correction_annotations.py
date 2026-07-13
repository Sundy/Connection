import base64
import json
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import inspect

from backend.app.core.config import Settings
from backend.app.core.database import SessionLocal, engine, init_db
from backend.app.models import AssignmentBatch, AssignmentItem, CorrectionResult, DailyTask, Family, QuestionResult, Student, Submission, SubmissionMedia, User
from backend.app.services.correction_annotation_service import group_questions, normalize_annotations
from backend.app.services.correction_service import _create_result_from_payload, create_correction


@pytest.fixture
def correction_submission():
    init_db()
    with SessionLocal() as db:
        user = User(openid=f"annotation-{uuid4().hex}", role="parent", nickname="家长")
        db.add(user)
        db.flush()
        family = Family(name="卷面批改测试家庭", created_by=user.id)
        db.add(family)
        db.flush()
        student = Student(family_id=family.id, name="测试学生", grade="三年级")
        db.add(student)
        db.flush()
        batch = AssignmentBatch(student_id=student.id, title="卷面批改测试")
        db.add(batch)
        db.flush()
        item = AssignmentItem(assignment_batch_id=batch.id, subject="语文", title="练习册")
        db.add(item)
        db.flush()
        task = DailyTask(
            student_id=student.id,
            assignment_batch_id=batch.id,
            assignment_item_id=item.id,
            task_date=date.today(),
            subject="语文",
            title="练习册",
            status="correcting",
        )
        db.add(task)
        db.flush()
        submission = Submission(
            daily_task_id=task.id,
            student_id=student.id,
            submission_type="photo",
            status="processing",
        )
        db.add(submission)
        db.commit()
        return submission.id


def test_annotation_schema_and_default_threshold_exist():
    init_db()
    inspector = inspect(engine)
    submission_columns = {column["name"] for column in inspector.get_columns("submissions")}
    question_columns = {column["name"] for column in inspector.get_columns("question_results")}

    assert {"processing_stage", "processing_message"} <= submission_columns
    assert {"source_media_id", "annotations_json"} <= question_columns
    assert Settings().annotation_confidence_threshold == 0.65


def test_result_persistence_maps_page_index_to_media_id(correction_submission):
    submission_id = correction_submission
    with SessionLocal() as db:
        submission = db.get(Submission, submission_id)
        first = SubmissionMedia(submission_id=submission.id, media_type="image", purpose="homework", file_url="page-1.jpg", sort_order=2)
        second = SubmissionMedia(submission_id=submission.id, media_type="image", purpose="homework", file_url="page-2.jpg", sort_order=5)
        db.add_all([first, second])
        db.commit()
        _create_result_from_payload(db, submission, {
            "completion_score": 80,
            "accuracy_score": 75,
            "confidence_score": 0.9,
            "questions": [{
                "source_image_index": 2,
                "question_no": "6",
                "is_correct": False,
                "annotations": [{"kind": "error_circle", "x": 0.2, "y": 0.3, "width": 0.2, "height": 0.1, "text": None, "confidence": 0.9}],
            }],
        }, {1: first.id, 2: second.id})
        saved = db.query(QuestionResult).join(CorrectionResult).filter(
            CorrectionResult.submission_id == submission.id,
            QuestionResult.question_no == "6",
        ).one()
        assert saved.source_media_id == second.id
        assert json.loads(saved.annotations_json)[0]["kind"] == "error_circle"
        assert submission.processing_stage == "corrected"


def test_review_question_persistence_suppresses_conclusion_annotations(correction_submission):
    submission_id = correction_submission
    with SessionLocal() as db:
        submission = db.get(Submission, submission_id)
        page = SubmissionMedia(submission_id=submission.id, media_type="image", purpose="homework", file_url="page-1.jpg", sort_order=1)
        db.add(page)
        db.flush()

        _create_result_from_payload(db, submission, {
            "completion_score": 70,
            "accuracy_score": None,
            "confidence_score": 0.6,
            "questions": [{
                "source_image_index": 1,
                "question_no": "8",
                "is_correct": None,
                "explanation": "字迹不清，需要复核",
                "annotations": [
                    {"kind": "correct_tick", "x": 0.1, "y": 0.1, "width": 0.1, "height": 0.1, "confidence": 0.9},
                    {"kind": "error_circle", "x": 0.2, "y": 0.2, "width": 0.1, "height": 0.1, "confidence": 0.9},
                    {"kind": "error_cross", "x": 0.3, "y": 0.3, "width": 0.1, "height": 0.1, "confidence": 0.9},
                    {"kind": "comment", "x": 0.4, "y": 0.4, "width": 0.2, "height": 0.1, "text": "请老师复核", "confidence": 0.9},
                ],
            }],
        }, {1: page.id})

        saved = db.query(QuestionResult).join(CorrectionResult).filter(
            CorrectionResult.submission_id == submission.id,
            QuestionResult.question_no == "8",
        ).one()

    assert saved.is_correct is None
    assert saved.explanation == "字迹不清，需要复核"
    assert [item["kind"] for item in json.loads(saved.annotations_json)] == ["comment"]


def test_create_correction_marks_missing_page_for_review_and_uses_ai_photo_order(
    monkeypatch,
    tmp_path,
    correction_submission,
):
    from backend.app.core.config import settings

    monkeypatch.setattr(settings, "dashscope_api_key", "test-key")
    monkeypatch.setattr(settings, "vision_provider", "qwen")
    monkeypatch.setattr(settings, "vision_base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setattr(settings, "vision_model", "qwen-vl-plus")
    monkeypatch.setattr(settings, "vision_max_images", 3)

    page_one = tmp_path / "page-one.jpg"
    page_two = tmp_path / "page-two.jpg"
    answer_image = tmp_path / "answer.jpg"
    attachment = tmp_path / "worksheet.pdf"
    page_one.write_bytes(b"page-one")
    page_two.write_bytes(b"page-two")
    answer_image.write_bytes(b"answer")
    attachment.write_bytes(b"%PDF-1.4")

    captured_payload = {}

    class DummyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "completion_score": 90,
                            "accuracy_score": 80,
                            "confidence_score": 0.9,
                            "questions": [{
                                "source_image_index": 1,
                                "question_no": "9",
                                "is_correct": False,
                                "annotations": [{
                                    "kind": "error_circle",
                                    "x": 0.2,
                                    "y": 0.3,
                                    "width": 0.2,
                                    "height": 0.1,
                                    "confidence": 0.9,
                                }],
                            }],
                        }, ensure_ascii=False),
                    },
                }],
            }

    def fake_post(url, headers, json, timeout):
        captured_payload["json"] = json
        return DummyResponse()

    monkeypatch.setattr("backend.app.services.correction_ai_service.httpx.post", fake_post)

    with SessionLocal() as db:
        submission = db.get(Submission, correction_submission)
        db.add_all([
            SubmissionMedia(submission_id=submission.id, media_type="pdf", purpose="homework", file_url=str(attachment), sort_order=0),
            SubmissionMedia(submission_id=submission.id, media_type="image", purpose="answer", file_url=str(answer_image), sort_order=0),
            SubmissionMedia(submission_id=submission.id, media_type="image", purpose="homework", file_url=str(page_one), sort_order=0),
            SubmissionMedia(submission_id=submission.id, media_type="image", purpose="homework", file_url=str(page_two), sort_order=0),
        ])
        db.commit()
        page_one_media_id = db.query(SubmissionMedia).filter(
            SubmissionMedia.submission_id == submission.id,
            SubmissionMedia.file_url == str(page_one),
        ).one().id

        result = create_correction(db, submission)

        content = captured_payload["json"]["messages"][0]["content"]
        labels = [
            item["text"]
            for item in content
            if item.get("type") == "text" and item.get("text", "").startswith("学生作业照片")
        ]
        image_urls = [
            item["image_url"]["url"]
            for item in content
            if item.get("type") == "image_url"
        ]
        saved = db.query(QuestionResult).join(CorrectionResult).filter(
            CorrectionResult.submission_id == submission.id,
            QuestionResult.question_no == "9",
        ).one()

    assert labels == ["学生作业照片 1", "学生作业照片 2"]
    assert image_urls == [
        f"data:image/jpeg;base64,{base64.b64encode(page_one.read_bytes()).decode('ascii')}",
        f"data:image/jpeg;base64,{base64.b64encode(page_two.read_bytes()).decode('ascii')}",
    ]
    assert saved.source_media_id == page_one_media_id
    assert result.needs_review is True
    assert "第 2 页" in result.review_reason
    assert submission.status == "needs_review"
    assert submission.processing_stage == "needs_review"


def test_annotations_are_clamped_and_low_confidence_items_are_removed():
    normalized = normalize_annotations([
        {"kind": "error_circle", "x": -0.1, "y": 0.4, "width": 0.3, "height": 0.2, "confidence": 0.92},
        {"kind": "error_cross", "x": 0.2, "y": 0.3, "width": 0, "height": 0.1, "confidence": 0.9},
        {"kind": "comment", "x": 0.7, "y": 0.8, "width": 0.5, "height": 0.2, "text": "检查单位", "confidence": 0.4},
    ], threshold=0.65)

    assert normalized == [{
        "kind": "error_circle",
        "x": 0.0,
        "y": 0.4,
        "width": 0.3,
        "height": 0.2,
        "text": None,
        "confidence": 0.92,
    }]


def test_subquestions_are_grouped_by_page_and_main_question_number():
    grouped = group_questions([
        {"source_image_index": 1, "question_no": "3(1)", "is_correct": True, "explanation": "第一小问正确", "annotations": []},
        {"source_image_index": 1, "question_no": "第3题（2）", "is_correct": False, "explanation": "第二小问用词错误", "annotations": [{"kind": "error_circle", "x": 0.2, "y": 0.3, "width": 0.2, "height": 0.1, "confidence": 0.9}]},
        {"source_image_index": 2, "question_no": "3", "is_correct": True, "explanation": "另一页第三题", "annotations": []},
    ], threshold=0.65)

    assert [(item["source_image_index"], item["question_no"]) for item in grouped] == [(1, "3"), (2, "3")]
    assert grouped[0]["is_correct"] is False
    assert grouped[0]["explanation"] == "第一小问正确；第二小问用词错误"
    assert len(grouped[0]["annotations"]) == 1


def test_review_questions_keep_only_neutral_annotations():
    grouped = group_questions([
        {
            "source_image_index": 1,
            "question_no": "10",
            "is_correct": None,
            "explanation": "无法判断答案是否正确",
            "annotations": [
                {"kind": "correct_tick", "x": 0.1, "y": 0.1, "width": 0.1, "height": 0.1, "confidence": 0.9},
                {"kind": "error_cross", "x": 0.2, "y": 0.2, "width": 0.1, "height": 0.1, "confidence": 0.9},
                {"kind": "comment", "x": 0.3, "y": 0.3, "width": 0.2, "height": 0.1, "text": "复核这里", "confidence": 0.9},
            ],
        },
    ], threshold=0.65)

    assert grouped[0]["is_correct"] is None
    assert grouped[0]["explanation"] == "无法判断答案是否正确"
    assert [item["kind"] for item in grouped[0]["annotations"]] == ["comment"]


@pytest.mark.parametrize("statuses", [
    [False, None],
    [None, False],
])
def test_grouped_question_status_uses_false_none_true_precedence(statuses):
    grouped = group_questions([
        {"source_image_index": 1, "question_no": "4(1)", "is_correct": status, "annotations": []}
        for status in statuses
    ], threshold=0.65)

    assert grouped[0]["is_correct"] is False


def test_non_finite_numeric_inputs_fall_back_without_aborting():
    grouped = group_questions([
        {"source_image_index": float("nan"), "question_no": "5", "is_correct": True, "annotations": []},
        {"source_image_index": float("inf"), "question_no": "6", "is_correct": True, "annotations": []},
    ], threshold=0.65)
    normalized = normalize_annotations([
        {"kind": "error_circle", "x": float("nan"), "y": float("inf"), "width": 0.2, "height": 0.1, "confidence": 0.9},
        {"kind": "comment", "x": 0.1, "y": 0.1, "width": 0.2, "height": 0.1, "confidence": float("inf")},
    ], threshold=0.65)

    assert [item["source_image_index"] for item in grouped] == [1, 1]
    assert normalized == [{
        "kind": "error_circle",
        "x": 0.0,
        "y": 0.0,
        "width": 0.2,
        "height": 0.1,
        "text": None,
        "confidence": 0.9,
    }]
