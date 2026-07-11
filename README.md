# Homework Agent V1

V1 scaffold for a native WeChat mini program plus FastAPI backend and Celery worker.

## Local Backend

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn backend.app.main:app --reload
```

The default database is local SQLite at `backend/dev.db`. Set `DATABASE_URL` to a MySQL SQLAlchemy URL for deployment.

## Qwen Model Config

`.env` is ignored by git and is used for local model configuration:

```bash
DashScope / Qwen shared config:
DASHSCOPE_API_KEY=your-dashscope-api-key
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

Text planning:
LLM_PROVIDER=qwen
LLM_API_KEY=
LLM_BASE_URL=
LLM_MODEL=qwen-plus
LLM_TIMEOUT_SECONDS=60
LLM_TEMPERATURE=0.2

OCR:
OCR_PROVIDER=qwen
OCR_MODEL=qwen-vl-ocr
OCR_TIMEOUT_SECONDS=120
OCR_MAX_PAGES=20

Vision correction:
VISION_PROVIDER=qwen
VISION_MODEL=qwen-vl-plus
VISION_TIMEOUT_SECONDS=120
VISION_MAX_IMAGES=8

ASR:
ASR_PROVIDER=qwen
ASR_MODEL=qwen3-asr-flash
ASR_TIMEOUT_SECONDS=300

Video preprocessing:
FFMPEG_PATH=ffmpeg
VIDEO_FRAME_FPS=1
VIDEO_MAX_DURATION_SECONDS=300
VIDEO_MAX_FRAMES=8
```

照片批改依赖 Vision 配置；朗读、背诵和口语视频同时依赖 ASR。书写、计算过程和操作展示视频由 FFmpeg 抽取有限数量关键帧，再交给 Vision 模型。无法可靠分类或低置信度的结果会标记为“需要家长复核”，AI 或媒体处理失败会显示失败原因，不会生成模拟分数。

本地默认 `ASYNC_TASKS_EAGER=true`，批改任务在请求后的后台任务中立即执行。异步部署时设置 `ASYNC_TASKS_EAGER=false`，确保 Redis 可用，并启动 worker：

```bash
celery -A backend.app.worker.celery_app.celery_app worker --loglevel=info
```

## Smoke Test

```bash
pytest backend/tests
node --test miniapp/tests/*.test.js
```

## Mini Program Acceptance

在微信开发者工具中检查：

- 学生端可切换前一天、今天、后一天和任意日期，任务按科目分组并可筛选。
- 家长端周期计划可在计划范围内切换日期，并按科目查看进度。
- 学生提交页只有拍照/相册和拍视频，没有上传答案入口。
- 照片作业能展示总评和逐题结果。
- 朗读视频走转写流程，书写视频走抽帧流程，不明确的视频显示“需要家长复核”。
- 批改失败停止轮询并允许重新提交；需要复核时家长端展示原因和逐题置信度。

## Role Navigation

家长一级导航为“首页 / 学习计划 / 我的”，学生一级导航为“今日学习 / 我的”。家庭邀请码、孩子管理、家庭加入和身份信息集中在“我的”；学生不会看到添加孩子或复制家庭邀请码。任务详情、导入、专注、提交和批改结果等二级页面不显示底部导航。
