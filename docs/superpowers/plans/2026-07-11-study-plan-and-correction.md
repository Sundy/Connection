# 学习计划与真实批改 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让家长和学生按日期、科目使用学习计划，并用可失败、可复核的真实 AI 流程批改照片和视频作业。

**Architecture:** 保留现有计划和提交表，扩展任务查询、提交状态和结果契约。小程序在客户端对扁平任务做确定性分组；服务端通过单一批改入口分派照片、朗读视频和书写视频，所有失败与复核状态均落库。

**Tech Stack:** FastAPI、SQLAlchemy、Pydantic、Celery、pytest、原生微信小程序 JavaScript/WXML/WXSS、Qwen 兼容多模态与 ASR API、FFmpeg。

## Global Constraints

- 不修改现有 AI 密钥、模型和服务商配置。
- 保留历史 `answer_text` 和 `purpose=answer` 数据，但新学生提交不得继续写入。
- 保留 `/tasks/today`、计划日历和结果接口的已有字段，新增字段必须向后兼容。
- 不得使用固定分数、固定题号或固定答案作为 AI 失败回退。
- 不覆盖工作区中的无关修改。
- Python 使用四空格和 snake_case；小程序 JavaScript/JSON 使用两空格和 camelCase。

---

## File Map

- `backend/app/api/routers/tasks.py`: 指定日期任务与科目汇总。
- `backend/app/api/routers/plans.py`: 计划日期范围和汇总。
- `backend/app/api/routers/submissions.py`: 新提交契约、媒体校验和批改调度。
- `backend/app/api/routers/results.py`: 最新提交关联结果和状态响应。
- `backend/app/models/__init__.py`: 提交失败字段。
- `backend/app/core/database.py`: SQLite 兼容式新增列。
- `backend/app/services/correction_service.py`: 结果校验、状态落库和真实批改入口。
- `backend/app/services/correction_ai_service.py`: 照片与视频多模态请求构造。
- `backend/app/services/media_processing_service.py`: 视频抽帧。
- `backend/app/worker/tasks/correct_homework.py`: 幂等执行与失败落库。
- `backend/tests/test_v1_flow.py`: API 回归测试。
- `backend/tests/test_ai_services.py`: AI 分支与状态测试。
- `miniapp/utils/date.js`: 日期计算和语义标签。
- `miniapp/utils/task-groups.js`: 科目分组与进度。
- `miniapp/services/task.js`: `target_date` 查询。
- `miniapp/pages/student/today/index.*`: 日期切换、选择器、科目分组。
- `miniapp/pages/parent/plan-calendar/index.*`: 日期与科目分组。
- `miniapp/pages/student/upload-homework/index.*`: 仅提交作业媒体。
- `miniapp/pages/student/result-detail/index.*`: 四态结果、超时和重试。
- `miniapp/pages/parent/task-result/index.*`: 家长复核和逐题详情。

---

### Task 1: 指定日期查询与科目汇总

**Files:**
- Modify: `backend/app/api/routers/tasks.py`
- Modify: `backend/app/api/routers/plans.py`
- Test: `backend/tests/test_v1_flow.py`

**Interfaces:**
- Produces: `GET /tasks/today?student_id=<id>&target_date=YYYY-MM-DD` 返回 `date`、`summary`、`subject_summary`、`tasks`。
- Produces: `GET /plans/{id}/calendar` 返回原有 `items`，并增加 `plan`、`date_summary`。

- [ ] **Step 1: 写指定日期与科目汇总失败测试**

在 `backend/tests/test_v1_flow.py` 创建两天、两个科目的任务，并断言：

```python
payload = unwrap(client.get(
    f"/api/v1/tasks/today?student_id={student_id}&target_date={tomorrow.isoformat()}",
    headers=headers,
))
assert payload["date"] == tomorrow.isoformat()
assert payload["subject_summary"] == [
    {"subject": "数学", "total_tasks": 2, "completed_tasks": 1},
    {"subject": "英语", "total_tasks": 1, "completed_tasks": 0},
]
```

另断言日历响应保留 `items`，并返回计划起止日期以及同样的逐日、逐科汇总。

- [ ] **Step 2: 运行测试确认 RED**

Run: `pytest backend/tests/test_v1_flow.py -k "target_date_subject_summary or calendar_date_summary" -q`

Expected: FAIL，缺少 `subject_summary`、`plan` 或 `date_summary`。

