# 老师式整页作业批改 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 学生提交几张作业照片，结果页就按顺序展示几页完整原图，并用老师式绿色勾、红圈、红叉和批语直接标注卷面，同时提供可离开的真实批改进度。

**Architecture:** 视觉模型在现有单次批改请求中返回按大题聚合的结果、图片序号和归一化标注坐标。后端校验并持久化标注，通过有权限校验的结果与媒体接口返回按页数据；学生端和家长端复用一个整页批改组件，以百分比坐标覆盖标注层，不修改原图。

**Tech Stack:** FastAPI、SQLAlchemy、SQLite、Celery、pytest、原生微信小程序 JavaScript/WXML/WXSS、Node.js `node:test`

## Global Constraints

- 学生端仍只把照片作为可标注卷面；PDF、DOCX 和视频不进入整页图片标注流程。
- 同一大题下的 `(1)(2)(3)` 等小问必须合并为一条大题结果。
- 正确使用绿色；错误使用红色；待复核不使用红色或绿色结论标记。
- 标注使用 `0..1` 归一化坐标，不修改学生原始照片。
- 标注定位置信度低于 `0.65` 时丢弃该标注，但保留文字结果。
- 第一版只提供小程序内状态提醒，不接入微信订阅消息。
- 不新增前端依赖，不更换现有技术栈，不重构无关页面。
- 不覆盖或提交现有未关联修改 `miniapp/utils/constants.js`。

## File Structure

- `backend/app/models/__init__.py`：保存提交阶段、题目所属照片和标注 JSON。
- `backend/app/core/database.py`：为已有 SQLite 数据库补充兼容列。
- `backend/app/core/config.py`：提供标注置信度阈值默认值。
- `backend/app/services/correction_annotation_service.py`：规范化坐标、合并同页同大题结果。
- `backend/app/services/correction_ai_service.py`：定义视觉模型的大题与标注输出契约。
- `backend/app/services/correction_service.py`：映射图片序号、持久化结果并推进阶段。
- `backend/app/worker/tasks/correct_homework.py`：记录批改任务的真实生命周期。
- `backend/app/services/result_page_service.py`：构建按上传顺序排列的卷面结果。
- `backend/app/services/access_service.py`：集中判断学生本人或同家庭家长的访问权限。
- `backend/app/api/routers/results.py`：返回阶段、页面和标注。
- `backend/app/api/routers/submissions.py`：提供带权限校验的原图内容接口。
- `miniapp/utils/result-state.js`：把服务端阶段转换为等待页文案。
- `miniapp/utils/task-status.js`：把处理中阶段转换为任务卡片文案。
- `miniapp/utils/annotation-style.js`：把归一化坐标转换为安全的百分比样式。
- `miniapp/services/correction-media.js`：通过带授权头的 `wx.downloadFile` 下载卷面原图。
- `miniapp/components/annotated-homework-page/index.*`：完整卷面与标注层组件。
- `miniapp/pages/student/result-detail/index.*`：学生等待页和整页结果列表。
- `miniapp/pages/parent/task-result/index.*`：复用卷面列表并保留家长复核。

---

### Task 1: 数据库字段与配置

**Files:**
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/core/database.py`
- Modify: `backend/app/core/config.py`
- Create: `backend/tests/test_correction_annotations.py`

**Interfaces:**
- Produces: `Submission.processing_stage: str | None`
- Produces: `Submission.processing_message: str | None`
- Produces: `QuestionResult.source_media_id: int | None`
- Produces: `QuestionResult.annotations_json: str | None`
- Produces: `Settings.annotation_confidence_threshold: float = 0.65`

- [ ] **Step 1: Write the failing schema test**

```python
from sqlalchemy import inspect

from backend.app.core.config import Settings
from backend.app.core.database import engine, init_db


def test_annotation_schema_and_default_threshold_exist():
    init_db()
    inspector = inspect(engine)
    submission_columns = {column["name"] for column in inspector.get_columns("submissions")}
    question_columns = {column["name"] for column in inspector.get_columns("question_results")}

    assert {"processing_stage", "processing_message"} <= submission_columns
    assert {"source_media_id", "annotations_json"} <= question_columns
    assert Settings().annotation_confidence_threshold == 0.65
```

- [ ] **Step 2: Run the schema test and verify it fails**

Run: `pytest backend/tests/test_correction_annotations.py::test_annotation_schema_and_default_threshold_exist -q`

Expected: FAIL because the four columns and configuration field do not exist.

- [ ] **Step 3: Add model and configuration fields**

Add to `Submission`:

```python
processing_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
processing_message: Mapped[str | None] = mapped_column(Text, nullable=True)
```

Add to `QuestionResult`:

```python
source_media_id: Mapped[int | None] = mapped_column(ForeignKey("submission_media.id"), nullable=True, index=True)
annotations_json: Mapped[str | None] = mapped_column(Text, nullable=True)
```

Add to `Settings`:

```python
annotation_confidence_threshold: float = 0.65
```

- [ ] **Step 4: Add SQLite compatibility upgrades**

Read `question_result_columns` alongside the existing inspected column sets, then execute:

```python
if "processing_stage" not in submission_columns:
    connection.execute(text("ALTER TABLE submissions ADD COLUMN processing_stage VARCHAR(32)"))
if "processing_message" not in submission_columns:
    connection.execute(text("ALTER TABLE submissions ADD COLUMN processing_message TEXT"))
if "source_media_id" not in question_result_columns:
    connection.execute(text("ALTER TABLE question_results ADD COLUMN source_media_id INTEGER"))
if "annotations_json" not in question_result_columns:
    connection.execute(text("ALTER TABLE question_results ADD COLUMN annotations_json TEXT"))
