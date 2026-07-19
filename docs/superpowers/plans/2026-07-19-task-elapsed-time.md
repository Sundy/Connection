# Task Elapsed Time Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reliably record the server-side elapsed time from a student's start action to successful homework submission, recover unfinished sessions after restart, and show the result to parents as `本次任务耗时`.

**Architecture:** Keep `StudySession` as the server-side source of truth and add a read endpoint for the unfinished session of a task. The mini-program restores that session on the timer and upload pages, while submission completion writes one shared completion timestamp to both the submission and linked session. Parent formatting remains client-side and uses the correction result's existing `study_duration_seconds` field.

**Tech Stack:** FastAPI, SQLAlchemy, pytest, native WeChat mini-program JavaScript/WXML, Node.js test runner

## Global Constraints

- The metric is named `本次任务耗时`, not focused or active study time.
- Server timestamps are authoritative; the mini-program timer is display-only.
- Pause and resume are removed from the student UI and are not part of elapsed-time calculation.
- A task has at most one unfinished study session reused by repeated starts.
- Submission without a linked session remains valid and displays `未记录`.
- No database migration, new table, dependency, or environment variable is added.

---

### Task 1: Backend Active Session Recovery

**Files:**
- Create: `backend/tests/test_study_elapsed_time.py`
- Modify: `backend/app/services/study_service.py`
- Modify: `backend/app/api/routers/study_sessions.py`

**Interfaces:**
- Produces: `get_active_session(db: Session, task_id: int) -> StudySession | None`
- Produces: `elapsed_seconds(session: StudySession, at: datetime | None = None) -> int`
- Produces: `GET /api/v1/study-sessions/active?daily_task_id=<id>` returning a session payload or `null`
- Preserves: `POST /api/v1/study-sessions/start` response fields and adds `elapsed_seconds`

- [ ] **Step 1: Write the backend fixture and failing repeated-start test**

Create an isolated fixture that inserts a family, student, active assignment batch, assignment item, and two daily tasks. Track inserted IDs and delete `StudySession`, `SubmissionMedia`, `Submission`, `DailyTask`, `AssignmentItem`, `AssignmentBatch`, `Student`, and `Family` rows in foreign-key order.

Add this behavior test:

```python
def test_start_reuses_an_unfinished_legacy_paused_session(study_elapsed_fixture):
    task_id = study_elapsed_fixture["task_ids"][0]

    first = unwrap(client.post(
        "/api/v1/study-sessions/start",
        json={"daily_task_id": task_id},
    ))
    unwrap(client.post(f"/api/v1/study-sessions/{first['session_id']}/pause", json={}))
    second = unwrap(client.post(
        "/api/v1/study-sessions/start",
        json={"daily_task_id": task_id},
    ))

    assert second["session_id"] == first["session_id"]
    with SessionLocal() as db:
        assert db.query(StudySession).filter(
            StudySession.daily_task_id == task_id,
            StudySession.end_time.is_(None),
        ).count() == 1
```

- [ ] **Step 2: Write the failing active-session endpoint test**

Start a session, move its `start_time` two minutes into the past inside the fixture database, call the new endpoint, and assert the same ID and a server-calculated elapsed value:

```python
active = unwrap(client.get(
    f"/api/v1/study-sessions/active?daily_task_id={task_id}",
))
assert active["session_id"] == session_id
assert 119 <= active["elapsed_seconds"] <= 121
```

Also assert a task with no session returns JSON `data: null`.

- [ ] **Step 3: Run the focused tests and verify RED**

Run: `.venv/bin/python -m pytest backend/tests/test_study_elapsed_time.py -k "legacy_paused or active_session" -q`

Expected: FAIL because the active endpoint and `elapsed_seconds` payload do not exist, and the legacy paused session is not reused.

- [ ] **Step 4: Implement unfinished-session lookup and elapsed calculation**

In `study_service.py`, add focused helpers:

```python
def get_active_session(db: Session, task_id: int) -> StudySession | None:
    return db.query(StudySession).filter(
        StudySession.daily_task_id == task_id,
        StudySession.end_time.is_(None),
        StudySession.status.in_({"running", "paused"}),
    ).order_by(StudySession.id.desc()).first()


def elapsed_seconds(session: StudySession, at: datetime | None = None) -> int:
    end = session.end_time or at or datetime.now(UTC).replace(tzinfo=None)
    return max(int((end - session.start_time).total_seconds()), 0)
```

Update `start_session` to call `get_active_session` before inserting. In `study_sessions.py`, use one payload helper for start and active responses and add `GET /active` before `GET /{session_id}`-style routes so routing remains unambiguous.

- [ ] **Step 5: Run the focused tests and verify GREEN**

Run: `.venv/bin/python -m pytest backend/tests/test_study_elapsed_time.py -k "legacy_paused or active_session" -q`

Expected: PASS.

