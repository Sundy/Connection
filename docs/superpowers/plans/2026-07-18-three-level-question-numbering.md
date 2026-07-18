# Three-Level Question Numbering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve section, main-question, and subquestion identities as leaf grading records while returning 14 aggregated main questions with correctly scoped page annotations.

**Architecture:** Normalize every AI question into a leaf record identified by `(source_image_index, section_no, question_no, subquestion_no)`, persist those fields in MySQL, and aggregate only at the result API boundary. The page API groups leaves by `(source_media_id, section_no, question_no)`, nests subquestions, flattens their annotations for the existing image overlay, and derives one stable main-question status. The miniapp renders the aggregate while retaining leaf-level details.

**Tech Stack:** Python 3, FastAPI, SQLAlchemy 2, MySQL/PyMySQL, pytest, native WeChat mini-program WXML/WXSS/JavaScript, Node.js test runner.

## Global Constraints

- Work directly on `main`, as explicitly authorized by the user.
- MySQL is the only supported database; `APP_ENV=development` uses `DB_PROD_OUT`, and `APP_ENV=production` uses `DATABASE_URL_PRODUCTION`.
- Both configured URLs currently target the `connection` database; there is no SQLite or separate pytest database.
- Do not copy, rewrite, or automatically reprocess historical correction results.
- Store one `QuestionResult` per leaf subquestion and expose one API card per main question.
- Never merge annotations across `source_media_id` values or recalculate model-provided normalized coordinates.
- Keep `response_format={"type": "json_object"}` in the Vision request.
- Because focused pytest commands use the persistent `connection` database, prefer pure unit tests and avoid running the complete backend suite during this change.

---

## File Map

- `backend/app/services/correction_annotation_service.py`: parse structured and combined question identifiers, normalize leaf rows, filter annotations, and detect safe global-number gaps.
- `backend/app/services/correction_ai_service.py`: request leaf-level three-part identifiers and turn gap detection into a review reason.
- `backend/app/models/__init__.py`: add nullable `section_no` and `subquestion_no` columns to `QuestionResult`.
- `backend/app/core/database.py`: perform an idempotent MySQL compatibility upgrade for the two new columns after `create_all()`.
- `backend/app/services/correction_service.py`: persist the two new identity fields without changing annotation coordinates.
- `backend/app/services/result_page_service.py`: aggregate leaf rows into main-question payloads and page summaries.
- `backend/app/api/routers/results.py`: reuse the same aggregation for top-level `questions`.
- `miniapp/components/annotated-homework-page/index.wxml`: show nested leaf details below each annotated page.
- `miniapp/components/annotated-homework-page/index.wxss`: style main-question and subquestion rows.
- `miniapp/pages/parent/task-result/index.wxml`: render nested subquestions in the no-image fallback.
- `miniapp/pages/parent/task-result/index.wxss`: style fallback subquestions.
- `miniapp/pages/student/result-detail/index.wxml`: render nested subquestions in the no-image fallback.
- `miniapp/pages/student/result-detail/index.wxss`: style fallback subquestions.
- `backend/tests/test_question_hierarchy.py`: pure parser, normalization, gap-detection, aggregation, annotation, history, and cross-page tests.
- `backend/tests/test_correction_ai_payload.py`: assert that the Vision prompt requests all three numbering levels and exhaustive scanning.
- `backend/tests/test_database_schema_upgrade.py`: verify idempotent MySQL DDL selection using fakes, without touching persistent records.
- `miniapp/tests/result-page-layout.test.js`: assert nested main/subquestion markup exists in student, parent, and annotated-page views.

---

### Task 1: Normalize AI Output into Three-Level Leaf Questions

**Files:**
- Create: `backend/tests/test_question_hierarchy.py`
- Modify: `backend/app/services/correction_annotation_service.py`
- Modify: `backend/app/services/correction_ai_service.py`
- Modify: `backend/tests/test_ai_services.py`

**Interfaces:**
- Produces: `parse_question_identity(raw: dict) -> tuple[str | None, str, str | None]`.
- Produces: `normalize_question_leaves(raw_questions: object, threshold: float) -> list[dict]`.
- Produces: `missing_global_question_nos(questions: list[dict]) -> list[int]`.
- Consumed later: every normalized leaf contains `source_image_index`, `section_no`, `question_no`, `subquestion_no`, grading fields, and `annotations`.