```

- [ ] **Step 5: Run the focused test**

Run: `pytest backend/tests/test_correction_annotations.py::test_annotation_schema_and_default_threshold_exist -q`

Expected: `1 passed`.

- [ ] **Step 6: Commit the schema change**

```bash
git add backend/app/models/__init__.py backend/app/core/database.py backend/app/core/config.py backend/tests/test_correction_annotations.py
git commit -m "增加卷面批改数据字段"
```

---

### Task 2: 标注规范化与大题聚合

**Files:**
- Create: `backend/app/services/correction_annotation_service.py`
- Modify: `backend/tests/test_correction_annotations.py`

**Interfaces:**
- Consumes: `settings.annotation_confidence_threshold`
- Produces: `normalize_question_no(value: object) -> str`
- Produces: `normalize_annotations(raw_annotations: object, threshold: float) -> list[dict]`
- Produces: `group_questions(raw_questions: object, threshold: float) -> list[dict]`

- [ ] **Step 1: Write failing normalization tests**

```python
from backend.app.services.correction_annotation_service import group_questions, normalize_annotations


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
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `pytest backend/tests/test_correction_annotations.py -q`

Expected: FAIL because `correction_annotation_service` does not exist.

- [ ] **Step 3: Implement the focused normalization service**

```python
import re


ALLOWED_ANNOTATION_KINDS = {"correct_tick", "error_circle", "error_cross", "comment"}


def _number(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: object) -> float:
    return max(0.0, min(1.0, _number(value)))


def normalize_question_no(value: object) -> str:
    text = str(value or "").strip()
    match = re.match(r"^(?:第\s*)?([0-9一二三四五六七八九十百]+)", text)
    return match.group(1) if match else text


def normalize_annotations(raw_annotations: object, threshold: float) -> list[dict]:
    normalized = []
    for raw in raw_annotations if isinstance(raw_annotations, list) else []:
        if not isinstance(raw, dict) or raw.get("kind") not in ALLOWED_ANNOTATION_KINDS:
            continue
        confidence = _clamp(raw.get("confidence"))
        if confidence < threshold:
            continue
        x = _clamp(raw.get("x"))
        y = _clamp(raw.get("y"))
        width = min(_clamp(raw.get("width")), 1.0 - x)
        height = min(_clamp(raw.get("height")), 1.0 - y)
        if width <= 0 or height <= 0:
            continue
        normalized.append({
            "kind": raw["kind"],
            "x": x,
            "y": y,
            "width": width,
            "height": height,
            "text": str(raw.get("text") or "").strip() or None,
            "confidence": confidence,
        })
    return normalized


def group_questions(raw_questions: object, threshold: float) -> list[dict]:
    grouped: dict[tuple[int, str], dict] = {}
    for raw in raw_questions if isinstance(raw_questions, list) else []:
        if not isinstance(raw, dict):
            continue
        image_index = max(1, int(_number(raw.get("source_image_index"), 1)))
        question_no = normalize_question_no(raw.get("question_no"))
        key = (image_index, question_no)
        row = grouped.setdefault(key, {
            "source_image_index": image_index,
            "question_no": question_no,
            "question_type": raw.get("question_type") or "unknown",
            "recognized_answer": raw.get("recognized_answer"),
            "expected_answer": raw.get("expected_answer"),
            "is_correct": True,
            "score": raw.get("score"),
            "explanation_parts": [],
            "confidence_score": raw.get("confidence_score"),
            "annotations": [],
        })
        if raw.get("is_correct") is None:
            row["is_correct"] = None
        elif raw.get("is_correct") is False and row["is_correct"] is not None:
            row["is_correct"] = False
        explanation = str(raw.get("explanation") or "").strip()
        if explanation and explanation not in row["explanation_parts"]:
            row["explanation_parts"].append(explanation)
        row["annotations"].extend(normalize_annotations(raw.get("annotations"), threshold))
    result = []
    for row in grouped.values():
        row["explanation"] = "；".join(row.pop("explanation_parts")) or None
        result.append(row)
    return result
```

- [ ] **Step 4: Run the focused tests**

Run: `pytest backend/tests/test_correction_annotations.py -q`

Expected: all tests in the file PASS.

- [ ] **Step 5: Commit normalization**

```bash
git add backend/app/services/correction_annotation_service.py backend/tests/test_correction_annotations.py
git commit -m "规范化卷面标注与大题结果"
```

---

### Task 3: 视觉模型大题与坐标契约

**Files:**
- Modify: `backend/app/services/correction_ai_service.py`
- Modify: `backend/tests/test_ai_services.py`

**Interfaces:**
- Consumes: `group_questions(raw_questions, threshold)`
- Produces: `normalize_correction_payload(payload: dict) -> dict` with grouped `questions`
- Produces: model prompt containing stable `source_image_index` instructions

- [ ] **Step 1: Extend the existing prompt test**

After extracting `prompt_text`, add:

```python
assert "按印刷的大题号合并" in prompt_text
assert "source_image_index" in prompt_text
assert "0 到 1" in prompt_text

content = captured_payload["json"]["messages"][0]["content"]
assert content[1]["text"] == "学生作业照片 1"
assert content[2]["type"] == "image_url"
```

Add a normalization test:

```python
def test_normalize_correction_groups_subquestions_and_keeps_annotations():
    payload = normalize_correction_payload({
        "completion_score": 80,
        "accuracy_score": 75,
        "confidence_score": 0.9,
        "questions": [
            {"source_image_index": 1, "question_no": "2(1)", "is_correct": True, "confidence_score": 0.9},
            {"source_image_index": 1, "question_no": "2(2)", "is_correct": False, "confidence_score": 0.9, "annotations": [{"kind": "error_circle", "x": 0.2, "y": 0.3, "width": 0.2, "height": 0.1, "confidence": 0.9}]},
        ],
    })

    assert len(payload["questions"]) == 1
    assert payload["questions"][0]["question_no"] == "2"
    assert payload["questions"][0]["is_correct"] is False
    assert payload["questions"][0]["annotations"][0]["kind"] == "error_circle"
```

- [ ] **Step 2: Run the two focused tests and verify failure**

Run: `pytest backend/tests/test_ai_services.py::test_ai_correction_prompt_includes_assignment_content_and_optional_answer backend/tests/test_ai_services.py::test_normalize_correction_groups_subquestions_and_keeps_annotations -q`

Expected: FAIL because the prompt, image labels and grouping contract are absent.

- [ ] **Step 3: Route question normalization through the new service**

Import `group_questions`, replace the per-question append loop with:

