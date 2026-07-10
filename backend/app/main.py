from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.app.api.routers import auth, families, imports, notifications, plans, reports, results, students, study_sessions, submissions, tasks
from backend.app.core.database import init_db

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Homework Agent API", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


app.include_router(auth.router, prefix="/api/v1")
app.include_router(families.router, prefix="/api/v1")
app.include_router(students.router, prefix="/api/v1")
app.include_router(imports.router, prefix="/api/v1")
app.include_router(plans.router, prefix="/api/v1")
app.include_router(tasks.router, prefix="/api/v1")
app.include_router(study_sessions.router, prefix="/api/v1")
app.include_router(submissions.router, prefix="/api/v1")
app.include_router(results.router, prefix="/api/v1")
app.include_router(reports.router, prefix="/api/v1")
app.include_router(notifications.router, prefix="/api/v1")
