# Content-Based Incremental Homework Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace temporary-file homework names with content-derived Chinese titles, append repeated imports into the matching date-range plan, support safe deletion of staged uploads/items, and enforce one-answer-to-one-homework content matching before confirmation.

**Architecture:** Each upload remains an `ImportFile` with a separate document role, structured content signature, recognition state, and optional answer-to-homework link. A staging `AssignmentBatch` is generated idempotently from one import batch; confirmation either activates it or transactionally merges only its new items/tasks into an existing active plan with the same student and date range. Import intelligence, answer matching, storage deletion, access control, and planning merge logic are separated into focused services and exposed through thin FastAPI routes.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, MySQL/PyMySQL, Qwen-compatible JSON chat completions, Celery, pytest, native WeChat mini-program WXML/WXSS/JavaScript, Node.js test runner, Aliyun OSS SDK.

## Global Constraints

- Work in the existing repository and preserve the user's unrelated files and data.
- MySQL `connection` is the only test/development database; use focused tests because pytest writes persistent fixture rows.
- Local `.env` uses `APP_ENV=development` and `DB_PROD_OUT`; production uses `APP_ENV=production` and `DATABASE_URL_PRODUCTION`.
- New homework titles must come from extracted content, never a temporary file name, UUID, timestamp, or extension.
- Content-derived titles are not manually editable.
- Repeated imports append; they must not delete or regenerate old active items, tasks, submissions, or corrections.
- Uploaded answers are optional, but every uploaded answer must match exactly one current-batch homework before confirmation.
- One answer cannot match multiple homework files, and one homework cannot receive multiple answers.
- Answers cannot be attached to already active or historically corrected homework.
- Only staged uploads and staged assignment items are deletable through this feature.
- Deleting a matched homework also deletes its paired staged answer; deleting an answer preserves the homework.
- Multi-day date ranges keep the existing scheduling algorithm; a one-day range schedules additions on that date.
- All import/upload/preview/parse/delete/generate/confirm operations enforce family access.
- Existing historical import records remain readable and are not batch-renamed or reprocessed.

---

## File Map

- `backend/app/models/__init__.py`: add import-role, recognition, signature, matching, and staging-target fields.
- `backend/app/core/database.py`: generalize the idempotent MySQL compatibility migration to the new nullable columns and unique index.
- `backend/app/core/config.py`: add exact content-title and answer-match confidence thresholds.
- `backend/app/services/import_content_service.py`: analyze extracted text and produce a safe Chinese title plus structured signature without consulting file names.
- `backend/app/services/llm_service.py`: request a JSON object for one import file's content analysis.
- `backend/app/services/answer_matching_service.py`: score and assign one-to-one answer/homework pairs inside one import batch.
- `backend/app/services/import_access_service.py`: centralize family access checks for import batches, files, and staging plans.
- `backend/app/services/import_file_service.py`: serialize upload state and delete staged database/storage artifacts safely.
- `backend/app/services/oss_service.py`: delete only validated OSS URLs owned by the configured bucket.
- `backend/app/worker/tasks/parse_files.py`: run extraction, content analysis, and batch answer re-matching.
- `backend/app/api/routers/imports.py`: accept document roles, return content display names/states, enforce access, parse only pending files, and expose staged file deletion.
- `backend/app/services/planning_service.py`: generate only missing staging items, validate confirmation, merge additions into an exact-range active plan, and delete staged items.
- `backend/app/api/routers/plans.py`: expose existing/new draft sections, blockers, deletion, and merged target plan ID with access checks.
- `backend/app/services/task_payload_service.py`: expose recognized content title as the source-file display name for newly imported homework.
- `miniapp/services/import.js`: send document roles and delete/reload staged files.
- `miniapp/services/plan.js`: delete staged assignment items.
- `miniapp/pages/parent/import-upload/index.js`: manage separate homework/answer uploads, restored file lists, recognition/matching polling, and confirmed deletion.
- `miniapp/pages/parent/import-upload/index.wxml`: show two upload sections and content-derived state cards.
- `miniapp/pages/parent/import-upload/index.wxss`: style role sections, status messages, and destructive actions.
- `miniapp/pages/parent/plan-confirm/index.js`: refresh draft data and delete only current additions.
- `miniapp/pages/parent/plan-confirm/index.wxml`: split existing items from current additions and show answer blockers.
- `miniapp/pages/parent/plan-confirm/index.wxss`: style read-only and staged sections.
- `backend/tests/test_import_content.py`: pure title/signature and LLM-boundary tests.
- `backend/tests/test_answer_matching.py`: pure scoring and database matching tests.
- `backend/tests/test_database_schema_upgrade.py`: fake-bind compatibility DDL and index tests.
- `backend/tests/test_v1_flow.py`: permission, upload/delete, matching blocker, incremental merge, and task-source contract integration tests.
- `miniapp/tests/import-plan-layout.test.js`: static upload/confirm layout contract tests.
- `miniapp/tests/import-upload-state.test.js`: upload-page state and API interaction tests.

---

### Task 1: Add Import Intelligence and Staging Schema

**Files:**
- Modify: `backend/app/models/__init__.py:64-140`
- Modify: `backend/app/core/database.py:18-65`
- Modify: `backend/app/core/config.py:8-55`
- Modify: `backend/tests/test_database_schema_upgrade.py`
- Create: `backend/tests/test_import_content.py`