```python
questions = group_questions(
    payload.get("questions"),
    threshold=settings.annotation_confidence_threshold,
)
has_uncertain_question = any(question.get("is_correct") is None for question in questions)
for question in questions:
    question["score"] = _score(question.get("score"), nullable=True)
    question["confidence_score"] = _score(
        question.get("confidence_score"),
        confidence=True,
        nullable=True,
    )
```

Return this `questions` list from `normalize_correction_payload`.

- [ ] **Step 4: Make image order explicit in the model content**

Use this prompt contract in the leading text part:

```python
"请按印刷的大题号合并批改结果，同一大题的(1)(2)(3)不得拆成多条。"
"每道大题返回 source_image_index、question_no、question_type、recognized_answer、"
"expected_answer、is_correct、score、explanation、confidence_score、annotations。"
"annotations 每项包含 kind、x、y、width、height、text、confidence；"
"坐标是相对原图宽高的 0 到 1 小数。正确位置用 correct_tick，"
"错误位置用 error_circle、error_cross 和必要的 comment。"
```

For each homework image, append a label immediately before its image part:

```python
image_index = 0
for item in homework_files:
    local_path = str(local_path_for_submission_media(item))
    if item.media_type == "image":
        image_part = _image_message_part(local_path)
        if image_part:
            image_index += 1
            content.append({"type": "text", "text": f"学生作业照片 {image_index}"})
            content.append(image_part)
```

Keep existing audio/video branches unchanged; video frames are not assigned student page indices.

- [ ] **Step 5: Run AI service tests**

Run: `pytest backend/tests/test_ai_services.py -q`

Expected: all tests PASS.

- [ ] **Step 6: Commit the model contract**

```bash
git add backend/app/services/correction_ai_service.py backend/tests/test_ai_services.py
git commit -m "约束大题批改与卷面坐标输出"
```

---

### Task 4: 持久化标注并记录真实处理阶段

**Files:**
- Modify: `backend/app/services/correction_service.py`
- Modify: `backend/app/worker/tasks/correct_homework.py`
- Modify: `backend/tests/test_correction_annotations.py`
- Modify: `backend/tests/test_ai_services.py`

**Interfaces:**
- Produces: `set_processing_stage(db: Session, submission: Submission, stage: str, message: str) -> None`
- Consumes: normalized question `source_image_index` and `annotations`
- Produces: persisted `QuestionResult.source_media_id` and `annotations_json`

- [ ] **Step 1: Write failing persistence and stage tests**

```python
import json
from datetime import date
from uuid import uuid4

import pytest

from backend.app.core.database import SessionLocal
from backend.app.models import AssignmentBatch, AssignmentItem, DailyTask, Family, QuestionResult, Student, Submission, SubmissionMedia, User
from backend.app.services.correction_service import _create_result_from_payload


@pytest.fixture
def correction_submission():
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
        saved = db.query(QuestionResult).filter(QuestionResult.question_no == "6").one()
        assert saved.source_media_id == second.id
        assert json.loads(saved.annotations_json)[0]["kind"] == "error_circle"
        assert submission.processing_stage == "corrected"
```

Extend the worker failure test:

```python
assert submission.processing_stage == "failed"
assert submission.processing_message == "批改服务暂时不可用，请稍后重试。"
```

- [ ] **Step 2: Run focused tests and verify failure**

Run: `pytest backend/tests/test_correction_annotations.py backend/tests/test_ai_services.py::test_correction_failure_is_persisted_without_mock_result -q`

Expected: FAIL because stages and mapped annotations are not persisted.

- [ ] **Step 3: Add a stage helper and persistence mapping**

```python
def set_processing_stage(db: Session, submission: Submission, stage: str, message: str) -> None:
    submission.processing_stage = stage
    submission.processing_message = message
    db.commit()
    db.refresh(submission)
```

At the start of `create_correction`, load image media in stable order and map one-based indices:

```python
homework_images = db.query(SubmissionMedia).filter(
    SubmissionMedia.submission_id == submission.id,
    SubmissionMedia.purpose == "homework",
    SubmissionMedia.media_type == "image",
).order_by(SubmissionMedia.sort_order, SubmissionMedia.id).all()
media_ids_by_index = {index: media.id for index, media in enumerate(homework_images, start=1)}
set_processing_stage(db, submission, "grading", "正在按大题批改")
payload = build_ai_correction_payload(db, submission)
set_processing_stage(db, submission, "annotating", "正在生成卷面批注")
return _create_result_from_payload(db, submission, payload, media_ids_by_index)
```

Change `_create_result_from_payload` to accept `media_ids_by_index: dict[int, int] | None = None`, and persist:

```python
source_image_index = int(question.get("source_image_index") or 0)
source_media_id = (media_ids_by_index or {}).get(source_image_index)
annotations_json = json.dumps(question.get("annotations") or [], ensure_ascii=False)
```

Set the terminal stage when the result is created:

```python
submission.processing_stage = "needs_review" if result.needs_review else "corrected"
submission.processing_message = "等待家长确认" if result.needs_review else "批改完成"
```

- [ ] **Step 4: Update worker boundaries and failure state**

Before `create_correction`:

```python
set_processing_stage(db, submission, "recognizing", "正在识别题目")
```

Inside `mark_correction_failed`:

```python
submission.processing_stage = "failed"
submission.processing_message = error_message
```

- [ ] **Step 5: Run focused and worker tests**

Run: `pytest backend/tests/test_correction_annotations.py backend/tests/test_ai_services.py -q`

Expected: all tests PASS.

- [ ] **Step 6: Commit stage and persistence work**

```bash
git add backend/app/services/correction_service.py backend/app/worker/tasks/correct_homework.py backend/tests/test_correction_annotations.py backend/tests/test_ai_services.py
git commit -m "保存卷面标注与批改阶段"
```

---

### Task 5: 按页结果接口、原图下载与访问控制

**Files:**
- Create: `backend/app/services/access_service.py`
- Create: `backend/app/services/result_page_service.py`
- Modify: `backend/app/api/routers/results.py`
- Modify: `backend/app/api/routers/submissions.py`
- Modify: `backend/tests/test_v1_flow.py`

