from backend.app.core.database import SessionLocal
from backend.app.models import Submission
from backend.app.services.correction_service import create_mock_correction
from backend.app.worker.celery_app import celery_app


@celery_app.task(name="run_homework_correction")
def run_homework_correction(submission_id: int) -> dict:
    db = SessionLocal()
    try:
        submission = db.get(Submission, submission_id)
        if not submission:
            return {"ok": False, "error": "submission not found"}
        result = create_mock_correction(db, submission)
        return {"ok": True, "correction_result_id": result.id}
    finally:
        db.close()