- [ ] **Step 6: Commit Task 1**

```bash
git add backend/tests/test_study_elapsed_time.py backend/app/services/study_service.py backend/app/api/routers/study_sessions.py
git commit -m "支持恢复任务计时"
```

### Task 2: Submission Link Validation and Authoritative Completion Time

**Files:**
- Modify: `backend/tests/test_study_elapsed_time.py`
- Modify: `backend/app/services/study_service.py`
- Modify: `backend/app/api/routers/submissions.py`

**Interfaces:**
- Changes: `finish_session(db: Session, session_id: int, finished_at: datetime | None = None) -> StudySession`
- Enforces: supplied session must be unfinished and match both `daily_task_id` and `student_id`
- Produces: successful completion sets `Submission.submitted_at` and the linked session's `end_time` to the same value

- [ ] **Step 1: Write failing association-validation tests**

Start a session for the fixture's first task, then try to create a submission for its second task using that session:

```python
response = client.post("/api/v1/submissions", json={
    "daily_task_id": second_task_id,
    "submission_type": "photo",
    "linked_study_session_id": first_session_id,
})
assert response.status_code == 422
assert "does not match" in response.json()["detail"]
```

Add equivalent coverage for a completed session and assert no `Submission` row is created in either case.

- [ ] **Step 2: Write the failing completion timestamp test**

Create a valid submission and one `SubmissionMedia` row directly in the fixture database. Patch `run_homework_correction.delay`, complete the submission, and assert:

```python
assert submission.submitted_at is not None
assert session.end_time == submission.submitted_at
assert session.duration_seconds == int(
    (submission.submitted_at - session.start_time).total_seconds()
)
```

Call completion a second time and assert `submitted_at`, `end_time`, and `duration_seconds` remain unchanged. Add a separate submission without `linked_study_session_id` and assert completion still returns HTTP 200.

- [ ] **Step 3: Run submission tests and verify RED**

Run: `.venv/bin/python -m pytest backend/tests/test_study_elapsed_time.py -k "submission" -q`

Expected: FAIL because session linkage is currently unchecked and `submitted_at` is not written.

- [ ] **Step 4: Implement minimal validation and shared completion timestamp**

In `create_submission`, load the supplied session and reject it with HTTP 422 unless all conditions hold:

```python
session is not None
session.end_time is None
session.daily_task_id == task.id
session.student_id == task.student_id
```

Extend `finish_session` with an optional `finished_at` argument and calculate duration through `elapsed_seconds`. In `complete`, create one UTC-naive `completed_at`, set `submission.submitted_at` only when absent, and pass that exact value to `finish_session`.

- [ ] **Step 5: Run all elapsed-time backend tests and verify GREEN**

Run: `.venv/bin/python -m pytest backend/tests/test_study_elapsed_time.py -q`

Expected: PASS.

- [ ] **Step 6: Commit Task 2**

```bash
git add backend/tests/test_study_elapsed_time.py backend/app/services/study_service.py backend/app/api/routers/submissions.py
git commit -m "校验任务计时关联"
```

### Task 3: Student Session Recovery and Pause-Free Timer UI

**Files:**
- Create: `miniapp/tests/study-elapsed-time.test.js`
- Modify: `miniapp/services/study.js`
- Modify: `miniapp/pages/student/focus-timer/index.js`
- Modify: `miniapp/pages/student/focus-timer/index.wxml`
- Modify: `miniapp/pages/student/upload-homework/index.js`

**Interfaces:**
- Produces: `studyApi.active(dailyTaskId) -> Promise<session | null>`
- Timer page consumes: `session.elapsed_seconds`
- Upload page guarantees: active-session lookup settles before the first submission is created

- [ ] **Step 1: Write failing service and layout tests**

Add a Node test that stubs the shared request module, reloads `services/study.js`, calls `active(42)`, and asserts a GET request to `/study-sessions/active?daily_task_id=42`.

Read the timer WXML and controller as text and assert:

```javascript
assert.match(controller, /studyApi\.active/)
assert.match(controller, /restoreActiveSession/)
assert.match(markup, /计时中/)
assert.doesNotMatch(markup, /bindtap="pause"/)
assert.doesNotMatch(markup, /bindtap="resume"/)
```

Read the upload controller and assert it calls `studyApi.active` and awaits a `sessionReady` promise before `submissionApi.create`.

- [ ] **Step 2: Run mini-program tests and verify RED**

Run: `node --test miniapp/tests/study-elapsed-time.test.js`

Expected: FAIL because `active`, restoration, and pause-free markup are absent.

- [ ] **Step 3: Add the active-session client and timer restoration**

Add to `services/study.js`:

```javascript
function active(dailyTaskId) {
  return request({ url: `/study-sessions/active?daily_task_id=${dailyTaskId}` })
}
```