**Interfaces:**
- Produces ORM fields `ImportFile.document_role`, `recognized_title`, `recognition_status`, `recognition_error`, `content_summary`, `content_signature_json`, `match_status`, `matched_homework_file_id`, `match_confidence`, and `match_reason`.
- Produces ORM field `AssignmentBatch.target_assignment_batch_id`.
- Produces settings `import_title_confidence_threshold: float = 0.75` and `answer_match_confidence_threshold: float = 0.80`.
- Produces `ensure_compatibility_schema(bind=engine) -> None`, replacing the question-only compatibility helper.

- [ ] **Step 1: Write failing model/config tests**

Add to `backend/tests/test_import_content.py`:

```python
from backend.app.core.config import Settings
from backend.app.models import AssignmentBatch, ImportFile


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
```

- [ ] **Step 2: Run the model/config test and verify RED**

Run: `.venv/bin/pytest backend/tests/test_import_content.py::test_import_intelligence_fields_and_thresholds_exist -q`

Expected: FAIL because the ORM fields and settings do not exist.

- [ ] **Step 3: Add the model fields and settings**

In `ImportFile`, add nullable compatibility fields with defaults for new rows:

```python
document_role: Mapped[str | None] = mapped_column(String(32), nullable=True, default="homework")
recognized_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
recognition_status: Mapped[str | None] = mapped_column(String(32), nullable=True, default="pending", index=True)
recognition_error: Mapped[str | None] = mapped_column(Text, nullable=True)
content_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
content_signature_json: Mapped[str | None] = mapped_column(Text, nullable=True)
match_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
matched_homework_file_id: Mapped[int | None] = mapped_column(
    ForeignKey("import_files.id"), nullable=True, unique=True
)
match_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
match_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
```

In `AssignmentBatch`, add:

```python
target_assignment_batch_id: Mapped[int | None] = mapped_column(
    ForeignKey("assignment_batches.id"), nullable=True, index=True
)
```

In `Settings`, add:

```python
import_title_confidence_threshold: float = 0.75
answer_match_confidence_threshold: float = 0.80
```

- [ ] **Step 4: Write failing fake-bind migration assertions**

Extend `backend/tests/test_database_schema_upgrade.py` so a fake inspector exposes existing tables/columns/indexes and assert the exact missing operations include:

```python
assert "ALTER TABLE import_files ADD COLUMN document_role VARCHAR(32) NULL" in executed
assert "ALTER TABLE import_files ADD COLUMN recognized_title VARCHAR(255) NULL" in executed
assert "ALTER TABLE import_files ADD COLUMN matched_homework_file_id INTEGER NULL" in executed
assert "ALTER TABLE assignment_batches ADD COLUMN target_assignment_batch_id INTEGER NULL" in executed
assert (
    "CREATE UNIQUE INDEX uq_import_files_matched_homework_file_id "
    "ON import_files (matched_homework_file_id)"
) in executed
```

Also assert a second invocation with all columns/indexes present executes nothing, and MySQL error code `1060` for a raced column plus `1061` for a raced index are tolerated.

- [ ] **Step 5: Run migration tests and verify RED**

Run: `.venv/bin/pytest backend/tests/test_database_schema_upgrade.py -q`

Expected: FAIL because only `question_results` hierarchy columns are migrated.

- [ ] **Step 6: Generalize the compatibility migration**

Replace the single table constant with fixed internal SQL definitions:

```python
COMPATIBILITY_COLUMNS = {
    "question_results": {
        "section_no": "VARCHAR(32) NULL",
        "subquestion_no": "VARCHAR(32) NULL",
    },
    "import_files": {
        "document_role": "VARCHAR(32) NULL",
        "recognized_title": "VARCHAR(255) NULL",
        "recognition_status": "VARCHAR(32) NULL",
        "recognition_error": "TEXT NULL",
        "content_summary": "TEXT NULL",
        "content_signature_json": "TEXT NULL",
        "match_status": "VARCHAR(32) NULL",
        "matched_homework_file_id": "INTEGER NULL",
        "match_confidence": "FLOAT NULL",
        "match_reason": "TEXT NULL",
    },
    "assignment_batches": {
        "target_assignment_batch_id": "INTEGER NULL",
    },
}

COMPATIBILITY_INDEXES = {
    "import_files": {
        "ix_import_files_recognition_status": (
            "CREATE INDEX ix_import_files_recognition_status "
            "ON import_files (recognition_status)"
        ),
        "uq_import_files_matched_homework_file_id": (
            "CREATE UNIQUE INDEX uq_import_files_matched_homework_file_id "
            "ON import_files (matched_homework_file_id)"
        ),
    },
    "assignment_batches": {
        "ix_assignment_batches_target_assignment_batch_id": (
            "CREATE INDEX ix_assignment_batches_target_assignment_batch_id "
            "ON assignment_batches (target_assignment_batch_id)"
        ),
    },
}

COMPATIBILITY_FOREIGN_KEYS = {
    "import_files": {
        "columns": ["matched_homework_file_id"],
        "referred_table": "import_files",
        "sql": (
            "ALTER TABLE import_files "
            "ADD CONSTRAINT fk_import_files_matched_homework_file_id "
            "FOREIGN KEY (matched_homework_file_id) REFERENCES import_files (id)"
        ),
    },
    "assignment_batches": {
        "columns": ["target_assignment_batch_id"],
        "referred_table": "assignment_batches",
        "sql": (
            "ALTER TABLE assignment_batches "
            "ADD CONSTRAINT fk_assignment_batches_target_assignment_batch_id "
            "FOREIGN KEY (target_assignment_batch_id) REFERENCES assignment_batches (id)"
        ),
    },
}
```

