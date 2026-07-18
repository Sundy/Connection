import httpx

from backend.app.core.config import Settings
from backend.app.models import AssignmentBatch, ImportFile
from backend.app.services.import_content_service import (
    analyze_import_content,
    normalize_content_title,
)
from backend.app.services.llm_service import analyze_import_file_with_llm


def test_import_intelligence_fields_and_thresholds_exist():
    file = ImportFile(
        import_batch_id=1,
        file_name="tmp_123.png",
        file_type="image",
        file_url="/tmp/tmp_123.png",
        document_role="homework",
        recognized_title="数学四年级下册第3单元练习",
        recognition_status="success",
        match_status="not_required",
    )
    plan = AssignmentBatch(student_id=1, title="新增作业", target_assignment_batch_id=8)

    assert file.document_role == "homework"
    assert file.recognized_title == "数学四年级下册第3单元练习"
    assert plan.target_assignment_batch_id == 8
    assert Settings().import_title_confidence_threshold == 0.75
    assert Settings().answer_match_confidence_threshold == 0.80


def test_content_title_removes_temporary_name_noise():
    title = normalize_content_title(
        "tmp_2fd24e2f2564d61a34e0b0c0f2446282.pdf",
        {
            "subject": "数学",
            "grade_hint": "四年级下册",
            "chapter": "第3单元",
            "exercise_type": "练习",
        },
    )

    assert title == "数学四年级下册第3单元练习"
    assert "tmp" not in title.lower()


def test_local_content_analysis_builds_chinese_title_without_file_name(monkeypatch):
    monkeypatch.setattr(
        "backend.app.services.import_content_service.analyze_import_file_with_llm",
        lambda text, document_role: {},
    )

    result = analyze_import_content(
        "四年级下册数学 第三单元 小数加减法练习 第1至20题",
        "homework",
    )

    assert result["recognized_title"] == "数学四年级下册第3单元小数加减法练习"
    assert result["signature"]["subject"] == "数学"
    assert result["signature"]["question_start"] == 1
    assert result["signature"]["question_end"] == 20


def test_unreadable_content_has_no_title():
    result = analyze_import_content("___", "homework")

    assert result["recognized_title"] is None
    assert result["recognition_status"] == "failed"


def test_import_content_llm_uses_json_object_without_file_name(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"subject":"数学","recommended_title":"数学四年级小数练习"}'
                        }
                    }
                ]
            }

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr("backend.app.services.llm_service.llm_is_configured", lambda: True)
    monkeypatch.setattr("backend.app.services.llm_service.httpx.post", fake_post)

    result = analyze_import_file_with_llm("四年级数学小数练习", "homework")

    assert captured["json"]["response_format"] == {"type": "json_object"}
    assert "file_name" not in str(captured["json"])
    assert "tmp_" not in str(captured["json"])
    assert result == {"subject": "数学", "recommended_title": "数学四年级小数练习"}


def test_import_content_normalizes_llm_signature(monkeypatch):
    monkeypatch.setattr(
        "backend.app.services.import_content_service.analyze_import_file_with_llm",
        lambda text, document_role: {
            "subject": "数学",
            "grade_hint": "四年级下册",
            "chapter": "第3单元",
            "exercise_type": "小数加减法练习",
            "question_start": "1",
            "question_end": "20",
            "question_count": "20",
            "keywords": "小数，加减法，练习",
            "is_answer": "false",
            "recommended_title": "数学四年级下册小数加减法练习",
            "confidence_score": "0.91",
            "content_summary": "小数加减法练习，共二十题。",
        },
        raising=False,
    )

    result = analyze_import_content("正文内容", "homework")

    assert result["recognized_title"] == "数学四年级下册小数加减法练习"
    assert result["recognition_status"] == "success"
    assert result["signature"]["question_start"] == 1
    assert result["signature"]["question_end"] == 20
    assert result["signature"]["question_count"] == 20
    assert result["signature"]["keywords"] == ["小数", "加减法", "练习"]
    assert result["signature"]["is_answer"] is False
    assert result["signature"]["confidence_score"] == 0.91


def test_import_content_falls_back_when_llm_http_fails(monkeypatch):
    def fail_llm(text, document_role):
        raise httpx.ReadTimeout("LLM timed out")

    monkeypatch.setattr(
        "backend.app.services.import_content_service.analyze_import_file_with_llm",
        fail_llm,
    )

    result = analyze_import_content(
        "四年级下册数学 第三单元 小数加减法练习 第1至20题",
        "homework",
    )

    assert result["recognized_title"] == "数学四年级下册第3单元小数加减法练习"
    assert result["recognition_status"] == "success"


def test_import_content_prefers_llm_title_over_local_title(monkeypatch):
    monkeypatch.setattr(
        "backend.app.services.import_content_service.analyze_import_file_with_llm",
        lambda text, document_role: {
            "recommended_title": "数学四年级小数加减法专项练习",
            "confidence_score": 0.96,
        },
    )

    result = analyze_import_content(
        "四年级下册数学 第三单元 小数加减法练习 第1至20题",
        "homework",
    )

    assert result["recognized_title"] == "数学四年级小数加减法专项练习"


def test_answer_content_succeeds_without_homework_title(monkeypatch):
    monkeypatch.setattr(
        "backend.app.services.import_content_service.analyze_import_file_with_llm",
        lambda text, document_role: {},
    )

    result = analyze_import_content(
        "四年级下册数学 第三单元 参考答案 第1至20题",
        "answer",
    )

    assert result["recognized_title"] is None
    assert result["recognition_status"] == "success"
    assert result["signature"]["is_answer"] is True
    assert result["signature"]["question_count"] == 20


def test_answer_content_without_answer_marker_fails(monkeypatch):
    monkeypatch.setattr(
        "backend.app.services.import_content_service.analyze_import_file_with_llm",
        lambda text, document_role: {},
    )

    result = analyze_import_content(
        "四年级下册数学 第三单元 第1至20题",
        "answer",
    )

    assert result["recognized_title"] is None
    assert result["recognition_status"] == "failed"
    assert result["signature"]["is_answer"] is False


def test_empty_llm_fields_fall_back_to_local_signature(monkeypatch):
    monkeypatch.setattr(
        "backend.app.services.import_content_service.analyze_import_file_with_llm",
        lambda text, document_role: {
            "subject": "",
            "keywords": [],
            "recommended_title": "",
        },
    )

    result = analyze_import_content(
        "四年级下册数学 第三单元 小数加减法练习 第1至20题",
        "homework",
    )

    assert result["recognized_title"] == "数学四年级下册第3单元小数加减法练习"
    assert "数学" in result["signature"]["keywords"]