**Interfaces:**
- Produces: `can_access_student(db: Session, user: User, student: Student) -> bool`
- Produces: `build_result_pages(db: Session, submission: Submission, questions: list[QuestionResult]) -> list[dict]`
- Produces: authenticated `GET /api/v1/submissions/media/{media_id}/content`
- Extends: `GET /api/v1/results/tasks/{task_id}` with `submission.processing_*` and `pages`

- [ ] **Step 1: Write a failing two-page API test**

```python
def test_teacher_style_pages_are_ordered_and_protected(tmp_path):
    owner = unwrap(client.post("/api/v1/auth/wechat-login", json={"code": f"page-owner-{uuid4().hex}", "role": "parent"}))
    owner_headers = {"Authorization": f"Bearer {owner['token']}"}
    owner_context = unwrap(client.get("/api/v1/auth/me", headers=owner_headers))
    student_id = owner_context["students"][0]["id"]
    other = unwrap(client.post("/api/v1/auth/wechat-login", json={"code": f"page-other-{uuid4().hex}", "role": "parent"}))
    other_parent_headers = {"Authorization": f"Bearer {other['token']}"}
    first_file = tmp_path / "page-one.jpg"
    second_file = tmp_path / "page-two.jpg"
    first_file.write_bytes(b"page-one")
    second_file.write_bytes(b"page-two")

    with SessionLocal() as db:
        plan = AssignmentBatch(student_id=student_id, title="两页卷面", status="active")
        db.add(plan)
        db.flush()
        item = AssignmentItem(assignment_batch_id=plan.id, subject="语文", title="练习册")
        db.add(item)
        db.flush()
        task = DailyTask(
            student_id=student_id,
            assignment_batch_id=plan.id,
            assignment_item_id=item.id,
            task_date=date.today(),
            subject="语文",
            title="两页练习册",
            status="corrected",
        )
        db.add(task)
        db.flush()
        submission = Submission(
            daily_task_id=task.id,
            student_id=student_id,
            submission_type="photo",
            status="corrected",
            processing_stage="corrected",
            processing_message="批改完成",
        )
        db.add(submission)
        db.flush()
        page_with_sort_20 = SubmissionMedia(
            submission_id=submission.id,
            media_type="image",
            purpose="homework",
            file_url=str(second_file),
            storage_path=str(second_file),
            sort_order=20,
        )
        page_with_sort_10 = SubmissionMedia(
            submission_id=submission.id,
            media_type="image",
            purpose="homework",
            file_url=str(first_file),
            storage_path=str(first_file),
            sort_order=10,
        )
        db.add_all([page_with_sort_20, page_with_sort_10])
        db.flush()
        correction = CorrectionResult(
            submission_id=submission.id,
            daily_task_id=task.id,
            completion_score=88,
            accuracy_score=75,
            confidence_score=0.9,
            summary="两页批改完成",
        )
        db.add(correction)
        db.flush()
        db.add_all([
            QuestionResult(
                correction_result_id=correction.id,
                source_media_id=page_with_sort_10.id,
                question_no="1",
                is_correct=True,
                annotations_json='[{"kind":"correct_tick","x":0.8,"y":0.2,"width":0.1,"height":0.1,"text":null,"confidence":0.9}]',
            ),
            QuestionResult(
                correction_result_id=correction.id,
                source_media_id=page_with_sort_20.id,
                question_no="6",
                is_correct=False,
                annotations_json='[{"kind":"error_circle","x":0.2,"y":0.5,"width":0.3,"height":0.1,"text":null,"confidence":0.9}]',
            ),
        ])
        db.commit()
        task_id = task.id
        page_with_sort_10_id = page_with_sort_10.id
        page_with_sort_20_id = page_with_sort_20.id

    result = unwrap(client.get(f"/api/v1/results/tasks/{task_id}", headers=owner_headers))
    assert result["submission"]["processing_stage"] == "corrected"
    assert [page["media_id"] for page in result["pages"]] == [page_with_sort_10_id, page_with_sort_20_id]
    assert result["pages"][0]["page_number"] == 1
assert result["pages"][0]["questions"][0]["annotations"][0]["kind"] == "correct_tick"
assert result["pages"][1]["summary"] == {
    "correct_question_nos": [],
    "incorrect_question_nos": ["6"],
        "review_question_nos": [],
    }

    denied = client.get(f"/api/v1/results/tasks/{task_id}", headers=other_parent_headers)
    assert denied.status_code == 403

    allowed = client.get(f"/api/v1/submissions/media/{page_with_sort_10_id}/content", headers=owner_headers)
    assert allowed.status_code == 200
    assert allowed.content == b"page-one"

    denied_media = client.get(f"/api/v1/submissions/media/{page_with_sort_10_id}/content", headers=other_parent_headers)
    assert denied_media.status_code == 403
```

Extend the imports in `backend/tests/test_v1_flow.py` with `QuestionResult`.

- [ ] **Step 2: Run the focused flow test and verify failure**

Run: `pytest backend/tests/test_v1_flow.py -k "teacher_style_pages" -q`

Expected: FAIL because the page payload, download endpoint and authorization do not exist.

- [ ] **Step 3: Implement shared access control**

```python
from sqlalchemy.orm import Session

from backend.app.models import FamilyMember, Student, User


def can_access_student(db: Session, user: User, student: Student) -> bool:
    if student.user_id == user.id:
        return True
    return db.query(FamilyMember).filter(
        FamilyMember.user_id == user.id,
        FamilyMember.family_id == student.family_id,
        FamilyMember.status == "active",
    ).first() is not None
```

- [ ] **Step 4: Implement page payload construction**