Implement `ensure_compatibility_schema()` by inspecting each existing table, applying only missing columns, indexes, and foreign-key column/table pairs. Catch only duplicate-column `1060`, duplicate-index `1061`, and duplicate-constraint `1826`; re-raise every other `OperationalError`. Call it after `Base.metadata.create_all()` in `init_db()`.

- [ ] **Step 7: Run schema/model tests and commit**

Run: `.venv/bin/pytest backend/tests/test_database_schema_upgrade.py backend/tests/test_import_content.py::test_import_intelligence_fields_and_thresholds_exist -q`

Expected: PASS.

```bash
git add backend/app/models/__init__.py backend/app/core/database.py backend/app/core/config.py backend/tests/test_database_schema_upgrade.py backend/tests/test_import_content.py
git commit -m "增加作业导入识别与匹配字段"
```

---

### Task 2: Generate Chinese Homework Titles from Content

**Files:**
- Create: `backend/app/services/import_content_service.py`
- Modify: `backend/app/services/llm_service.py`
- Modify: `backend/tests/test_import_content.py`

**Interfaces:**
- Produces `analyze_import_content(text: str, document_role: str) -> dict`.
- Produces `normalize_content_title(candidate: object, signature: dict) -> str | None`.
- Produces `analyze_import_file_with_llm(text: str, document_role: str) -> dict`.
- Analysis dictionary keys: `subject`, `grade_hint`, `material`, `chapter`, `exercise_type`, `question_start`, `question_end`, `question_count`, `keywords`, `is_answer`, `recommended_title`, `confidence_score`, and `content_summary`.

- [ ] **Step 1: Write failing title sanitation tests**

Add:

```python
from backend.app.services.import_content_service import (
    analyze_import_content,
    normalize_content_title,
)


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


def test_local_content_analysis_builds_chinese_title_without_file_name():
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
```

- [ ] **Step 2: Run title tests and verify RED**

Run: `.venv/bin/pytest backend/tests/test_import_content.py -q`

Expected: FAIL because the content service does not exist.

- [ ] **Step 3: Add a strict JSON-object LLM analyzer**

In `llm_service.py`, build a JSON Object request whose system prompt explicitly says:

```text
只根据正文分析，不参考文件名。输出 JSON 对象：subject, grade_hint, material,
chapter, exercise_type, question_start, question_end, question_count, keywords,
is_answer, recommended_title, confidence_score, content_summary。
recommended_title 必须是简洁中文语义名称，不得包含 tmp、UUID、扩展名或时间戳。
```

Use `response_format={"type": "json_object"}` and return `{}` on an unconfigured LLM; let HTTP/JSON errors propagate to the caller so the local analyzer can be used deliberately.

- [ ] **Step 4: Implement deterministic local analysis and title sanitation**

`import_content_service.py` must:

```python
TEMP_NAME_PATTERN = re.compile(
    r"(?:tmp[_-])?[0-9a-f]{16,}|\.(?:pdf|docx?|xlsx?|png|jpe?g)$",
    re.IGNORECASE,
)


def normalize_content_title(candidate: object, signature: dict) -> str | None:
    raw = TEMP_NAME_PATTERN.sub("", str(candidate or "")).strip(" _-.")
    if raw and not re.search(r"tmp|[0-9a-f]{16}", raw, re.IGNORECASE):
        return raw[:40]
    parts = [
        signature.get("subject"),
        signature.get("grade_hint"),
        signature.get("chapter"),
        signature.get("exercise_type"),
    ]
    fallback = "".join(str(part).strip() for part in parts if part)
    return fallback[:40] or None
```

`analyze_import_content()` first tries the LLM result, normalizes numeric/confidence/list fields, and falls back to regex/keyword extraction for subject, Chinese/Arabic unit number, grade, question range, answer markers, and up to eight meaningful keywords. A homework result succeeds only when the sanitized title exists and confidence meets `settings.import_title_confidence_threshold`; an answer succeeds when it has a usable signature and answer markers, even though it does not create its own homework title.

- [ ] **Step 5: Add LLM boundary test and run GREEN**

Mock `httpx.post` and assert the request contains JSON Object mode, does not pass a file name, and parses a Chinese title object. Run:

`.venv/bin/pytest backend/tests/test_import_content.py -q`

Expected: PASS.

- [ ] **Step 6: Commit content naming**

```bash
git add backend/app/services/import_content_service.py backend/app/services/llm_service.py backend/tests/test_import_content.py
git commit -m "根据作业内容生成中文名称"
```

---

### Task 3: Match One Answer to One Current-Batch Homework

**Files:**
- Create: `backend/app/services/answer_matching_service.py`
- Create: `backend/tests/test_answer_matching.py`

