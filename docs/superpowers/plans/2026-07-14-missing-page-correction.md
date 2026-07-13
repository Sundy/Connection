# 多页作业漏批提示 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 多页批改缺少某一页结果时进入待复核，并在该页原图旁明确显示漏批提示。

**Architecture:** 批改落库前校验模型页码覆盖率，缺页时合并复核原因；结果页构建服务基于每页关联题目生成页面状态，供学生端和家长端共用组件展示。历史结果通过读取时的页面状态立即获得提示，无需数据库迁移。

**Tech Stack:** FastAPI、SQLAlchemy、pytest、原生微信小程序、Node.js test runner

## Global Constraints

- 未生成批改数据的页面不得推断为全对或错误。
- 已有页面的题目、分数和红绿批注保持不变。
- 不增加数据库字段或环境变量。

---

### Task 1: 批改页覆盖校验

**Files:**
- Modify: `backend/app/services/correction_service.py`
- Test: `backend/tests/test_correction_annotations.py`

**Interfaces:**
- Consumes: `payload["questions"][*]["source_image_index"]` 和实际作业图片数量
- Produces: 缺页时 `CorrectionResult.needs_review=True`，`review_reason` 包含缺失页码

- [ ] **Step 1: Write the failing test**

新增两页照片、仅第一页有题目的批改测试，断言结果与提交为待复核且原因包含“第 2 页”。

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest backend/tests/test_correction_annotations.py -k "missing_page" -q`
Expected: FAIL，因为当前结果仍是 `corrected`。

- [ ] **Step 3: Write minimal implementation**

在 `create_correction` 调用落库函数前，计算缺失的 1-based 页面索引；缺页时复制 payload、设置 `needs_review` 并合并 `review_reason`。

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest backend/tests/test_correction_annotations.py -k "missing_page" -q`
Expected: PASS。

### Task 2: 结果接口与整页提示

**Files:**
- Modify: `backend/app/services/result_page_service.py`
- Modify: `miniapp/components/annotated-homework-page/index.wxml`
- Modify: `miniapp/components/annotated-homework-page/index.wxss`
- Test: `backend/tests/test_v1_flow.py`
- Test: `miniapp/tests/result-page-layout.test.js`

**Interfaces:**
- Produces: `page.has_correction: bool` 和 `page.review_message: str | None`
- Consumes: `page.review_message` 并在整页原图前显示中性漏批提示

- [ ] **Step 1: Write the failing tests**

后端接口测试增加一张无题目页面并断言页面状态；小程序布局测试断言共用组件绑定 `page.review_message` 且包含“不能判断为全对”。

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest backend/tests/test_v1_flow.py -k "teacher_style_pages" -q && node --test miniapp/tests/result-page-layout.test.js`
Expected: FAIL，因为接口和组件尚无漏批状态。

- [ ] **Step 3: Write minimal implementation**

结果页构建时按 `page_questions` 生成两个字段；组件用中性色警告块展示提示。

- [ ] **Step 4: Run focused tests to verify they pass**

Run: `.venv/bin/python -m pytest backend/tests/test_v1_flow.py -k "teacher_style_pages" -q && node --test miniapp/tests/result-page-layout.test.js`
Expected: PASS。

### Task 3: 全量验证

**Files:**
- Verify all modified files

- [ ] **Step 1: Run backend tests**

Run: `.venv/bin/python -m pytest backend/tests -q`
Expected: 全部通过。

- [ ] **Step 2: Run miniapp tests**

Run: `node --test miniapp/tests/*.test.js`
Expected: 全部通过。

- [ ] **Step 3: Check whitespace and diff**

Run: `git diff --check && git status --short`
Expected: 无空白错误，仅包含本次修正文件。