```python
import json

from sqlalchemy.orm import Session

from backend.app.models import QuestionResult, Submission, SubmissionMedia


def _question_payload(question: QuestionResult) -> dict:
    try:
        annotations = json.loads(question.annotations_json or "[]")
    except json.JSONDecodeError:
        annotations = []
    return {
        "question_no": question.question_no,
        "is_correct": question.is_correct,
        "recognized_answer": question.recognized_answer,
        "expected_answer": question.expected_answer,
        "explanation": question.explanation,
        "confidence_score": question.confidence_score,
        "annotations": annotations if isinstance(annotations, list) else [],
    }


def build_result_pages(db: Session, submission: Submission, questions: list[QuestionResult]) -> list[dict]:
    media = db.query(SubmissionMedia).filter(
        SubmissionMedia.submission_id == submission.id,
        SubmissionMedia.purpose == "homework",
        SubmissionMedia.media_type == "image",
    ).order_by(SubmissionMedia.sort_order, SubmissionMedia.id).all()
    by_media: dict[int, list[QuestionResult]] = {}
    for question in questions:
        if question.source_media_id:
            by_media.setdefault(question.source_media_id, []).append(question)
    pages = []
    for page_number, item in enumerate(media, start=1):
        page_questions = by_media.get(item.id, [])
        pages.append({
            "media_id": item.id,
            "page_number": page_number,
            "image_url": f"/submissions/media/{item.id}/content",
            "summary": {
                "correct_question_nos": [q.question_no for q in page_questions if q.is_correct is True],
                "incorrect_question_nos": [q.question_no for q in page_questions if q.is_correct is False],
                "review_question_nos": [q.question_no for q in page_questions if q.is_correct is None],
            },
            "questions": [_question_payload(question) for question in page_questions],
        })
    return pages
```

- [ ] **Step 5: Secure and extend the result endpoint**

Add `user: User = Depends(get_current_user)` to `task_result`. After loading the task and student:

```python
if not task:
    raise HTTPException(status_code=404, detail="Task not found")
student = db.get(Student, task.student_id)
if not student or not can_access_student(db, user, student):
    raise HTTPException(status_code=403, detail="Task does not belong to current user")
```

Add processing fields and pages to the response:

```python
"processing_stage": submission.processing_stage,
"processing_message": submission.processing_message,
```

```python
"pages": build_result_pages(db, submission, questions) if submission else [],
```

Keep the top-level `questions` field during this task for backward compatibility; remove its UI use in Tasks 7 and 8.

- [ ] **Step 6: Add an authenticated media content endpoint**

```python
from fastapi.responses import FileResponse, RedirectResponse


@router.get("/media/{media_id}/content")
def media_content(
    media_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    media = db.get(SubmissionMedia, media_id)
    submission = db.get(Submission, media.submission_id) if media else None
    student = db.get(Student, submission.student_id) if submission else None
    if not media or not submission or not student:
        raise HTTPException(status_code=404, detail="Submission media not found")
    if not can_access_student(db, user, student):
        raise HTTPException(status_code=403, detail="Submission media does not belong to current user")
    signed_url = signed_download_url(media.file_url)
    if signed_url.startswith("http"):
        return RedirectResponse(signed_url)
    local_path = local_path_for_submission_media(media)
    return FileResponse(local_path)
```

- [ ] **Step 7: Run API and full backend tests**

Run: `pytest backend/tests/test_v1_flow.py -k "teacher_style_pages or parent_can_confirm" -q`

Expected: focused tests PASS.

Run: `pytest backend/tests -q`

Expected: all backend tests PASS.

- [ ] **Step 8: Commit the API slice**

```bash
git add backend/app/services/access_service.py backend/app/services/result_page_service.py backend/app/api/routers/results.py backend/app/api/routers/submissions.py backend/tests/test_v1_flow.py
git commit -m "返回受保护的整页批改结果"
```

---

### Task 6: 小程序状态文案与标注样式工具

**Files:**
- Modify: `miniapp/utils/result-state.js`
- Modify: `miniapp/utils/task-status.js`
- Create: `miniapp/utils/annotation-style.js`
- Modify: `miniapp/tests/result-state.test.js`
- Modify: `miniapp/tests/task-status.test.js`
- Create: `miniapp/tests/annotation-style.test.js`

**Interfaces:**
- Produces: `resultViewState(payload, timedOut) -> {kind,title,message,shouldPoll,stageIndex}`
- Produces: `taskStatusLabel(status, processingStage) -> string`
- Produces: `annotationStyle(annotation) -> string`

- [ ] **Step 1: Write failing utility tests**

```javascript
test('maps real processing stages to progress copy', () => {
  const recognizing = resultViewState({ submission: { status: 'processing', processing_stage: 'recognizing' } })
  assert.equal(recognizing.title, '正在识别题目')
  assert.equal(recognizing.stageIndex, 2)
  assert.equal(recognizing.shouldPoll, true)

  const annotating = resultViewState({ submission: { status: 'processing', processing_stage: 'annotating' } })
  assert.equal(annotating.title, '正在生成卷面批注')
  assert.equal(annotating.stageIndex, 4)
})
```

```javascript
test('uses processing stage on task cards', () => {
  assert.equal(taskStatusLabel('correcting', 'recognizing'), '识别中')
  assert.equal(taskStatusLabel('correcting', 'grading'), '批改中')
  assert.equal(taskStatusLabel('correcting', 'annotating'), '生成批注中')
})
```

```javascript
const { annotationStyle } = require('../utils/annotation-style')

test('converts normalized geometry to bounded percentages', () => {
  assert.equal(
    annotationStyle({ x: 0.2, y: 0.3, width: 0.4, height: 0.1 }),
    'left:20%;top:30%;width:40%;height:10%;'
  )
  assert.equal(
    annotationStyle({ x: -1, y: 2, width: 4, height: 0 }),
    'left:0%;top:100%;width:100%;height:0%;'
  )
})
```

- [ ] **Step 2: Run utility tests and verify failure**

Run: `node --test miniapp/tests/result-state.test.js miniapp/tests/task-status.test.js miniapp/tests/annotation-style.test.js`

Expected: FAIL on missing stage mappings and missing module.

- [ ] **Step 3: Implement stage mappings**

In `result-state.js`, define:

```javascript
const PROCESSING_STAGES = {
  uploaded: { title: '上传完成', message: '作业照片已安全提交。', stageIndex: 1 },
  recognizing: { title: '正在识别题目', message: '正在读取作业页和大题编号。', stageIndex: 2 },
  grading: { title: '正在逐题批改', message: '系统正在按大题检查答案。', stageIndex: 3 },
  annotating: { title: '正在生成卷面批注', message: '正在把勾、红圈和批语放回原图。', stageIndex: 4 }
}
```