**Interfaces:**
- Produces `score_answer_match(homework_signature: dict, answer_signature: dict) -> tuple[float, str]`.
- Produces `match_batch_answers(db: Session, batch_id: int) -> list[ImportFile]`.
- Consumes `ImportFile.content_signature_json`, `document_role`, and recognition states from Tasks 1-2.

- [ ] **Step 1: Write failing pure scoring tests**

```python
from backend.app.services.answer_matching_service import score_answer_match


def test_matching_subject_chapter_and_question_range_scores_high():
    score, reason = score_answer_match(
        {
            "subject": "数学", "chapter": "第3单元",
            "question_start": 1, "question_end": 20,
            "question_count": 20, "keywords": ["小数", "加减法"],
        },
        {
            "subject": "数学", "chapter": "第3单元",
            "question_start": 1, "question_end": 20,
            "question_count": 20, "keywords": ["小数", "加减法"],
            "is_answer": True,
        },
    )

    assert score >= 0.8
    assert "题号范围一致" in reason


def test_different_question_ranges_do_not_match():
    score, reason = score_answer_match(
        {"subject": "数学", "question_start": 21, "question_end": 40},
        {"subject": "数学", "question_start": 1, "question_end": 20, "is_answer": True},
    )

    assert score < 0.8
    assert "题号范围不一致" in reason
```

- [ ] **Step 2: Run scoring tests and verify RED**

Run: `.venv/bin/pytest backend/tests/test_answer_matching.py -q`

Expected: FAIL because the matching service does not exist.

- [ ] **Step 3: Implement deterministic scoring**

Assign explicit weights totaling 1.0:

```python
MATCH_WEIGHTS = {
    "subject": 0.25,
    "grade_hint": 0.10,
    "chapter": 0.15,
    "question_range": 0.25,
    "question_count": 0.10,
    "keywords": 0.15,
}
```

Hard-cap the result below 0.8 when subjects conflict, non-overlapping question ranges conflict, or `answer_signature["is_answer"]` is not true. Return a Chinese reason assembled from matched and conflicting features.

- [ ] **Step 4: Write database matching tests**

Create a focused fixture with one import batch, two recognized homework files, and two recognized answer files. Assert:

```python
matched = match_batch_answers(db, batch.id)

assert matched_answer.match_status == "matched"
assert matched_answer.matched_homework_file_id == math_homework.id
assert unmatched_answer.match_status == "unmatched"
assert unmatched_answer.matched_homework_file_id is None
assert unmatched_answer.match_reason
```

Add cases for:

- an answer uploaded before any recognized homework remains `pending`;
- two answers competing for one homework leave only the highest score matched;
- an already matched homework is not assigned again;
- files outside the current batch are never candidates.

- [ ] **Step 5: Implement one-to-one assignment**

`match_batch_answers()` loads current-batch recognized files, clears only current-batch answer match fields, scores all answer/homework pairs, sorts candidates by descending score and then stable IDs, and assigns a pair only when:

- score is at least `settings.answer_match_confidence_threshold`;
- the homework has not been assigned;
- the best and second-best answer-specific scores differ by at least `0.10`, unless only one homework candidate exists.

Unassigned answers become `pending` if no recognized homework exists, otherwise `unmatched`. Commit once after all rows are updated; do not create `AssignmentItem` rows here.

- [ ] **Step 6: Run matching tests and commit**

Run: `.venv/bin/pytest backend/tests/test_answer_matching.py -q`

Expected: PASS.

```bash
git add backend/app/services/answer_matching_service.py backend/tests/test_answer_matching.py
git commit -m "实现作业答案一对一匹配"
```

---

### Task 4: Process, Secure, Serialize, and Delete Staged Files

**Files:**
- Create: `backend/app/services/import_access_service.py`
- Create: `backend/app/services/import_file_service.py`
- Modify: `backend/app/services/access_service.py`
- Modify: `backend/app/services/oss_service.py`
- Modify: `backend/app/worker/tasks/parse_files.py`
- Modify: `backend/app/api/routers/imports.py`
- Modify: `backend/tests/test_v1_flow.py`

**Interfaces:**
- Produces `require_import_batch_access(db, user, batch_id) -> ImportBatch`.
- Produces `import_file_payload(item: ImportFile, role_index: int, matched_homework_title: str | None = None) -> dict`.
- Produces `delete_staged_import_file(db, user, file_id: int) -> list[int]` returning all deleted file IDs.
- Produces `delete_oss_url(url: str, config=settings) -> None`.
- Consumes Tasks 2-3 analysis and matching services.

- [ ] **Step 1: Write failing upload-role and display-name API test**

In `test_v1_flow.py`, create a family batch and upload an image with:

```python
data={
    "file_type": "image",
    "document_role": "homework",
    "sort_order": "0",
}
```

Assert the response does not expose the temporary file name as its main label:

```python
assert uploaded["document_role"] == "homework"
assert uploaded["display_name"] == "正在识别第 1 份作业"
assert "tmp_" not in uploaded["display_name"]
assert uploaded["can_delete"] is True
```

Upload an answer with `document_role=answer` and assert “正在识别第 1 份答案”. Also assert an invalid role receives 422.

