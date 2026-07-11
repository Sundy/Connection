from backend.app.core.database import SessionLocal
from backend.app.models import CorrectionResult, Submission
from backend.app.services.correction_service import create_correction, mark_correction_failed
from backend.app.worker.celery_app import celery_app


@celery_app.task(name="run_homework_correction")
def run_homework_correction(submission_id: int) -> dict:
    db = SessionLocal()
    try:
        submission = db.get(Submission, submission_id)
        if not submission:
            return {"ok": False, "error": "submission not found"}
        if submission.status in {"corrected", "needs_review"}:
            result = db.query(CorrectionResult).filter(
                CorrectionResult.submission_id == submission.id,
            ).order_by(CorrectionResult.id.desc()).first()
            return {
                "ok": True,
                "correction_result_id": result.id if result else None,
                "status": submission.status,
            }
        submission.status = "processing"
        submission.error_code = None
        submission.error_message = None
        db.commit()
        try:
            result = create_correction(db, submission)
        except Exception:
            db.rollback()
            submission = db.get(Submission, submission_id)
            mark_correction_failed(db, submission)
            return {"ok": False, "error": "correction_failed"}
        return {"ok": True, "correction_result_id": result.id, "status": submission.status}
    finally:
        db.close()