For non-terminal submissions, return the matching copy, `kind: 'processing'`, `shouldPoll: !timedOut`, and its `stageIndex`. Preserve existing failed, resubmit, review and corrected handling.

In `task-status.js`, change the signature and add:

```javascript
const PROCESSING_STAGE_LABELS = {
  recognizing: '识别中',
  grading: '批改中',
  annotating: '生成批注中'
}

function taskStatusLabel(status, processingStage) {
  if (PROCESSING_STAGE_LABELS[processingStage]) return PROCESSING_STAGE_LABELS[processingStage]
  return STATUS_LABELS[status] || '待学习'
}
```

Update `miniapp/utils/task-groups.js` to call `taskStatusLabel(task.status, task.processing_stage)`.

- [ ] **Step 4: Implement safe percentage styling**

```javascript
function clamp(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return 0
  return Math.max(0, Math.min(1, number))
}

function percentage(value) {
  return `${Number((clamp(value) * 100).toFixed(2))}%`
}

function annotationStyle(annotation) {
  return `left:${percentage(annotation.x)};top:${percentage(annotation.y)};width:${percentage(annotation.width)};height:${percentage(annotation.height)};`
}

module.exports = { annotationStyle }
```

- [ ] **Step 5: Run all miniapp utility tests**

Run: `node --test miniapp/tests/*.test.js`

Expected: all tests PASS.

- [ ] **Step 6: Commit utility changes**

```bash
git add miniapp/utils/result-state.js miniapp/utils/task-status.js miniapp/utils/task-groups.js miniapp/utils/annotation-style.js miniapp/tests/result-state.test.js miniapp/tests/task-status.test.js miniapp/tests/annotation-style.test.js
git commit -m "增加批改阶段与标注样式工具"
```

---

### Task 7: 整页批改组件与学生结果页

**Files:**
- Create: `miniapp/services/correction-media.js`
- Create: `miniapp/components/annotated-homework-page/index.js`
- Create: `miniapp/components/annotated-homework-page/index.json`
- Create: `miniapp/components/annotated-homework-page/index.wxml`
- Create: `miniapp/components/annotated-homework-page/index.wxss`
- Modify: `miniapp/pages/student/result-detail/index.js`
- Modify: `miniapp/pages/student/result-detail/index.json`
- Modify: `miniapp/pages/student/result-detail/index.wxml`
- Modify: `miniapp/pages/student/result-detail/index.wxss`
- Create: `miniapp/tests/result-page-layout.test.js`

**Interfaces:**
- Consumes: result API `pages[]`
- Consumes: `annotationStyle(annotation)`
- Produces: `<annotated-homework-page page="{{item}}" />`
- Produces: downloaded page field `localImageUrl`

- [ ] **Step 1: Write a failing static layout contract test**

```javascript
const fs = require('node:fs')
const path = require('node:path')

test('student result uses full annotated pages instead of question cards', () => {
  const root = path.join(__dirname, '..')
  const pageWxml = fs.readFileSync(path.join(root, 'pages/student/result-detail/index.wxml'), 'utf8')
  const pageJson = JSON.parse(fs.readFileSync(path.join(root, 'pages/student/result-detail/index.json'), 'utf8'))
  const componentWxml = fs.readFileSync(path.join(root, 'components/annotated-homework-page/index.wxml'), 'utf8')

  assert.match(pageWxml, /wx:for="{{result.pages}}"/)
  assert.match(pageWxml, /annotated-homework-page/)
  assert.doesNotMatch(pageWxml, /wx:for="{{result.questions}}"/)
  assert.equal(pageJson.usingComponents['annotated-homework-page'], '/components/annotated-homework-page/index')
  assert.match(componentWxml, /annotation-error_circle/)
  assert.match(componentWxml, /annotation-correct_tick/)
})
```

- [ ] **Step 2: Run the layout test and verify failure**

Run: `node --test miniapp/tests/result-page-layout.test.js`

Expected: FAIL because the component and page loop do not exist.

- [ ] **Step 3: Add authenticated page download service**

```javascript
const { API_BASE_URL } = require('../utils/constants')

function absoluteUrl(url) {
  if (/^https?:\/\//.test(url || '')) return url
  return `${API_BASE_URL}${url}`
}

function downloadCorrectionPage(url) {
  const app = getApp()
  return new Promise((resolve, reject) => {
    wx.downloadFile({
      url: absoluteUrl(url),
      header: { Authorization: app.globalData.token ? `Bearer ${app.globalData.token}` : '' },
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) return resolve(res.tempFilePath)
        reject({ detail: '作业图片加载失败' })
      },
      fail(err) { reject({ detail: '作业图片加载失败', raw: err }) }
    })
  })
}

module.exports = { absoluteUrl, downloadCorrectionPage }
```

- [ ] **Step 4: Implement the reusable component**

`index.json`:

```json
{
  "component": true
}
```

`index.js`:

```javascript
const { annotationStyle } = require('../../utils/annotation-style')

Component({
  properties: {
    page: { type: Object, value: {} }
  },
  data: { imageLoaded: false },
  observers: {
    'page.questions': function (questions) {
      const annotations = []
      ;(questions || []).forEach((question) => {
        ;(question.annotations || []).forEach((annotation) => {
          annotations.push(Object.assign({}, annotation, { style: annotationStyle(annotation) }))
        })
      })
      const summary = this.data.page.summary || {}
      const correctNos = summary.correct_question_nos || []
      const incorrectNos = summary.incorrect_question_nos || []
      const reviewNos = summary.review_question_nos || []
      this.setData({
        annotations,
        correctText: correctNos.length ? `第 ${correctNos.join('、')} 题正确` : '',
        incorrectText: incorrectNos.length ? `第 ${incorrectNos.join('、')} 题错误` : '',
        reviewText: reviewNos.length ? `第 ${reviewNos.join('、')} 题待复核` : ''
      })
    }
  },
  methods: {
    onImageLoad() { this.setData({ imageLoaded: true }) },
    onImageError() { this.setData({ imageLoaded: false }); this.triggerEvent('imageretry', { mediaId: this.data.page.media_id }) }
  }
})
```

