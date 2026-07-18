import json
from types import SimpleNamespace

from backend.app.services.correction_annotation_service import (
    missing_global_question_nos,
    normalize_question_leaves,
    parse_question_identity,
)
from backend.app.services.correction_ai_service import normalize_correction_payload
from backend.app.services.correction_service import _question_result_from_payload
from backend.app.services.result_page_service import aggregate_question_results


def leaf(*, media, section, main, sub, status, annotations):
    return SimpleNamespace(
        source_media_id=media,
        section_no=section,
        question_no=main,
        subquestion_no=sub,
        question_type="written",
        recognized_answer="学生答案",
        expected_answer="参考答案",
        is_correct=status,
        score=None,
        explanation="批改说明",
        confidence_score=0.9,
        annotations_json=json.dumps(annotations, ensure_ascii=False),
    )


def test_combined_and_structured_question_identities_are_parsed():
    assert parse_question_identity({"question_no": "一、1"}) == ("一", "1", None)
    assert parse_question_identity({"question_no": "四、12(3)"}) == ("四", "12", "3")
    assert parse_question_identity({"question_no": "12（3）"}) == (None, "12", "3")
    assert parse_question_identity({"question_no": "第12题（3）"}) == (None, "12", "3")
    assert parse_question_identity({
        "section_no": "二",
        "question_no": "7(9)",
        "subquestion_no": "2",
    }) == ("二", "7", "2")


def test_real_twenty_two_leaf_identifiers_keep_fourteen_main_questions():
    identifiers = [
        "一、1", "一、2", "一、3",
        "二、4", "二、5", "二、6", "二、7",
        "三、8", "三、9", "三、10", "三、11",
        *[f"四、12({number})" for number in range(1, 8)],
        "四、13(1)", "四、13(2)",
        "四、14(1)", "四、14(2)",
    ]

    leaves = normalize_question_leaves(
        [
            {
                "source_image_index": 1,
                "question_no": identifier,
                "is_correct": True,
            }
            for identifier in identifiers
        ],
        threshold=0.65,
    )

    assert len(leaves) == 22
    assert len({
        (question["section_no"], question["question_no"])
        for question in leaves
    }) == 14
    assert leaves[11]["subquestion_no"] == "1"
    assert leaves[-1]["subquestion_no"] == "2"


def test_global_sequence_reports_missing_main_questions():
    questions = [
        {
            "section_no": "一",
            "question_no": str(number),
            "subquestion_no": None,
        }
        for number in [1, 2, 3, 5, 6]
    ]

    assert missing_global_question_nos(questions) == [4]


def test_section_number_reset_is_not_reported_as_a_global_gap():
    questions = [
        {
            "section_no": section,
            "question_no": str(number),
            "subquestion_no": None,
        }
        for section in ("一", "二")
        for number in (1, 2, 3)
    ]

    assert missing_global_question_nos(questions) == []


def test_normalize_correction_marks_missing_global_question_for_review():
    payload = normalize_correction_payload({
        "completion_score": 80,
        "accuracy_score": 75,
        "confidence_score": 0.9,
        "questions": [
            {"section_no": "一", "question_no": "1", "is_correct": True},
            {"section_no": "一", "question_no": "3", "is_correct": True},
        ],
    })

    assert payload["needs_review"] is True
    assert payload["review_reason"] == "未生成第 2 题批改结果"


def test_question_result_persistence_mapping_keeps_three_level_identity():
    saved = _question_result_from_payload(
        correction_result_id=99,
        question={
            "source_image_index": 2,
            "section_no": "四",
            "question_no": "12",
            "subquestion_no": "3",
            "is_correct": False,
            "annotations": [{
                "kind": "error_circle",
                "x": 0.2,
                "y": 0.3,
                "width": 0.2,
                "height": 0.1,
                "confidence": 0.9,
            }],
        },
        media_ids_by_index={1: 100, 2: 200},
    )

    assert saved.correction_result_id == 99
    assert saved.section_no == "四"
    assert saved.question_no == "12"
    assert saved.subquestion_no == "3"
    assert saved.source_media_id == 200
    assert '"kind": "error_circle"' in saved.annotations_json


def test_result_aggregation_keeps_subquestions_and_flattens_annotations():
    questions = [
        leaf(
            media=10,
            section="四",
            main="12",
            sub="1",
            status=True,
            annotations=[{"kind": "correct_tick", "x": 0.1}],
        ),
        leaf(
            media=10,
            section="四",
            main="12",
            sub="2",
            status=False,
            annotations=[{"kind": "error_circle", "x": 0.6}],
        ),
    ]

    mains = aggregate_question_results(questions)

    assert len(mains) == 1
    assert mains[0]["section_no"] == "四"
    assert mains[0]["question_no"] == "12"
    assert mains[0]["is_correct"] is False
    assert [
        question["subquestion_no"]
        for question in mains[0]["subquestions"]
    ] == ["1", "2"]
    assert [
        annotation["kind"]
        for annotation in mains[0]["annotations"]
    ] == ["correct_tick", "error_circle"]


def test_result_aggregation_never_moves_annotations_between_pages():
    mains = aggregate_question_results([
        leaf(
            media=10,
            section="一",
            main="1",
            sub=None,
            status=True,
            annotations=[{"kind": "correct_tick"}],
        ),
        leaf(
            media=20,
            section="一",
            main="1",
            sub=None,
            status=False,
            annotations=[{"kind": "error_circle"}],
        ),
    ])

    assert len(mains) == 2
    assert mains[0]["source_media_id"] == 10
    assert mains[0]["annotations"] == [{"kind": "correct_tick"}]
    assert mains[1]["source_media_id"] == 20
    assert mains[1]["annotations"] == [{"kind": "error_circle"}]


def test_historical_single_level_result_has_empty_subquestions():
    main = aggregate_question_results([
        leaf(
            media=10,
            section=None,
            main="6",
            sub=None,
            status=True,
            annotations=[],
        ),
    ])[0]

    assert main["question_no"] == "6"
    assert main["recognized_answer"] == "学生答案"
    assert main["subquestions"] == []


def test_main_question_status_uses_false_none_true_precedence():
    mains = aggregate_question_results([
        leaf(
            media=10,
            section="四",
            main="12",
            sub="1",
            status=True,
            annotations=[],
        ),
        leaf(
            media=10,
            section="四",
            main="12",
            sub="2",
            status=None,
            annotations=[],
        ),
        leaf(
            media=10,
            section="四",
            main="12",
            sub="3",
            status=False,
            annotations=[],
        ),
    ])

    assert mains[0]["is_correct"] is False