- [ ] **Step 1: Write failing identity and real-worksheet regression tests**

```python
from backend.app.services.correction_annotation_service import (
    missing_global_question_nos,
    normalize_question_leaves,
    parse_question_identity,
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
        "一、1", "一、2", "一、3", "二、4", "二、5", "二、6", "二、7",
        "三、8", "三、9", "三、10", "三、11",
        *[f"四、12({number})" for number in range(1, 8)],
        "四、13(1)", "四、13(2)", "四、14(1)", "四、14(2)",
    ]
    leaves = normalize_question_leaves(
        [{"source_image_index": 1, "question_no": value, "is_correct": True}
         for value in identifiers],
        threshold=0.65,
    )

    assert len(leaves) == 22
    assert len({(q["section_no"], q["question_no"]) for q in leaves}) == 14
    assert leaves[11]["subquestion_no"] == "1"
    assert leaves[-1]["subquestion_no"] == "2"
```

- [ ] **Step 2: Run the parser tests and verify they fail**

Run: `pytest backend/tests/test_question_hierarchy.py -q`

Expected: FAIL because `parse_question_identity`, `normalize_question_leaves`, and `missing_global_question_nos` do not exist.

- [ ] **Step 3: Implement identity parsing and leaf normalization**

Replace the prefix-only normalization and main-question grouping with helpers that:

```python
COMBINED_QUESTION_PATTERNS = (
    re.compile(r"^\s*([一二三四五六七八九十百]+)\s*[、,.，]\s*(\d+)\s*(?:[（(]\s*([^）)]+)\s*[）)])?\s*$"),
    re.compile(r"^\s*(?:第\s*)?(\d+)\s*(?:题)?\s*(?:[（(]\s*([^）)]+)\s*[）)])?\s*$"),
)


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def parse_question_identity(raw: dict) -> tuple[str | None, str, str | None]:
    """Prefer explicit fields and use combined question_no only as fallback."""
    combined = _optional_text(raw.get("question_no")) or ""
    parsed_section = None
    parsed_main = combined
    parsed_subquestion = None
    section_match = COMBINED_QUESTION_PATTERNS[0].match(combined)
    number_match = COMBINED_QUESTION_PATTERNS[1].match(combined)
    if section_match:
        parsed_section, parsed_main, parsed_subquestion = section_match.groups()
    elif number_match:
        parsed_main, parsed_subquestion = number_match.groups()
    return (
        _optional_text(raw.get("section_no")) or parsed_section,
        parsed_main.strip(),
        _optional_text(raw.get("subquestion_no")) or parsed_subquestion,
    )


def _merge_status(current: bool | None, incoming: bool | None) -> bool | None:
    if current is False or incoming is False:
        return False
    if current is None or incoming is None:
        return None
    return True


def normalize_question_leaves(raw_questions: object, threshold: float) -> list[dict]:
    """Normalize and de-duplicate exact leaves, never aggregate sibling subquestions."""
    grouped: dict[tuple[int, str | None, str, str | None], dict] = {}
    for raw in raw_questions if isinstance(raw_questions, list) else []:
        if not isinstance(raw, dict):
            continue
        section_no, question_no, subquestion_no = parse_question_identity(raw)
        if not question_no:
            continue
        image_index = max(1, int(_number(raw.get("source_image_index"), 1)))
        key = (image_index, section_no, question_no, subquestion_no)
        annotations = normalize_annotations(raw.get("annotations"), threshold)
        if raw.get("is_correct") is None:
            annotations = remove_conclusion_annotations(annotations)
        if key not in grouped:
            grouped[key] = {
                "source_image_index": image_index,
                "section_no": section_no,
                "question_no": question_no,
                "subquestion_no": subquestion_no,
                "question_type": raw.get("question_type") or "unknown",
                "recognized_answer": raw.get("recognized_answer"),
                "expected_answer": raw.get("expected_answer"),
                "is_correct": raw.get("is_correct"),
                "score": raw.get("score"),
                "explanation": _optional_text(raw.get("explanation")),
                "confidence_score": raw.get("confidence_score"),
                "annotations": annotations,
            }
            continue
        row = grouped[key]
        row["is_correct"] = _merge_status(row["is_correct"], raw.get("is_correct"))
        row["annotations"].extend(annotations)
        explanation = _optional_text(raw.get("explanation"))
        if explanation and explanation not in str(row.get("explanation") or ""):
            row["explanation"] = "；".join(
                value for value in (row.get("explanation"), explanation) if value
            )
    return list(grouped.values())
```