- [ ] **Step 2: Write failing access isolation tests**

Create two parent families and assert the second family receives 403 for upload, batch detail, file list, preview, parse, batch patch, and delete against the first family's batch/file. Also assert creating a batch for another family's student returns 403.

- [ ] **Step 3: Run API tests and verify RED**

Run:

```bash
.venv/bin/pytest \
  backend/tests/test_v1_flow.py::test_import_upload_roles_use_content_display_names \
  backend/tests/test_v1_flow.py::test_import_routes_enforce_family_access -q
```

Expected: FAIL because routes lack document roles and consistent access dependencies.

- [ ] **Step 4: Implement centralized import access and payloads**

`require_import_batch_access()` loads the batch and student, then calls existing `can_access_student()`; it raises a domain `ImportAccessError(status_code, detail)` that routers convert to `HTTPException`.

`import_file_payload()` computes display text without falling back to `file_name`:

```python
def import_file_display_name(
    item: ImportFile,
    role_index: int,
    matched_homework_title: str | None = None,
) -> str:
    if item.document_role == "homework":
        return item.recognized_title or (
            "作业内容无法识别"
            if item.recognition_status == "failed"
            else f"正在识别第 {role_index} 份作业"
        )
    if item.match_status == "matched" and item.matched_homework_file_id:
        return f"《{matched_homework_title}》答案"
    return "未匹配答案" if item.match_status == "unmatched" else f"正在识别第 {role_index} 份答案"
```

Resolve the matched homework title in the router/file service, pass it explicitly to `import_file_payload()`, and assert it is non-empty for a matched answer. Return raw `file_name` only under a secondary `original_file_name` field for preview metadata.

- [ ] **Step 5: Wire extraction, content analysis, and re-matching**

In `parse_import_file()`:

1. set both parse and recognition state to processing;
2. extract text with current OCR/document services;
3. call `analyze_import_content(extracted_text, item.document_role or "homework")`;
4. store title, signature JSON, summary, confidence-derived status/error;
5. set homework `match_status=not_required` and answer `match_status=pending`;
6. call `match_batch_answers(db, item.import_batch_id)`;
7. update batch status to `parsed` when no file remains pending/processing, even when blockers exist.

`POST /parse` dispatches only files whose parse/recognition state is pending or failed. `GET /{batch_id}` returns `can_generate` plus structured blockers but never silently converts failed recognition into success.

- [ ] **Step 6: Write failing storage deletion tests**

Mock OSS deletion and local files. Assert deleting an unmatched answer deletes only its ID; deleting a matched homework returns both homework and answer IDs; a failed OSS deletion returns 502/409, keeps database rows and cards, and an active/confirmed batch returns 409.

- [ ] **Step 7: Implement controlled OSS and staged deletion**

`delete_oss_url()` resolves the key only via `object_key_from_oss_url()`. If the URL is local or OSS is not configured, it returns without remote work. If configured and the URL is not owned by the configured bucket, raise `ValueError`. Call `bucket.delete_object(key)` and propagate SDK failures.

`delete_staged_import_file()`:

- verifies family access and batch status is not confirmed;
- resolves the exact `ImportFile` rows to delete;
- when deleting homework, includes its matched answer;
- removes dependent staging `DailyTask` rows, then `AssignmentItem` rows;
- performs remote/local storage deletion before database deletion;
- only commits DB deletion after every storage target succeeds;
- re-runs `match_batch_answers()` when deleting an answer leaves other answers to reconsider.

Expose `DELETE /import-batches/files/{file_id}` and return
`{"deleted_file_ids": [homework_file_id, paired_answer_file_id]}` when a matched
homework/answer pair is removed; answer-only deletion returns the one deleted ID.

- [ ] **Step 8: Run focused import API tests and commit**

Run:

```bash
.venv/bin/pytest \
  backend/tests/test_v1_flow.py::test_import_upload_roles_use_content_display_names \
  backend/tests/test_v1_flow.py::test_import_routes_enforce_family_access \
  backend/tests/test_v1_flow.py::test_staged_import_file_deletion_cascades_without_false_success -q
```

Expected: PASS.

```bash
git add backend/app/services/import_access_service.py backend/app/services/import_file_service.py backend/app/services/access_service.py backend/app/services/oss_service.py backend/app/worker/tasks/parse_files.py backend/app/api/routers/imports.py backend/tests/test_v1_flow.py
git commit -m "支持安全的作业答案上传与删除"
```

---

### Task 5: Generate and Confirm Incremental Plan Additions

**Files:**
- Modify: `backend/app/services/planning_service.py`
- Modify: `backend/app/api/routers/plans.py`
- Modify: `backend/app/services/task_payload_service.py`
- Modify: `backend/tests/test_v1_flow.py`

**Interfaces:**
- Produces `plan_confirmation_blockers(db, plan: AssignmentBatch) -> list[dict]`.
- Produces `find_active_merge_target(db, staging_plan: AssignmentBatch, lock: bool = False) -> AssignmentBatch | None`.
- Changes `confirm_plan(db: Session, plan_id: int, adjustments: list[dict] | None = None) -> AssignmentBatch` to return the final active target plan.
- Produces `delete_staged_assignment_item(db, user, plan_id: int, item_id: int) -> list[int]`.

