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
```

## Smoke Test

```bash
pytest backend/tests
```