Keep `normalize_annotations()` and `remove_conclusion_annotations()` behavior unchanged. Empty/invalid question numbers are skipped instead of creating an unaddressable result row.

- [ ] **Step 4: Add and implement safe gap-detection tests**

```python
def test_global_sequence_reports_missing_main_questions():
    questions = [
        {"section_no": "一", "question_no": str(number), "subquestion_no": None}
        for number in [1, 2, 3, 5, 6]
    ]
    assert missing_global_question_nos(questions) == [4]


def test_section_number_reset_is_not_reported_as_a_global_gap():
    questions = [
        {"section_no": section, "question_no": str(number), "subquestion_no": None}
        for section in ("一", "二")
        for number in (1, 2, 3)
    ]
    assert missing_global_question_nos(questions) == []
```

Implement `missing_global_question_nos()` by first reducing leaves to insertion-ordered unique `(section_no, question_no)` mains. Return gaps only when all mains are Arabic positive integers, the number sequence does not reset/decrease across section transitions, and there are no duplicate main numbers in distinct sections. Then compare the observed set with `range(1, max_no + 1)`.

```python
def missing_global_question_nos(questions: list[dict]) -> list[int]:
    mains = list(dict.fromkeys(
        (question.get("section_no"), str(question.get("question_no") or ""))
        for question in questions
    ))
    if not mains or any(not number.isdigit() or int(number) < 1 for _, number in mains):
        return []
    numbers = [int(number) for _, number in mains]
    sections_by_number: dict[int, set[str | None]] = {}
    for (section, _), number in zip(mains, numbers):
        sections_by_number.setdefault(number, set()).add(section)
    if any(len(sections) > 1 for sections in sections_by_number.values()):
        return []
    for index in range(1, len(mains)):
        previous_section, _ = mains[index - 1]
        current_section, _ = mains[index]
        if current_section != previous_section and numbers[index] <= numbers[index - 1]:
            return []
    observed = set(numbers)
    return [number for number in range(1, max(numbers) + 1) if number not in observed]
```

- [ ] **Step 5: Wire leaves and missing-number review into correction normalization**

Update `normalize_correction_payload()` to call `normalize_question_leaves()`. Append a review reason without erasing an existing reason:

```python
missing_nos = missing_global_question_nos(questions)
if missing_nos:
    needs_review = True
    missing_text = "、".join(str(number) for number in missing_nos)
    reason_parts.append(f"未生成第 {missing_text} 题批改结果")
```

Update the old `test_normalize_correction_groups_subquestions_and_keeps_annotations` expectation from one grouped main row to two leaf rows with `question_no == "2"` and `subquestion_no` values `"1"`, `"2"`.

- [ ] **Step 6: Run the pure normalization tests**

Run: `pytest backend/tests/test_question_hierarchy.py backend/tests/test_ai_services.py -q`

Expected: PASS; the real 22 identifiers remain 22 leaves and represent 14 main questions.

- [ ] **Step 7: Commit the leaf-normalization change**

```bash
git add backend/app/services/correction_annotation_service.py backend/app/services/correction_ai_service.py backend/tests/test_question_hierarchy.py backend/tests/test_ai_services.py
git commit -m "支持三级题号叶子解析"
```

---

### Task 2: Upgrade the Vision Prompt for Exhaustive Leaf Recognition

**Files:**
- Modify: `backend/app/services/correction_ai_service.py`
- Modify: `backend/tests/test_correction_ai_payload.py`

**Interfaces:**
- Consumes: `normalize_question_leaves()` from Task 1 through `parse_correction_content()`.
- Produces: AI requests whose question objects contain `section_no`, `question_no`, `subquestion_no`, `source_image_index`, grading fields, and annotations.