- [ ] **Step 3: 实现共用汇总函数与兼容响应**

在 `tasks.py` 增加纯函数：

```python
def subject_summary(tasks: list[DailyTask]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for task in tasks:
        row = grouped.setdefault(task.subject, {
            "subject": task.subject,
            "total_tasks": 0,
            "completed_tasks": 0,
        })
        row["total_tasks"] += 1
        row["completed_tasks"] += int(task.status in {"corrected", "needs_review"})
    return list(grouped.values())
```

确保 `date` 经 FastAPI JSON 编码后为 ISO 日期；日历汇总复用相同完成状态规则，原有 `items` 不变。

- [ ] **Step 4: 运行聚焦和完整 API 测试**

Run: `pytest backend/tests/test_v1_flow.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/app/api/routers/tasks.py backend/app/api/routers/plans.py backend/tests/test_v1_flow.py
git commit -m "支持按日期和科目查看计划"
```

---

### Task 2: 学生端日期切换与科目分组

**Files:**
- Create: `miniapp/utils/date.js`
- Create: `miniapp/utils/task-groups.js`
- Modify: `miniapp/services/task.js`
- Modify: `miniapp/pages/student/today/index.js`
- Modify: `miniapp/pages/student/today/index.wxml`
- Modify: `miniapp/pages/student/today/index.wxss`
- Test: `miniapp/tests/date.test.js`
- Test: `miniapp/tests/task-groups.test.js`

**Interfaces:**
- Produces: `taskApi.today(studentId, targetDate)`。
- Produces: `shiftDate(isoDate, delta)`、`dateLabel(isoDate, todayIso)`。
- Produces: `groupTasks(tasks, selectedSubject)` 返回 `{ subjects, groups }`。

- [ ] **Step 1: 写工具函数失败测试**

使用 Node 内置 `node:test`：

```javascript
test('groups tasks by subject and computes progress', () => {
  const result = groupTasks([
    { id: 1, subject: '数学', status: 'corrected' },
    { id: 2, subject: '数学', status: 'todo' },
    { id: 3, subject: '英语', status: 'needs_review' }
  ], '全部')
  assert.deepEqual(result.groups.map((item) => [item.subject, item.completedTasks, item.totalTasks]), [
    ['数学', 1, 2], ['英语', 1, 1]
  ])
})
```

日期测试覆盖跨月、跨年和“今天/昨天/明天”。

- [ ] **Step 2: 运行测试确认 RED**

Run: `node --test miniapp/tests/date.test.js miniapp/tests/task-groups.test.js`

Expected: FAIL，模块不存在。

- [ ] **Step 3: 实现纯工具和请求参数**

`today` 使用 `encodeURIComponent(targetDate)`；没有日期时保持旧请求。日期函数只使用本地 `YYYY-MM-DD` 分量构造日期，避免 UTC 导致东八区跨日。

- [ ] **Step 4: 改造学生页面**

页面数据增加：

```javascript
selectedDate: '',
dateLabel: '',
selectedSubject: '全部',
subjects: [],
taskGroups: [],
loading: false
```

实现 `previousDay`、`nextDay`、`backToday`、`onDateChange`、`selectSubject` 和 `loadTasks`。WXML 使用日期导航、横向科目筛选和 `taskGroups` 双层循环；标题不再固定写“今日任务”。

- [ ] **Step 5: 验证工具测试和 JS 语法**

Run: `node --test miniapp/tests/date.test.js miniapp/tests/task-groups.test.js && node --check miniapp/pages/student/today/index.js && node --check miniapp/services/task.js`

Expected: PASS，exit 0。

- [ ] **Step 6: 提交**

```bash
git add miniapp/utils miniapp/tests miniapp/services/task.js miniapp/pages/student/today
git commit -m "增加学生计划日期与科目切换"
```

---

### Task 3: 家长端计划按日期和科目组织

**Files:**
- Modify: `miniapp/pages/parent/plan-calendar/index.js`
- Modify: `miniapp/pages/parent/plan-calendar/index.wxml`
- Modify: `miniapp/pages/parent/plan-calendar/index.wxss`
- Test: `miniapp/tests/task-groups.test.js`

**Interfaces:**
- Consumes: Task 1 的日历 `plan` 和 `items`。
- Consumes: Task 2 的日期及分组工具。

- [ ] **Step 1: 添加按日期过滤失败测试**

为 `task-groups.js` 增加 `tasksForDate(tasks, date)` 测试，断言只返回目标日期且输入数组不被修改。