On timer-page show, call `restoreActiveSession`. When a session is returned, set `sessionId`, `elapsed`, formatted display, `running: true`, and `statusText: '计时中'`, then restart the visual tick. Clear the interval on hide and unload. Keep `start` idempotent through the backend response and remove page-level `pause` and `resume` methods.

Replace the pause/resume button branch with a stable `计时中` state while preserving the upload button.

- [ ] **Step 4: Make upload wait for recovery**

In upload-page `onLoad`, assign an instance promise:

```javascript
this.sessionReady = options.session_id
  ? Promise.resolve(Number(options.session_id))
  : studyApi.active(Number(options.task_id)).then((session) => {
      const sessionId = session ? session.session_id : null
      this.setData({ sessionId })
      return sessionId
    })
```

Start `ensureSubmission` from `this.sessionReady`, and use the resolved ID in `linked_study_session_id`. On lookup failure, show a toast and resolve to `null` so submission remains available without inventing an association.

- [ ] **Step 5: Run the focused mini-program test and verify GREEN**

Run: `node --test miniapp/tests/study-elapsed-time.test.js`

Expected: PASS.

- [ ] **Step 6: Commit Task 3**

```bash
git add miniapp/tests/study-elapsed-time.test.js miniapp/services/study.js miniapp/pages/student/focus-timer/index.js miniapp/pages/student/focus-timer/index.wxml miniapp/pages/student/upload-homework/index.js
git commit -m "恢复学生任务计时"
```

### Task 4: Parent Task Elapsed-Time Display

**Files:**
- Create: `miniapp/utils/task-elapsed.js`
- Modify: `miniapp/tests/study-elapsed-time.test.js`
- Modify: `miniapp/pages/parent/task-result/index.js`
- Modify: `miniapp/pages/parent/task-result/index.wxml`

**Interfaces:**
- Produces: `formatTaskElapsed(seconds) -> string`
- Consumes: `result.result.study_duration_seconds`
- Displays: `本次任务耗时` and the formatted duration or `未记录`

- [ ] **Step 1: Write failing formatter and markup tests**

Add tests for exact formatting:

```javascript
assert.equal(formatTaskElapsed(null), '未记录')
assert.equal(formatTaskElapsed(0), '未记录')
assert.equal(formatTaskElapsed(8), '8 秒')
assert.equal(formatTaskElapsed(1518), '25 分 18 秒')
assert.equal(formatTaskElapsed(3918), '1 小时 5 分 18 秒')
```

Assert the parent controller imports `formatTaskElapsed`, derives `taskElapsedLabel`, and the WXML contains both `本次任务耗时` and `{{taskElapsedLabel}}`.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `node --test miniapp/tests/study-elapsed-time.test.js`

Expected: FAIL because the formatter and parent display do not exist.

- [ ] **Step 3: Implement formatter and parent binding**

Create `miniapp/utils/task-elapsed.js` with finite, non-negative integer normalization. Return `未记录` for missing or zero values; otherwise produce seconds, minutes plus seconds, or hours plus minutes plus seconds without empty units.

In `loadResult`, derive `taskElapsedLabel` from the prepared result before calling `setData`. Add one compact metadata row beneath the score grid in parent WXML:

```xml
<view class="list-row result-meta-row">
  <view class="muted">本次任务耗时</view>
  <view class="item-title">{{taskElapsedLabel}}</view>
</view>
```

- [ ] **Step 4: Run focused mini-program tests and verify GREEN**

Run: `node --test miniapp/tests/study-elapsed-time.test.js miniapp/tests/result-page-layout.test.js`

Expected: PASS.

- [ ] **Step 5: Commit Task 4**

```bash
git add miniapp/utils/task-elapsed.js miniapp/tests/study-elapsed-time.test.js miniapp/pages/parent/task-result/index.js miniapp/pages/parent/task-result/index.wxml
git commit -m "展示本次任务耗时"
```

### Task 5: Full Verification

**Files:**
- Verify all files modified by Tasks 1-4

- [ ] **Step 1: Run focused backend flow tests**

Run: `.venv/bin/python -m pytest backend/tests/test_study_elapsed_time.py backend/tests/test_notifications.py backend/tests/test_v1_flow.py -q -k "study or submission or notification or homework_v1_flow"`

Expected: all selected tests PASS.

- [ ] **Step 2: Run the complete mini-program test suite**

Run: `node --test miniapp/tests/*.test.js`

Expected: all tests PASS.

- [ ] **Step 3: Run the complete backend suite**

Run: `.venv/bin/python -m pytest backend/tests -q`

Expected: all tests PASS. If an unrelated pre-existing failure remains, record its exact test name and failure without claiming the full suite passes.

- [ ] **Step 4: Inspect the final diff**

Run: `git diff --check && git status --short && git diff --stat HEAD~4..HEAD`

Expected: no whitespace errors; only elapsed-time implementation, tests, specification, and plan files are present.