- [ ] **Step 1: Write failing idempotent generation test**

Create one parsed/recognized homework file and call `/plans/from-import/{batch_id}/generate` twice. Assert both responses use the same staging plan and database counts remain one item and one set of scheduled preview tasks:

```python
assert second["assignment_batch_id"] == first["assignment_batch_id"]
assert item_count == 1
assert task_count == expected_task_count
assert item.title == "数学四年级下册第3单元练习"
assert "tmp_" not in item.title
```

Then add a second homework to the same unconfirmed batch, generate again, and assert the first item/task IDs still exist while only the second is added.

- [ ] **Step 2: Run generation test and verify RED**

Expected: FAIL because current generation deletes every staging item/task before rebuilding.

- [ ] **Step 3: Implement file-idempotent staging generation**

Remove wholesale deletes from `generate_plan_from_import()`. Load existing staging items keyed by `import_file_id`; create only missing recognized homework items. Set each title from `file.recognized_title`, copy a matched answer's `extracted_text` into `answer_text`, and call `create_daily_tasks()` only for a newly created item.

If an existing staging item has no answer and a newly matched current-batch answer appears, update only its `answer_text`; never regenerate its tasks. Raw-text-only imports retain the existing extraction path and are keyed by a single existing no-file staging item.

- [ ] **Step 4: Write failing confirmation blocker tests**

Assert `POST /plans/{id}/confirm` returns 409 with a structured reason when any current-batch file is processing, a homework has failed title recognition, or an answer is pending/unmatched. Assert a batch containing only recognized homework and no answer can confirm.

- [ ] **Step 5: Implement confirmation validation**

`plan_confirmation_blockers()` returns dictionaries with `code`, `file_id`, and `message`. Exact codes:

- `file_processing`
- `homework_title_unrecognized`
- `answer_pending`
- `answer_unmatched`
- `answer_match_conflict`

`confirm_plan()` raises `PlanConfirmationBlocked(blockers)` before changing any status. The router maps it to HTTP 409 with the blockers in `detail`.

- [ ] **Step 6: Write failing append-to-active-plan test**

Create and confirm a first exact-range plan with one item/task. Create a second import batch for the same student/period/start/end, generate one staging addition, and confirm it. Assert:

```python
assert confirmed["plan_id"] == first_active_plan_id
assert confirmed["status"] == "active"
assert old_item_still_exists
assert old_task_still_exists
assert new_item.assignment_batch_id == first_active_plan_id
assert new_task.assignment_batch_id == first_active_plan_id
assert staging_plan.status == "merged"
```

Record an old `Submission` and `CorrectionResult` before merging and assert both IDs/data remain unchanged afterward. Add a different date-range case and assert it activates a separate plan instead of merging.

- [ ] **Step 7: Implement transactional target re-resolution and merge**

`find_active_merge_target()` filters exact `student_id`, `period_type`, `start_date`, `end_date`, `status == "active"`, excludes the staging plan, orders by ID, and uses `with_for_update()` when `lock=True`.

At confirmation, re-run the locked query instead of trusting the stored target hint. If a target exists, update only staging items/tasks to the target ID, add staging estimated minutes to target, set staging status `merged`, and leave target historical rows untouched. If no target exists, activate staging normally. Return the final active plan so the miniapp stores/navigates to the canonical ID.

- [ ] **Step 8: Write and implement staged-item deletion test**

Test `DELETE /plans/{plan_id}/draft-items/{item_id}` removes only the selected new item, its staged tasks, homework file, and paired answer; it must reject an existing active item or a merged/active staging plan. Reuse `delete_staged_import_file()` so storage and relationship behavior has one implementation.

- [ ] **Step 9: Expose existing/new draft sections and recognized source names**

`GET /plans/{plan_id}/draft` returns:

```json
{
  "plan": {"id": 2, "target_assignment_batch_id": 1},
  "existing_items": [],
  "new_items": [{"id": 9, "title": "数学四年级下册第3单元练习", "answer_status": "matched", "can_delete": true}],
  "daily_preview": [],
  "confirmation_blockers": [],
  "can_confirm": true
}
```

When a target plan exists, populate `existing_items` from it and `new_items` only from staging. Update `source_file_payload()` to return `display_name=item.recognized_title or source.file_name`; new miniapp consumers use `display_name`, while `file_name` remains for old clients and download metadata.

- [ ] **Step 10: Run incremental planning tests and commit**

Run:

```bash
.venv/bin/pytest \
  backend/tests/test_v1_flow.py::test_import_generation_is_idempotent_and_appends_new_files \
  backend/tests/test_v1_flow.py::test_plan_confirmation_blocks_unready_import_files \
  backend/tests/test_v1_flow.py::test_same_range_confirmation_merges_without_touching_history \
  backend/tests/test_v1_flow.py::test_staged_assignment_item_can_be_deleted_before_confirmation -q
```

Expected: PASS.

```bash
git add backend/app/services/planning_service.py backend/app/api/routers/plans.py backend/app/services/task_payload_service.py backend/tests/test_v1_flow.py
git commit -m "按日期范围增量合并作业计划"
```

---

### Task 6: Build Separate Homework and Answer Upload UI