- [ ] **Step 2: 运行测试确认 RED**

Run: `node --test miniapp/tests/task-groups.test.js`

Expected: FAIL，`tasksForDate` 不存在。

- [ ] **Step 3: 实现过滤函数与家长页面**

页面保存 `plan`、`items`、`selectedDate`、`dateLabel`、`taskGroups`。日期切换限制在 `plan.start_date` 和 `plan.end_date`；WXML 先显示日期进度，再显示科目标题和任务卡片，并在每张卡明确展示科目。

- [ ] **Step 4: 验证**

Run: `node --test miniapp/tests/task-groups.test.js && node --check miniapp/pages/parent/plan-calendar/index.js`

Expected: PASS，exit 0。

- [ ] **Step 5: 提交**

```bash
git add miniapp/utils/task-groups.js miniapp/tests/task-groups.test.js miniapp/pages/parent/plan-calendar
git commit -m "按日期和科目展示家长计划"
```

---

### Task 4: 移除学生答案入口并强化提交校验

**Files:**
- Modify: `backend/app/schemas/requests.py`
- Modify: `backend/app/api/routers/submissions.py`
- Modify: `backend/tests/test_v1_flow.py`
- Modify: `miniapp/pages/student/upload-homework/index.js`
- Modify: `miniapp/pages/student/upload-homework/index.wxml`
- Modify: `miniapp/pages/student/upload-homework/index.wxss`

**Interfaces:**
- Produces: `SubmissionCreateIn` 使用 `extra="forbid"`，不包含 `answer_text`。
- Produces: 完成提交前要求至少一个 `purpose=homework` 媒体。

- [ ] **Step 1: 写拒绝答案和空媒体失败测试**

```python
response = client.post("/api/v1/submissions", headers=headers, json={
    "daily_task_id": task_id,
    "submission_type": "photo",
    "answer_text": "不应由学生提交",
})
assert response.status_code == 422
```

另创建无媒体 submission，调用 `/complete` 并断言 422，状态仍为 `draft`。

- [ ] **Step 2: 运行测试确认 RED**

Run: `pytest backend/tests/test_v1_flow.py -k "rejects_student_answer or complete_requires_homework_media" -q`

Expected: FAIL，当前接受答案且允许空提交。

- [ ] **Step 3: 实现后端最小校验**

移除请求 schema 和 router 中的新写入路径；上传接口把外部 `purpose` 限定为 `homework`。保留数据库历史列。为不存在的任务、提交增加 404，为无媒体增加 422。

- [ ] **Step 4: 删除小程序答案 UI 与逻辑**

移除 `answerMedia`、`answerText`、`onAnswerText`、`chooseAnswerImages`、`chooseAnswerFiles`、`purpose` 分支和 `submissionApi.update` 调用；提交直接调用 `complete`。

- [ ] **Step 5: 验证**

Run: `pytest backend/tests/test_v1_flow.py -q && node --check miniapp/pages/student/upload-homework/index.js`

Expected: PASS，且 `rg -n "答案（可选）|chooseAnswer|answerText" miniapp/pages/student/upload-homework` 无输出。

- [ ] **Step 6: 提交**

```bash
git add backend/app/schemas/requests.py backend/app/api/routers/submissions.py backend/tests/test_v1_flow.py miniapp/pages/student/upload-homework
git commit -m "移除学生上传答案入口"
```

---

### Task 5: 建立可靠批改状态与失败落库