- [ ] **Step 1: Extend the payload test with exact prompt requirements**

After extracting the request text from `captured["payload"]`, assert:

```python
prompt = captured["payload"]["messages"][0]["content"][0]["text"]
assert "section_no" in prompt
assert "subquestion_no" in prompt
assert "每个叶子小题独立返回" in prompt
assert "从上到下" in prompt
assert "不要跳过选择题、填空题、计算题" in prompt
assert "source_image_index" in prompt
assert captured["payload"]["response_format"] == {"type": "json_object"}
```

- [ ] **Step 2: Run the prompt test and verify it fails**

Run: `pytest backend/tests/test_correction_ai_payload.py -q`

Expected: FAIL because the existing prompt asks the model to merge `(1)(2)(3)` into one row.

- [ ] **Step 3: Replace the conflicting prompt instructions**

The prompt must explicitly state:

```text
从上到下检查每张照片中的全部印刷题号，不要跳过选择题、填空题、计算题或写在页边的答案。
每个叶子小题独立返回一条 questions 记录，不要把 (1)(2)(3) 合并。
section_no 只返回章节编号（如 一、二、四），question_no 只返回阿拉伯主题题号（如 1、12、14），subquestion_no 只返回小题号；没有对应层级时返回 null。
source_image_index 必须是该题所在学生作业照片从 1 开始的序号。
```

Retain the JSON Object response format and all existing grading/annotation field definitions.

- [ ] **Step 4: Run the prompt and parser tests**

Run: `pytest backend/tests/test_correction_ai_payload.py backend/tests/test_question_hierarchy.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the prompt upgrade**

```bash
git add backend/app/services/correction_ai_service.py backend/tests/test_correction_ai_payload.py
git commit -m "升级整页题号识别提示词"
```

---

### Task 3: Persist Three-Level Identity and Idempotently Upgrade MySQL

**Files:**
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/core/database.py`
- Modify: `backend/app/services/correction_service.py`
- Create: `backend/tests/test_database_schema_upgrade.py`
- Modify: `backend/tests/test_correction_annotations.py`

**Interfaces:**
- Produces: nullable ORM fields `QuestionResult.section_no` and `QuestionResult.subquestion_no` as `String(32)`.
- Produces: `ensure_question_result_hierarchy_columns(bind=engine) -> None`.
- Consumes: leaf dictionaries from Task 1.

- [ ] **Step 1: Write a failing pure schema-upgrade test**

Use a fake bind/inspector seam so no persistent rows are created:

```python
from contextlib import contextmanager

import backend.app.core.database as database


class FakeInspector:
    def __init__(self, columns):
        self.columns = columns

    def get_table_names(self):
        return ["question_results"]

    def get_columns(self, table_name):
        assert table_name == "question_results"
        return [{"name": name} for name in self.columns]


class FakeConnection:
    def __init__(self, executed):
        self.executed = executed

    def execute(self, statement):
        self.executed.append(str(statement))


class FakeBind:
    def __init__(self, existing_columns, executed):
        self.existing_columns = existing_columns
        self.executed = executed

    @contextmanager
    def begin(self):
        yield FakeConnection(self.executed)


def test_hierarchy_schema_upgrade_adds_only_missing_columns(monkeypatch):
    executed = []
    fake_bind = FakeBind(existing_columns={"id", "question_no", "section_no"}, executed=executed)
    monkeypatch.setattr(database, "inspect", lambda bind: FakeInspector(bind.existing_columns))

    database.ensure_question_result_hierarchy_columns(fake_bind)

    assert executed == [
        "ALTER TABLE question_results ADD COLUMN subquestion_no VARCHAR(32) NULL"
    ]


def test_hierarchy_schema_upgrade_is_idempotent(monkeypatch):
    executed = []
    fake_bind = FakeBind(
        existing_columns={"id", "question_no", "section_no", "subquestion_no"},
        executed=executed,
    )
    monkeypatch.setattr(database, "inspect", lambda bind: FakeInspector(bind.existing_columns))

    database.ensure_question_result_hierarchy_columns(fake_bind)

    assert executed == []
```