`index.wxml`:

```xml
<view class="homework-page">
  <view class="page-heading"><view>第 {{page.page_number}} / {{page.total_pages}} 页</view><view class="muted">完整批改卷面</view></view>
  <view class="image-stage">
    <image class="homework-image" src="{{page.localImageUrl}}" mode="widthFix" bindload="onImageLoad" binderror="onImageError" />
    <view wx:if="{{imageLoaded}}" class="annotation-layer">
      <view wx:for="{{annotations}}" wx:key="index" class="annotation annotation-{{item.kind}}" style="{{item.style}}">
        <text wx:if="{{item.kind === 'correct_tick'}}">✓</text>
        <text wx:elif="{{item.kind === 'error_cross'}}">×</text>
        <text wx:elif="{{item.kind === 'comment'}}">{{item.text}}</text>
      </view>
    </view>
  </view>
  <view class="page-summary">
    <text wx:if="{{correctText}}" class="summary-correct">{{correctText}}</text>
    <text wx:if="{{incorrectText}}" class="summary-error">{{incorrectText}}</text>
    <text wx:if="{{reviewText}}" class="summary-review">{{reviewText}}</text>
  </view>
</view>
```

`index.wxss` must define these exact behaviors:

```css
.homework-page { display: grid; gap: 16rpx; margin-bottom: 40rpx; }
.page-heading { display: flex; justify-content: space-between; align-items: center; }
.image-stage { position: relative; width: 100%; overflow: hidden; background: #edf2eb; }
.homework-image { display: block; width: 100%; }
.annotation-layer { position: absolute; inset: 0; pointer-events: none; }
.annotation { position: absolute; box-sizing: border-box; font-weight: 800; }
.annotation-error_circle { border: 5rpx solid #c83d36; border-radius: 50%; }
.annotation-error_cross { color: #c83d36; font-size: 58rpx; line-height: 1; }
.annotation-correct_tick { color: #2f7d45; font-size: 58rpx; line-height: 1; }
.annotation-comment { color: #c83d36; font-size: 24rpx; line-height: 1.25; }
.page-summary { display: flex; flex-wrap: wrap; gap: 10rpx 22rpx; font-size: 25rpx; }
.summary-correct { color: #2f7d45; }
.summary-error { color: #b9342e; }
.summary-review { color: #7b6b42; }
```

- [ ] **Step 5: Replace student result cards with progress and page list**

Register the component:

```json
{
  "navigationBarTitleText": "批改结果",
  "usingComponents": {
    "annotated-homework-page": "/components/annotated-homework-page/index"
  }
}
```

In `index.js`, add `onShow`/`onHide`, progressive polling, and page preparation:

```javascript
onLoad(options) {
  this.setData({ taskId: options.task_id })
},

onShow() {
  if (this.data.taskId) this.refresh()
},

onHide() { this.stopPolling() },
onUnload() { this.stopPolling() },

preparePages(result) {
  return Promise.all((result.pages || []).map((page) => {
    return downloadCorrectionPage(page.image_url)
      .then((localImageUrl) => Object.assign({}, page, { localImageUrl, total_pages: result.pages.length }))
      .catch(() => Object.assign({}, page, { localImageUrl: '', total_pages: result.pages.length, imageError: true }))
  })).then((pages) => Object.assign({}, result, { pages }))
},

schedulePoll() {
  this.stopPolling()
  const delay = this.data.pollCount < 10 ? 2000 : 5000
  this.pollTimer = setTimeout(() => this.refresh(), delay)
},

retryPageImage(e) {
  const mediaId = e.detail.mediaId
  const page = (this.data.result.pages || []).find((item) => item.media_id === mediaId)
  if (!page) return
  downloadCorrectionPage(page.image_url).then((localImageUrl) => {
    const pages = this.data.result.pages.map((item) => item.media_id === mediaId
      ? Object.assign({}, item, { localImageUrl, imageError: false })
      : item)
    this.setData({ 'result.pages': pages })
  }).catch(() => wx.showToast({ title: '作业图片加载失败', icon: 'none' }))
}
```

Remove the old `setInterval` creation from `onLoad`; this page must use only the one-shot timeout created by `schedulePoll`.

`refresh` must call `preparePages` only when `result.pages.length > 0`, preserve the previous result on request failure, and call `schedulePoll()` only while `viewState.shouldPoll` is true.

Replace the WXML question loop with:

```xml
<view wx:elif="{{viewState.kind === 'processing' || viewState.kind === 'empty'}}" class="card stack state-card">
  <view class="section-title">{{viewState.title}}</view>
  <view class="muted">{{viewState.message}}</view>
  <view class="progress-list">
    <view wx:for="{{['上传完成','识别题目','逐题批改','生成批注']}}" wx:key="*this" class="progress-step {{viewState.stageIndex >= index + 1 ? 'done' : ''}}">{{index + 1}}. {{item}}</view>
  </view>
  <button class="secondary-button" bindtap="backToday">先离开，稍后查看</button>
</view>

<view wx:else class="result-content">
  <view class="result-summary">
    <view class="section-title">{{result.task.title}}</view>
    <view class="muted">批改完成 · 共 {{result.pages.length}} 页</view>
    <view class="muted">{{result.result.summary}}</view>
  </view>
  <annotated-homework-page wx:for="{{result.pages}}" wx:key="media_id" page="{{item}}" bindimageretry="retryPageImage" />
</view>
```

- [ ] **Step 6: Run miniapp tests**

Run: `node --test miniapp/tests/*.test.js`

Expected: all tests PASS.

- [ ] **Step 7: Manually verify in WeChat DevTools**

Use a mocked or real result with two pages and confirm:

- Both full images render in `sort_order` sequence.
- Red circles and green ticks stay attached while resizing the simulator.
- The page has no per-question card loop.
- Leaving the progress page and returning resumes the server stage.

- [ ] **Step 8: Commit the student experience**

