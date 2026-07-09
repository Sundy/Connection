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

## Smoke Test

```bash
pytest backend/tests
```
