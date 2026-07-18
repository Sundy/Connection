import httpx
import pytest

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


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        ("550e8400-e29b-41d4-a716-446655440000.pdf", "数学四年级下册第3单元练习"),
        ("数学练习_550e8400-e29b-41d4-a716-446655440000", "数学四年级下册第3单元练习"),
        ("2fd24e2f2564d61a34e0b0c0f2446282", "数学四年级下册第3单元练习"),
        ("数学练习_2fd24e2f2564d61a34e0b0c0f2446282", "数学四年级下册第3单元练习"),
        ("1712345678", "数学四年级下册第3单元练习"),
        ("1712345678123", "数学四年级下册第3单元练习"),
        ("20240719123045", "数学四年级下册第3单元练习"),
        ("数学练习_1712345678", "数学四年级下册第3单元练习"),
        ("数学练习_1712345678123", "数学四年级下册第3单元练习"),
        ("数学练习_20240719123045", "数学四年级下册第3单元练习"),
        ("数学练习_2024-07-19", "数学四年级下册第3单元练习"),
        ("数学练习_2024_07_19", "数学四年级下册第3单元练习"),
        ("数学练习_20240719", "数学四年级下册第3单元练习"),
        ("数学练习_12:30", "数学四年级下册第3单元练习"),
        ("homework-final.pages", "数学四年级下册第3单元练习"),
        ("数学小数练习.material", "数学小数练习"),
        ("数学小数练习.archive.backup", "数学小数练习"),
    ],
)
def test_content_title_rejects_identifiers_dates_and_extensions(candidate, expected):
    signature = {
        "subject": "数学",
        "grade_hint": "四年级下册",
        "chapter": "第3单元",
        "exercise_type": "练习",
    }

    assert normalize_content_title(candidate, signature) == expected


def test_content_title_rejects_unsafe_signature_fallback():
    title = normalize_content_title(
        "550e8400-e29b-41d4-a716-446655440000.pdf",
        {"subject": "数学", "exercise_type": "练习_20240719123045.pdf"},
    )

    assert title is None


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


def test_import_content_llm_returns_empty_when_unconfigured(monkeypatch):
    def unexpected_post(*args, **kwargs):
        pytest.fail("unconfigured LLM must not make an HTTP request")

    monkeypatch.setattr("backend.app.services.llm_service.llm_is_configured", lambda: False)
    monkeypatch.setattr("backend.app.services.llm_service.httpx.post", unexpected_post)

    result = analyze_import_file_with_llm("四年级数学小数练习", "homework")

    assert result == {}


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


@pytest.mark.parametrize("failure", ["empty_choices", "bad_json", "http_error"])
def test_import_content_falls_back_for_llm_boundary_failures(monkeypatch, failure):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            if failure == "empty_choices":
                return {"choices": []}
            return {"choices": [{"message": {"content": "not-json"}}]}

    def fake_post(url, **kwargs):
        if failure == "http_error":
            raise httpx.ReadTimeout("LLM timed out")
        return FakeResponse()

    monkeypatch.setattr("backend.app.services.llm_service.llm_is_configured", lambda: True)
    monkeypatch.setattr("backend.app.services.llm_service.httpx.post", fake_post)

    result = analyze_import_content(
        "四年级下册数学 第三单元 小数加减法练习 第1至20题",
        "homework",
    )

    assert result["recognized_title"] == "数学四年级下册第3单元小数加减法练习"
    assert result["recognition_status"] == "success"


@pytest.mark.parametrize("error_type", [KeyError, TypeError, ValueError])
def test_import_content_propagates_programming_errors(monkeypatch, error_type):
    def fail_with_programming_error(text, document_role):
        raise error_type("programming bug")

    monkeypatch.setattr(
        "backend.app.services.import_content_service.analyze_import_file_with_llm",
        fail_with_programming_error,
    )

    with pytest.raises(error_type, match="programming bug"):
        analyze_import_content("四年级数学练习 第1至10题", "homework")


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


def test_answer_content_with_only_subject_fails(monkeypatch):
    monkeypatch.setattr(
        "backend.app.services.import_content_service.analyze_import_file_with_llm",
        lambda text, document_role: {},
    )

    result = analyze_import_content("数学参考答案", "answer")

    assert result["recognized_title"] is None
    assert result["recognition_status"] == "failed"
    assert result["signature"]["subject"] == "数学"
    assert result["signature"]["is_answer"] is True


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