**Files:**
- Modify: `miniapp/services/import.js`
- Modify: `miniapp/pages/parent/import-upload/index.js`
- Modify: `miniapp/pages/parent/import-upload/index.wxml`
- Modify: `miniapp/pages/parent/import-upload/index.wxss`
- Modify: `miniapp/tests/import-plan-layout.test.js`
- Create: `miniapp/tests/import-upload-state.test.js`

**Interfaces:**
- `uploadFile(batchId, filePath, fileType, sortOrder, fileName, documentRole)` sends `document_role`.
- `deleteFile(fileId)` calls `DELETE /import-batches/files/{fileId}`.
- Page data includes `homeworkFiles`, `answerFiles`, `batch`, `loading`, and `progressText`.

- [ ] **Step 1: Write failing static UI contract tests**

In `import-plan-layout.test.js`, assert upload markup contains:

```javascript
assert.match(markup, /上传作业/)
assert.match(markup, /上传答案（可选）/)
assert.match(markup, /homeworkFiles/)
assert.match(markup, /answerFiles/)
assert.match(markup, /deleteFile/)
assert.doesNotMatch(markup, /item\.file_name \|\| item\.file_url/)
```

- [ ] **Step 2: Write failing page-state tests**

Stub `importApi.listFiles()` to return recognized homework, matched answer, and unmatched answer payloads. Load the page definition and assert `refreshFiles()` separates roles, preserves API `display_name`, and exposes unmatched `match_reason`.

Stub `wx.showModal` plus `deleteFile()` and assert deleting matched homework displays a paired-answer warning, calls the API only after confirmation, then reloads the list.

- [ ] **Step 3: Run Node tests and verify RED**

Run: `node --test miniapp/tests/import-plan-layout.test.js miniapp/tests/import-upload-state.test.js`

Expected: FAIL because the page has generic image/file buttons, in-memory-only file state, and no delete action.

- [ ] **Step 4: Implement role-aware API methods and page state**

Change `uploadFile()` form data to include `document_role`. Add:

```javascript
function deleteFile(fileId) {
  return request({ url: `/import-batches/files/${fileId}`, method: 'DELETE' })
}
```

On page load call `Promise.all([getBatch(batchId), listFiles(batchId)])`. Derive lists from `document_role`, always display `display_name`, and never render `original_file_name` as the card heading.

Use shared selection methods parameterized by role so both homework and answer sections support images and message files. After each upload and during parse polling, reload server state rather than only concatenating the local response.

- [ ] **Step 5: Implement delete and blocker behavior**

`onDeleteFile(e)` reads the file ID/role/match status. For a matched homework, modal content says its paired answer will also be deleted. After confirmed API success, call `refreshFiles()`; on failure retain cards and show the server error.

`generatePlan()` parses and polls. If the completed batch reports blockers, refresh files, show the first blocker, and do not call plan generation. If no blockers, generate and navigate as today.

- [ ] **Step 6: Update WXML/WXSS and run GREEN**

Render separate titled sections, role-specific add buttons, status chips, match reasons, and a small destructive “删除” action. Recognition failures use explicit red/error copy; processing uses neutral copy; matched answers name their homework.

Run: `node --test miniapp/tests/import-plan-layout.test.js miniapp/tests/import-upload-state.test.js miniapp/tests/request.test.js`

Expected: PASS.

- [ ] **Step 7: Commit upload UI**

```bash
git add miniapp/services/import.js miniapp/pages/parent/import-upload/index.js miniapp/pages/parent/import-upload/index.wxml miniapp/pages/parent/import-upload/index.wxss miniapp/tests/import-plan-layout.test.js miniapp/tests/import-upload-state.test.js
git commit -m "区分作业答案上传并支持删除"
```

---

### Task 7: Show Existing vs New Items and Delete Staged Additions

**Files:**
- Modify: `miniapp/services/plan.js`
- Modify: `miniapp/pages/parent/plan-confirm/index.js`
- Modify: `miniapp/pages/parent/plan-confirm/index.wxml`
- Modify: `miniapp/pages/parent/plan-confirm/index.wxss`
- Modify: `miniapp/tests/import-plan-layout.test.js`

**Interfaces:**
- `deleteDraftItem(planId, itemId)` calls `DELETE /plans/{planId}/draft-items/{itemId}`.
- Confirmation redirects using the server-returned final `plan_id`, not always the staging plan ID.

- [ ] **Step 1: Add failing layout and behavior assertions**

Assert WXML contains “已有作业”, “本次新增”, `existing_items`, `new_items`, `answer_status`, `confirmation_blockers`, and a delete binding only inside the new-items loop.

In a page test, stub draft data and assert `confirm()` does not call the API when `can_confirm` is false. Stub a merge response `{plan_id: 8, status: "active"}` for staging ID 12 and assert global/storage `currentPlanId` plus redirect use 8.

- [ ] **Step 2: Run layout tests and verify RED**

Run: `node --test miniapp/tests/import-plan-layout.test.js`

Expected: FAIL because the current page renders one undifferentiated `assignment_items` list.

- [ ] **Step 3: Implement service/page behavior**

Add:

```javascript
function deleteDraftItem(planId, itemId) {
  return request({
    url: `/plans/${planId}/draft-items/${itemId}`,
    method: 'DELETE'
  })
}
```