The fake bind supplies only the context-managed transaction and captured execution required by the helper; the module-level `inspect()` is monkeypatched to avoid a real connection.

- [ ] **Step 2: Run the schema-upgrade test and verify it fails**

Run: `pytest backend/tests/test_database_schema_upgrade.py -q`

Expected: FAIL because the compatibility helper and ORM fields do not exist.

- [ ] **Step 3: Add ORM columns and the guarded MySQL compatibility helper**

In `QuestionResult` add:

```python
section_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
question_no: Mapped[str] = mapped_column(String(32))
subquestion_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
```

In `backend/app/core/database.py`, import `inspect` and `text`, define:

```python
QUESTION_RESULT_HIERARCHY_COLUMNS = {
    "section_no": "VARCHAR(32) NULL",
    "subquestion_no": "VARCHAR(32) NULL",
}


def ensure_question_result_hierarchy_columns(bind=engine) -> None:
    inspector = inspect(bind)
    if "question_results" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("question_results")}
    with bind.begin() as connection:
        for name, sql_type in QUESTION_RESULT_HIERARCHY_COLUMNS.items():
            if name not in existing:
                connection.execute(text(
                    f"ALTER TABLE question_results ADD COLUMN {name} {sql_type}"
                ))
```

Call it from `init_db()` after `Base.metadata.create_all(bind=engine)`. Column names and SQL types come only from the module constant, not external input.

- [ ] **Step 4: Persist the hierarchy and extend persistence assertions**

Pass normalized fields into each ORM row:

```python
section_no=question.get("section_no"),
question_no=str(question.get("question_no") or ""),
subquestion_no=question.get("subquestion_no"),
```

Extend the existing persistence test payload to `section_no="四"`, `question_no="12"`, `subquestion_no="3"`, then assert all three saved values and the existing annotation/source-media assertions.

- [ ] **Step 5: Run safe unit tests, then run the focused persistence test once**

Run: `pytest backend/tests/test_database_schema_upgrade.py -q`

Expected: PASS without opening the production database.

Run: `pytest backend/tests/test_correction_annotations.py::test_result_persistence_maps_page_index_to_media_id -q`

Expected: PASS. This focused test writes uniquely identified fixture data to `connection`, which is accepted by the current repository test policy.

- [ ] **Step 6: Commit the persistence and compatibility upgrade**

```bash
git add backend/app/models/__init__.py backend/app/core/database.py backend/app/services/correction_service.py backend/tests/test_database_schema_upgrade.py backend/tests/test_correction_annotations.py
git commit -m "保存三级题号并升级数据库结构"
```

---

### Task 4: Aggregate Leaves into Main Questions at the API Boundary

**Files:**
- Modify: `backend/app/services/result_page_service.py`
- Modify: `backend/app/api/routers/results.py`
- Modify: `backend/tests/test_question_hierarchy.py`
- Modify: `backend/tests/test_v1_flow.py`

**Interfaces:**
- Produces: `aggregate_question_results(questions: list[QuestionResult]) -> list[dict]`.
- Main payload fields: `section_no`, `question_no`, `is_correct`, existing answer/explanation/confidence fields, flat `annotations`, and `subquestions`.
- Leaf payload fields: `section_no`, `question_no`, `subquestion_no`, grading fields, and leaf `annotations`.

- [ ] **Step 1: Write pure aggregation, annotation, status, history, and cross-page tests**

Construct `SimpleNamespace` leaves and assert:

```python
import json
from types import SimpleNamespace

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


def test_result_aggregation_keeps_subquestions_and_flattens_annotations():
    questions = [
        leaf(media=10, section="四", main="12", sub="1", status=True,
             annotations=[{"kind": "correct_tick", "x": 0.1}]),
        leaf(media=10, section="四", main="12", sub="2", status=False,
             annotations=[{"kind": "error_circle", "x": 0.6}]),
    ]
    mains = aggregate_question_results(questions)

    assert len(mains) == 1
    assert mains[0]["section_no"] == "四"
    assert mains[0]["question_no"] == "12"
    assert mains[0]["is_correct"] is False
    assert [q["subquestion_no"] for q in mains[0]["subquestions"]] == ["1", "2"]
    assert [a["kind"] for a in mains[0]["annotations"]] == ["correct_tick", "error_circle"]


def test_result_aggregation_never_moves_annotations_between_pages():
    mains = aggregate_question_results([
        leaf(media=10, section="一", main="1", sub=None, status=True,
             annotations=[{"kind": "correct_tick"}]),
        leaf(media=20, section="一", main="1", sub=None, status=False,
             annotations=[{"kind": "error_circle"}]),
    ])
    assert len(mains) == 2
    assert mains[0]["source_media_id"] == 10
    assert mains[1]["source_media_id"] == 20


def test_historical_single_level_result_has_empty_subquestions():
    main = aggregate_question_results([
        leaf(media=10, section=None, main="6", sub=None, status=True, annotations=[])
    ])[0]
    assert main["question_no"] == "6"
    assert main["subquestions"] == []
```

Also parameterize leaf statuses to assert `False > None > True` for a main question.

- [ ] **Step 2: Run the aggregation tests and verify they fail**

Run: `pytest backend/tests/test_question_hierarchy.py -q`

Expected: FAIL because `aggregate_question_results()` does not exist.

- [ ] **Step 3: Implement one shared aggregator**

In `result_page_service.py`:

```python
def _question_payload(question: QuestionResult) -> dict:
    try:
        annotations = json.loads(question.annotations_json or "[]")
    except json.JSONDecodeError:
        annotations = []
    return {
        "section_no": question.section_no,
        "question_no": question.question_no,
        "subquestion_no": question.subquestion_no,
        "question_type": question.question_type,
        "is_correct": question.is_correct,
        "recognized_answer": question.recognized_answer,
        "expected_answer": question.expected_answer,
        "score": question.score,
        "explanation": question.explanation,
        "confidence_score": question.confidence_score,
        "annotations": annotations if isinstance(annotations, list) else [],
    }


def aggregate_question_status(statuses: list[bool | None]) -> bool | None:
    if any(status is False for status in statuses):
        return False
    if any(status is None for status in statuses):
        return None
    return True


def aggregate_question_results(questions: list[QuestionResult]) -> list[dict]:
    """Group only leaves from the same page, section, and main question."""
    grouped: dict[tuple[int | None, str | None, str], list[dict]] = {}
    for question in questions:
        leaf = _question_payload(question)
        key = (question.source_media_id, question.section_no, question.question_no)
        grouped.setdefault(key, []).append(leaf)
    result = []
    for (media_id, section_no, question_no), leaves in grouped.items():
        subquestions = [leaf for leaf in leaves if leaf["subquestion_no"] is not None]
        only_leaf = leaves[0]

        def combined(field: str):
            values = []
            for leaf in leaves:
                value = leaf.get(field)
                if value in (None, ""):
                    continue
                prefix = f"({leaf['subquestion_no']}) " if leaf["subquestion_no"] else ""
                values.append(f"{prefix}{value}")
            return "；".join(values) or None

        result.append({
            "source_media_id": media_id,
            "section_no": section_no,
            "question_no": question_no,
            "is_correct": aggregate_question_status(
                [leaf["is_correct"] for leaf in leaves]
            ),
            "recognized_answer": only_leaf["recognized_answer"] if len(leaves) == 1 else combined("recognized_answer"),
            "expected_answer": only_leaf["expected_answer"] if len(leaves) == 1 else combined("expected_answer"),
            "explanation": only_leaf["explanation"] if len(leaves) == 1 else combined("explanation"),
            "confidence_score": min(
                (leaf["confidence_score"] for leaf in leaves if leaf["confidence_score"] is not None),
                default=None,
            ),
            "annotations": [
                annotation
                for leaf in leaves
                for annotation in leaf["annotations"]
            ],
            "subquestions": subquestions,
        })
    return result
```

For a multi-leaf main, build readable aggregate answer/explanation strings by prefixing each non-empty value with `({subquestion_no})`; never discard the nested originals.

- [ ] **Step 4: Reuse the aggregator for pages and the top-level route**