**Files:**
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/core/database.py`
- Modify: `backend/app/services/correction_service.py`
- Modify: `backend/app/worker/tasks/correct_homework.py`
- Modify: `backend/app/api/routers/results.py`
- Test: `backend/tests/test_ai_services.py`
- Test: `backend/tests/test_v1_flow.py`

**Interfaces:**
- Adds: `Submission.error_code: str | None`、`Submission.error_message: str | None`。
- Produces: `run_correction(db, submission) -> CorrectionResult`；失败抛出有类型的异常，不生成模拟结果。
- Produces: 结果接口返回 `submission.status/error_code/error_message` 和关联当前 submission 的结果。

- [ ] **Step 1: 写失败、幂等和最新提交测试**

测试 AI 抛错后 submission/task 均为 `failed` 且没有 `CorrectionResult`；重复运行 corrected submission 不新增结果；任务存在两次提交时只返回最新提交关联的结果。

- [ ] **Step 2: 运行测试确认 RED**

Run: `pytest backend/tests/test_ai_services.py backend/tests/test_v1_flow.py -k "correction_failure or correction_idempotent or latest_submission_result" -q`

Expected: FAIL，当前异常被吞并生成模拟结果。

- [ ] **Step 3: 增加兼容字段与状态函数**

SQLite 启动迁移仅在列不存在时执行：

```sql
ALTER TABLE submissions ADD COLUMN error_code VARCHAR(64)
ALTER TABLE submissions ADD COLUMN error_message TEXT
```

增加 `mark_correction_failed(db, submission, code, message)`，对用户消息限长并避免保存异常请求正文。

- [ ] **Step 4: 删除模拟回退并实现 worker 幂等**

把 `create_mock_correction` 替换为真实 `create_correction`。worker 对终态直接返回现有结果；捕获配置、媒体、HTTP、解析异常后写 `failed`，不重新抛出导致状态丢失。

- [ ] **Step 5: 修正结果关联**

先找最新 submission，再用 `CorrectionResult.submission_id == submission.id` 查询，响应中加入状态和错误字段；问题项加入 `confidence_score`。

- [ ] **Step 6: 验证**

Run: `pytest backend/tests/test_ai_services.py backend/tests/test_v1_flow.py -q`

Expected: PASS；`rg -n "completion_score=92|accuracy_score=None if is_video else 82|recognized_answer=\"36\"" backend/app` 无输出。

- [ ] **Step 7: 提交**

```bash
git add backend/app/models backend/app/core/database.py backend/app/services/correction_service.py backend/app/worker/tasks/correct_homework.py backend/app/api/routers/results.py backend/tests
git commit -m "实现可追踪的真实批改状态"
```

---

### Task 6: 照片逐题批改与结果校验

**Files:**
- Modify: `backend/app/services/correction_ai_service.py`
- Modify: `backend/app/services/correction_service.py`
- Test: `backend/tests/test_ai_services.py`

**Interfaces:**
- Produces: `normalize_correction_payload(payload: dict) -> dict`。
- Rule: 总分 0..100，置信度 0..1；缺少可信结论时 `needs_review=True`。

- [ ] **Step 1: 写结构归一化失败测试**

测试字符串数值、越界数值、缺失逐题字段、低置信度和无效 JSON；断言只对完全不可解析结果抛错，不伪造答案。

- [ ] **Step 2: 运行测试确认 RED**

Run: `pytest backend/tests/test_ai_services.py -k "normalize_correction or photo_correction" -q`

Expected: FAIL，归一化函数不存在或错误值原样入库。

- [ ] **Step 3: 实现照片请求和归一化**

请求只读取 `purpose=homework` 媒体；标准答案来自 `AssignmentItem.answer_text`。模型提示明确要求纯 JSON、不可判断时使用 `null` 并设置复核。使用代码剥离 Markdown code fence 后解析 JSON。

- [ ] **Step 4: 验证**

Run: `pytest backend/tests/test_ai_services.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/correction_ai_service.py backend/app/services/correction_service.py backend/tests/test_ai_services.py
git commit -m "接通照片作业逐题批改"
```

---

### Task 7: 视频自动分类、转写与抽帧

**Files:**
- Modify: `backend/app/services/media_processing_service.py`
- Modify: `backend/app/services/correction_ai_service.py`
- Modify: `backend/app/core/config.py`
- Test: `backend/tests/test_ai_services.py`

**Interfaces:**
- Produces: `classify_video_strategy(task: DailyTask) -> Literal["speech", "visual", "mixed"]`。
- Produces: `extract_video_frames(file_path: str, max_frames: int) -> list[str]`。
- Adds: `video_max_frames: int = 8`。

- [ ] **Step 1: 写分类和抽帧失败测试**

覆盖朗读/背诵/口语为 `speech`，书写/计算/操作为 `visual`，不明确为 `mixed`；mock `subprocess.run` 验证 FFmpeg 带时长限制且返回不超过上限的帧。

- [ ] **Step 2: 运行测试确认 RED**

Run: `pytest backend/tests/test_ai_services.py -k "video_strategy or video_frames or video_correction" -q`

Expected: FAIL，分类和抽帧函数不存在。

- [ ] **Step 3: 实现分类与媒体准备**

优先使用 `task_type`，再使用标题关键词。`mixed` 同时尝试音频与关键帧，最终强制 `needs_review=True`。FFmpeg 失败抛出媒体处理异常，不返回虚假成功。

- [ ] **Step 4: 接入批改内容**

`speech` 发送转写，`visual` 发送帧图片，`mixed` 发送可获得的两类内容。两类内容均不可用时进入 `failed`；只有一类可用时进入 `needs_review`。

- [ ] **Step 5: 验证**

Run: `pytest backend/tests/test_ai_services.py -q`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add backend/app/core/config.py backend/app/services/media_processing_service.py backend/app/services/correction_ai_service.py backend/tests/test_ai_services.py
git commit -m "实现视频作业分类与抽帧批改"
```

