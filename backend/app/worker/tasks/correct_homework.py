import json
import logging

import httpx

from backend.app.core.database import SessionLocal
from backend.app.models import CorrectionResult, Submission
from backend.app.services.correction_service import MissingHomeworkMediaError, create_correction, mark_correction_failed
from backend.app.worker.celery_app import celery_app


logger = logging.getLogger(__name__)


def _failure_details(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, MissingHomeworkMediaError):
        return "missing_homework_media", "未找到作业图片或视频，请重新上传后提交。"
    if isinstance(exc, FileNotFoundError):
        return "media_file_missing", "作业文件已失效，请重新上传后提交。"
    if isinstance(exc, httpx.HTTPError):
        return "ai_request_failed", "AI 批改服务请求失败，请稍后重新提交。"
    if isinstance(exc, (json.JSONDecodeError, ValueError)):
        return "ai_response_invalid", "AI 返回的批改结果无法解析，请重新提交。"
    return "correction_failed", "批改服务暂时不可用，请稍后重试。"


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
        except Exception as exc:
            logger.exception("Homework correction failed for submission_id=%s", submission_id)
            db.rollback()
            submission = db.get(Submission, submission_id)
            error_code, error_message = _failure_details(exc)
            mark_correction_failed(db, submission, error_code, error_message)
            return {"ok": False, "error": error_code}
        return {"ok": True, "correction_result_id": result.id, "status": submission.status}
    finally:
        db.close()