`build_result_pages()` must aggregate `by_media[item.id]`, then derive each summary list from aggregated mains so each main number appears once. Add `total_pages=len(media)` to every page because the existing component already reads it.

In `results.task_result()`, replace the flat ORM list comprehension with:

```python
"questions": aggregate_question_results(questions),
```

- [ ] **Step 5: Extend the API flow test**

Add two `QuestionResult` rows for `section_no="四"`, `question_no="12"`, subquestions `"1"` and `"2"` on the same media. Assert top-level and page `questions` each contain one main 12, its status is false, it has two nested subquestions, and its flat annotations contain both leaf annotations. Keep the ungraded-page and authorization assertions intact.

- [ ] **Step 6: Run pure tests and the focused API flow**

Run: `pytest backend/tests/test_question_hierarchy.py -q`

Expected: PASS.

Run: `pytest backend/tests/test_v1_flow.py::test_result_returns_full_annotated_homework_pages -q`

Expected: PASS using the persistent `connection` database.

- [ ] **Step 7: Commit the API aggregation change**

```bash
git add backend/app/services/result_page_service.py backend/app/api/routers/results.py backend/tests/test_question_hierarchy.py backend/tests/test_v1_flow.py
git commit -m "按主题题聚合批改结果"
```

---

### Task 5: Render Nested Subquestions Without Breaking the Circle Overlay

**Files:**
- Modify: `miniapp/components/annotated-homework-page/index.wxml`
- Modify: `miniapp/components/annotated-homework-page/index.wxss`
- Modify: `miniapp/pages/parent/task-result/index.wxml`
- Modify: `miniapp/pages/parent/task-result/index.wxss`
- Modify: `miniapp/pages/student/result-detail/index.wxml`
- Modify: `miniapp/pages/student/result-detail/index.wxss`
- Modify: `miniapp/tests/result-page-layout.test.js`

**Interfaces:**
- Consumes: main-question payloads with flat `annotations` plus nested `subquestions` from Task 4.
- Preserves: `annotated-homework-page/index.js` reads only `question.annotations`, so both `12(1)` and `12(2)` annotation boxes remain in the same page overlay without a JavaScript behavior change.

- [ ] **Step 1: Add failing static layout assertions**

```javascript
assert.match(componentWxml, /wx:for="{{page.questions}}"/)
assert.match(componentWxml, /wx:for="{{item.subquestions}}"/)
assert.match(componentWxml, /subquestion_no/)
assert.match(pageWxml, /wx:for="{{item.subquestions}}"/)
```

Apply the fallback assertion to both parent and student WXML.

- [ ] **Step 2: Run the miniapp layout test and verify it fails**

Run: `node --test miniapp/tests/result-page-layout.test.js`

Expected: FAIL because no view currently renders `subquestions`.

- [ ] **Step 3: Render main and leaf details on annotated pages**

Below `.page-summary`, add a main-question list:

```xml
<view wx:if="{{page.questions.length}}" class="question-list">
  <view wx:for="{{page.questions}}" wx:key="question_no" class="question-card">
    <view class="question-title">
      {{item.section_no ? item.section_no + '、' : ''}}{{item.question_no}} ·
      {{item.is_correct === null ? '待复核' : (item.is_correct ? '正确' : '需订正')}}
    </view>
    <view wx:for="{{item.subquestions}}" wx:for-item="subitem" wx:key="subquestion_no" class="subquestion-row">
      <view class="subquestion-title">({{subitem.subquestion_no}}) {{subitem.is_correct === null ? '待复核' : (subitem.is_correct ? '正确' : '需订正')}}</view>
      <view wx:if="{{subitem.recognized_answer}}" class="muted">学生答案：{{subitem.recognized_answer}}</view>
      <view wx:if="{{subitem.expected_answer}}" class="muted">参考答案：{{subitem.expected_answer}}</view>
      <view wx:if="{{subitem.explanation}}" class="muted">{{subitem.explanation}}</view>
    </view>
  </view>
</view>
```

Use distinct `wx:for-item="subitem"` to avoid the nested loop shadowing the main item.

- [ ] **Step 4: Render the same nested details in parent/student text fallbacks**