```bash
git add miniapp/services/correction-media.js miniapp/components/annotated-homework-page miniapp/pages/student/result-detail miniapp/tests/result-page-layout.test.js
git commit -m "实现学生整页卷面批改结果"
```

---

### Task 8: 家长复用卷面、任务状态与端到端回归

**Files:**
- Modify: `backend/app/services/task_payload_service.py`
- Modify: `backend/tests/test_v1_flow.py`
- Modify: `miniapp/pages/parent/task-result/index.js`
- Modify: `miniapp/pages/parent/task-result/index.json`
- Modify: `miniapp/pages/parent/task-result/index.wxml`
- Modify: `miniapp/pages/parent/task-result/index.wxss`
- Modify: `miniapp/tests/result-page-layout.test.js`

**Interfaces:**
- Extends: task payload with latest submission `processing_stage`
- Consumes: shared `<annotated-homework-page>` component
- Preserves: `confirmReview()` and `requestResubmit()`

- [ ] **Step 1: Write failing task-stage and parent-layout tests**

```python
def test_task_payload_processing_stage():
    login = unwrap(client.post("/api/v1/auth/wechat-login", json={
        "code": f"task-stage-{uuid4().hex}",
        "role": "parent",
    }))
    headers = {"Authorization": f"Bearer {login['token']}"}
    context = unwrap(client.get("/api/v1/auth/me", headers=headers))
    student_id = context["students"][0]["id"]
    with SessionLocal() as db:
        plan = AssignmentBatch(student_id=student_id, title="阶段显示", status="active")
        db.add(plan)
        db.flush()
        item = AssignmentItem(assignment_batch_id=plan.id, subject="数学", title="阶段显示")
        db.add(item)
        db.flush()
        task = DailyTask(
            student_id=student_id,
            assignment_batch_id=plan.id,
            assignment_item_id=item.id,
            task_date=date.today(),
            subject="数学",
            title="阶段显示",
            status="correcting",
        )
        db.add(task)
        db.flush()
        db.add(Submission(
            daily_task_id=task.id,
            student_id=student_id,
            submission_type="photo",
            status="processing",
            processing_stage="annotating",
            processing_message="正在生成卷面批注",
        ))
        db.commit()
        task_id = task.id

    task_payload_result = unwrap(client.get(f"/api/v1/tasks/{task_id}", headers=headers))
    assert task_payload_result["processing_stage"] == "annotating"
```

Extend `result-page-layout.test.js`:

```javascript
test('parent result reuses full pages and keeps review actions', () => {
  const root = path.join(__dirname, '..')
  const wxml = fs.readFileSync(path.join(root, 'pages/parent/task-result/index.wxml'), 'utf8')
  const config = JSON.parse(fs.readFileSync(path.join(root, 'pages/parent/task-result/index.json'), 'utf8'))

  assert.match(wxml, /annotated-homework-page/)
  assert.match(wxml, /confirmReview/)
  assert.match(wxml, /requestResubmit/)
  assert.doesNotMatch(wxml, /wx:for="{{result.questions}}"/)
  assert.equal(config.usingComponents['annotated-homework-page'], '/components/annotated-homework-page/index')
})
```

- [ ] **Step 2: Run focused tests and verify failure**

Run: `pytest backend/tests/test_v1_flow.py -k "task_payload_processing_stage" -q`

Run: `node --test miniapp/tests/result-page-layout.test.js`

Expected: both focused tests FAIL.

- [ ] **Step 3: Expose the latest processing stage on task cards**

In `task_payload`, load the latest submission and return its stage:

```python
submission = db.query(Submission).filter(
    Submission.daily_task_id == task.id,
).order_by(Submission.id.desc()).first()
```

```python
"processing_stage": submission.processing_stage if submission else None,
```

This lets the existing `task-groups.js` mapping from Task 6 show “识别中 / 批改中 / 生成批注中” on the today page.

- [ ] **Step 4: Reuse the annotated component on the parent page**

Register the component using the same JSON entry as the student page. Import `downloadCorrectionPage`, prepare `result.pages` after `reportApi.result`, and replace the current `result.questions` loop with:

```xml
<view class="annotated-pages">
  <annotated-homework-page wx:for="{{result.pages}}" wx:key="media_id" page="{{item}}" />
</view>
```

Keep the existing `review-banner`, `confirmReview`, `requestResubmit`, source-file preview, score summary and error state. Place the review action block after the overall summary and before the annotated pages.

- [ ] **Step 5: Run all automated tests**

Run: `pytest backend/tests -q`

Expected: all backend tests PASS.

Run: `node --test miniapp/tests/*.test.js`

Expected: all miniapp tests PASS.

- [ ] **Step 6: Run final manual acceptance in WeChat DevTools**

Use one two-photo submission containing eight large questions and at least two wrong answers. Verify all of the following:

1. Submission redirects to a real four-stage progress view.
2. “先离开，稍后查看” returns to the today page without cancelling work.
3. Today-page status changes as the server stage changes.
4. The completed student result shows exactly two complete pages in upload order.
5. Green ticks and red circles remain aligned at narrow and wide simulator sizes.
6. Each large question appears once; subquestion details are merged into its explanation.
7. A low-confidence annotation is absent while its neutral review summary remains.
8. The parent sees the same two pages and can still confirm or request resubmission.
9. A different family cannot load either the result payload or page image.
10. Existing video-task text results still load without an annotated page.

- [ ] **Step 7: Commit the integrated experience**

```bash
git add backend/app/services/task_payload_service.py backend/tests/test_v1_flow.py miniapp/pages/parent/task-result miniapp/tests/result-page-layout.test.js
git commit -m "完成家长卷面复核与状态提醒"
```

---

## Final Verification

- [ ] Run: `git diff --check`

Expected: no output.

- [ ] Run: `pytest backend/tests -q`

Expected: all backend tests PASS.

- [ ] Run: `node --test miniapp/tests/*.test.js`

Expected: all miniapp tests PASS.

- [ ] Open `miniapp/` in WeChat DevTools and repeat the two-page manual acceptance checklist from Task 8.

- [ ] Confirm `git status --short` contains no feature files outside the planned commits and still preserves the user's unrelated `miniapp/utils/constants.js` change.