Add `loadDraft()`, `deleteNewItem(e)`, and confirmation blocker guards. `deleteNewItem()` shows a confirmation modal, calls the endpoint, then reloads. On confirmation success, use `data.plan_id` returned by the server as the canonical plan ID.

- [ ] **Step 4: Update WXML/WXSS**

Existing items are read-only cards. New items show content-derived title, source `display_name`, answer state (`已匹配标准答案` or `无标准答案`), schedule preview association, and delete action. Blockers appear above the disabled confirmation button with actionable Chinese messages.

- [ ] **Step 5: Run Node tests and commit**

Run: `node --test miniapp/tests/import-plan-layout.test.js miniapp/tests/import-upload-state.test.js`

Expected: PASS.

```bash
git add miniapp/services/plan.js miniapp/pages/parent/plan-confirm/index.js miniapp/pages/parent/plan-confirm/index.wxml miniapp/pages/parent/plan-confirm/index.wxss miniapp/tests/import-plan-layout.test.js
git commit -m "确认页区分已有作业与本次新增"
```

---

### Task 8: Migrate Production and Verify the End-to-End Contract

**Files:**
- Modify only if verification reveals a defect: files from Tasks 1-7.

**Interfaces:**
- Consumes the complete schema, analysis, matching, staged deletion, incremental planning, and miniapp contracts.
- Produces migrated nullable columns/index in MySQL `connection` without rewriting historical rows.

- [ ] **Step 1: Run all safe pure and static regressions**

Run:

```bash
.venv/bin/pytest backend/tests/test_import_content.py backend/tests/test_answer_matching.py backend/tests/test_database_schema_upgrade.py backend/tests/test_database_config.py -q
node --test miniapp/tests/import-plan-layout.test.js miniapp/tests/import-upload-state.test.js miniapp/tests/request.test.js
```

Expected: all selected tests pass.

- [ ] **Step 2: Run focused persistent integration tests**

Run:

```bash
.venv/bin/pytest \
  backend/tests/test_v1_flow.py::test_import_upload_roles_use_content_display_names \
  backend/tests/test_v1_flow.py::test_import_routes_enforce_family_access \
  backend/tests/test_v1_flow.py::test_staged_import_file_deletion_cascades_without_false_success \
  backend/tests/test_v1_flow.py::test_import_generation_is_idempotent_and_appends_new_files \
  backend/tests/test_v1_flow.py::test_plan_confirmation_blocks_unready_import_files \
  backend/tests/test_v1_flow.py::test_same_range_confirmation_merges_without_touching_history \
  backend/tests/test_v1_flow.py::test_staged_assignment_item_can_be_deleted_before_confirmation -q
```

Expected: all pass against `connection`. Do not run the entire backend suite because it writes broad persistent fixture data outside this feature's verification scope.

- [ ] **Step 3: Apply the idempotent schema upgrade**

Run:

```bash
.venv/bin/python -c 'from backend.app.core.database import init_db; init_db(); print("schema_upgrade=completed")'
```

Expected: `schema_upgrade=completed` using local `APP_ENV=development` and the external `DB_PROD_OUT` endpoint for database `connection`.

- [ ] **Step 4: Inspect production schema without printing credentials**

Use SQLAlchemy `inspect(engine)` and print only the new column names/types/nullability plus the unique index name. Expected columns:

```text
import_files.document_role
import_files.recognized_title
import_files.recognition_status
import_files.recognition_error
import_files.content_summary
import_files.content_signature_json
import_files.match_status
import_files.matched_homework_file_id
import_files.match_confidence
import_files.match_reason
assignment_batches.target_assignment_batch_id
```

Expected index: `uq_import_files_matched_homework_file_id`, unique true.

- [ ] **Step 5: Exercise the accepted scenario with isolated fixture data**

Create two different recognized math homework files and one matching answer inside a uniquely named test batch. Verify:

- no `tmp_` name appears in generated item/API titles;
- the answer matches only the correct homework;
- an intentionally wrong answer remains unmatched and blocks confirmation;
- after deleting the wrong answer, confirmation succeeds;
- a second batch for the same student/date range adds one item to the original active plan;
- old item/task IDs and any attached submission/correction IDs remain unchanged;
- deleting a staged item removes only that addition.

Clean up only the uniquely identified fixture rows through normal application deletion paths where safe; do not issue broad SQL deletes.

- [ ] **Step 6: Request code review and resolve findings**

Use `superpowers:requesting-code-review` for the full implementation range. Fix every Critical and Important issue with a new RED/GREEN test, then ask for a concise re-review of the fix range.

- [ ] **Step 7: Run fresh final verification**

Re-run Step 1, Step 2, database ping/schema inspection, `git diff --check`, and `git status --short` after the final commit.

Expected: tests have zero failures; database ping is 1; every new column/index exists; worktree is clean.

- [ ] **Step 8: Report exact operational behavior**

State the final commit, schema changes, test counts, and these user-visible guarantees:

- content-derived Chinese names replace temporary names;
- repeated same-range uploads append;
- staged uploads/items are deletable under the agreed cascade rules;
- answers are optional but uploaded answers must match exactly one current-batch homework;
- unmatched answers block confirmation;
- historical active work, submissions, and correction results are not rewritten or deleted.