Keep existing main fields for historical single-level records. Within each fallback card add the same `item.subquestions` loop and leaf answer/explanation fields.

- [ ] **Step 5: Add focused styles**

Add compact styles consistently to the component and both pages:

```css
.question-list { display: grid; gap: 14rpx; }
.question-card { display: grid; gap: 10rpx; padding: 18rpx; border-radius: 14rpx; background: #f6fbf4; }
.question-title, .subquestion-title { color: #39533e; font-weight: 700; }
.subquestion-row { display: grid; gap: 6rpx; padding: 12rpx 0 0 20rpx; border-top: 1rpx solid #dfe9dc; }
```

- [ ] **Step 6: Run miniapp tests**

Run: `node --test miniapp/tests/result-page-layout.test.js miniapp/tests/annotation-style.test.js`

Expected: PASS; the component still consumes the main question's flat annotation list, and nested details appear in all result views.

- [ ] **Step 7: Commit the miniapp rendering change**

```bash
git add miniapp/components/annotated-homework-page/index.wxml miniapp/components/annotated-homework-page/index.wxss miniapp/pages/parent/task-result/index.wxml miniapp/pages/parent/task-result/index.wxss miniapp/pages/student/result-detail/index.wxml miniapp/pages/student/result-detail/index.wxss miniapp/tests/result-page-layout.test.js
git commit -m "展示主题题与小题批改详情"
```

---

### Task 6: Apply Production Schema Upgrade and Verify the Complete Chain

**Files:**
- Modify only if verification exposes a defect: files already listed in Tasks 1–5.

**Interfaces:**
- Consumes: `init_db()` compatibility upgrade, leaf normalization, persistence, aggregation, and miniapp payload contract.
- Produces: production `connection.question_results` with nullable `section_no` and `subquestion_no` columns.

- [ ] **Step 1: Verify the pending diff and repository state**

Run: `git status --short && git diff --check`

Expected: no uncommitted implementation changes and no whitespace errors.

- [ ] **Step 2: Apply the idempotent schema upgrade to the configured development/production target**

Run:

```bash
python -c 'from backend.app.core.database import init_db; init_db()'
```

Expected: exits 0. With the current `.env`, `APP_ENV=development` resolves `DB_PROD_OUT` to MySQL database `connection`; no historical rows are updated.

- [ ] **Step 3: Verify both nullable columns in MySQL without printing credentials**

Run a short SQLAlchemy inspection command that prints only column name/type/nullability for `section_no` and `subquestion_no`.

Expected:

```text
section_no VARCHAR(32) True
subquestion_no VARCHAR(32) True
```

- [ ] **Step 4: Run the safe focused regression set**

Run:

```bash
pytest backend/tests/test_question_hierarchy.py backend/tests/test_database_schema_upgrade.py backend/tests/test_correction_ai_payload.py backend/tests/test_database_config.py -q
node --test miniapp/tests/result-page-layout.test.js miniapp/tests/annotation-style.test.js
```

Expected: all selected Python and Node tests pass. Do not run `pytest backend/tests` because it writes broad fixture data into persistent `connection`.

- [ ] **Step 5: Exercise the real 22-identifier payload in memory**

Run a read-only Python snippet that passes the observed identifiers through `normalize_correction_payload()` and `aggregate_question_results()` using transient `SimpleNamespace` objects. Assert and print only:

```text
leaf_questions=22
main_questions=14
main_numbers=1,2,3,4,5,6,7,8,9,10,11,12,13,14
```

Expected: exactly the output above; no correction rows are inserted.

- [ ] **Step 6: Inspect final commits and diff**

Run: `git status --short && git log -6 --oneline && git diff b20174e..HEAD --stat`

Expected: clean working tree; focused commits for parsing, prompt, schema/persistence, API aggregation, and miniapp rendering.

- [ ] **Step 7: Report operational behavior clearly**

State that future corrections save 22 leaf rows for this worksheet but APIs display 14 main questions; question 12 contains seven subquestions, 13 and 14 contain two each. Confirm that leaf annotations are filtered and stored individually, then flattened only within their original page/main question for circle rendering. Also state that existing failed submissions are not reprocessed automatically and model coordinate precision still depends on source-image quality.