---

### Task 8: 学生与家长结果状态、复核和重试

**Files:**
- Modify: `miniapp/pages/student/result-detail/index.js`
- Modify: `miniapp/pages/student/result-detail/index.wxml`
- Modify: `miniapp/pages/student/result-detail/index.wxss`
- Modify: `miniapp/pages/parent/task-result/index.js`
- Modify: `miniapp/pages/parent/task-result/index.wxml`
- Modify: `miniapp/pages/parent/task-result/index.wxss`
- Test: `miniapp/tests/result-state.test.js`
- Create: `miniapp/utils/result-state.js`

**Interfaces:**
- Produces: `resultViewState(payload) -> { kind, title, message, shouldPoll }`。
- Polling: 2 秒一次，最多 60 次；终态或页面卸载时清理 timer。

- [ ] **Step 1: 写四态映射失败测试**

测试 `processing/corrected/needs_review/failed`，并断言网络错误和轮询超时有非空提示。

- [ ] **Step 2: 运行测试确认 RED**

Run: `node --test miniapp/tests/result-state.test.js`

Expected: FAIL，模块不存在。

- [ ] **Step 3: 实现状态工具与学生结果页**

移除空 `catch`。终态停止轮询；失败页提供 `重新提交`，跳转到当前 task 的上传页；复核态展示 `review_reason` 和已有逐题结果；逐题 `is_correct === null` 显示“待复核”。

- [ ] **Step 4: 实现家长结果页**

展示提交状态、错误消息、复核原因、识别答案、参考答案、解释和置信度。无提交与处理中使用不同文案。

- [ ] **Step 5: 验证**

Run: `node --test miniapp/tests/result-state.test.js && node --check miniapp/pages/student/result-detail/index.js && node --check miniapp/pages/parent/task-result/index.js`

Expected: PASS，exit 0。

- [ ] **Step 6: 提交**

```bash
git add miniapp/utils/result-state.js miniapp/tests/result-state.test.js miniapp/pages/student/result-detail miniapp/pages/parent/task-result
git commit -m "完善批改结果与家长复核状态"
```

---

### Task 9: 全量验证与微信开发者工具验收清单

**Files:**
- Modify: `README.md`

**Interfaces:**
- Produces: README 中真实批改状态、FFmpeg、Celery 和手动验收说明。

- [ ] **Step 1: 更新运行与验收说明**

记录所需 AI 服务是 vision 和 ASR；说明 `async_tasks_eager=true` 用于本地、异步部署需要 Redis 与 Celery worker；列出微信开发者工具中的日期、科目、照片、三类视频、失败和复核验收路径。

- [ ] **Step 2: 安装依赖（仅环境缺失时）**

Run: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`

Expected: exit 0。

- [ ] **Step 3: 运行完整自动化验证**

Run: `.venv/bin/pytest backend/tests -q`

Expected: 全部 PASS，0 failed。

Run: `node --test miniapp/tests/*.test.js`

Expected: 全部 PASS，0 failed。

Run: `find miniapp -name '*.js' -print0 | xargs -0 -n1 node --check`

Expected: exit 0。

Run: `git diff --check`

Expected: exit 0。

- [ ] **Step 4: 检查需求禁用项**

Run: `rg -n "答案（可选）|chooseAnswer|create_mock_correction|completion_score=92|recognized_answer=\"36\"" miniapp backend/app`

Expected: 无输出。

- [ ] **Step 5: 在微信开发者工具手动验收**

验证学生日期切换和科目筛选、家长计划分组、照片提交、朗读视频、书写视频、不明确视频、批改失败、需要复核、重新提交。记录无法由命令行自动证明的 DevTools 构建结果。

- [ ] **Step 6: 最终提交**

```bash
git add README.md
git commit -m "补充真实批改运行与验收说明"
```

