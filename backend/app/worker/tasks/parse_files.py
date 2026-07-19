import json
from uuid import uuid4

from sqlalchemy import or_, select

from backend.app.core.database import SessionLocal
from backend.app.models import ImportBatch, ImportFile
from backend.app.services.answer_matching_service import match_batch_answers
from backend.app.services.document_extract_service import extract_text_from_document
from backend.app.services.import_content_service import analyze_import_content
from backend.app.services.import_lock_service import lock_import_batch_files
from backend.app.services.local_file_service import local_path_for_import_file
from backend.app.services.ocr_service import extract_text_from_file
from backend.app.worker.celery_app import celery_app


def _finish_batch_if_complete(db, batch_id: int) -> None:
    remaining = db.query(ImportFile).filter(
        ImportFile.import_batch_id == batch_id,
        or_(
            ImportFile.parse_status.is_(None),
            ImportFile.recognition_status.is_(None),
            ImportFile.parse_status.in_(("", "pending", "queued", "processing")),
            ImportFile.recognition_status.in_(("", "pending", "queued", "processing")),
        ),
    ).first()
    if remaining:
        return
    batch = db.get(ImportBatch, batch_id)
    if not batch:
        return
    files = db.query(ImportFile).filter(
        ImportFile.import_batch_id == batch.id
    ).order_by(ImportFile.sort_order, ImportFile.id).all()
    batch.merged_text = "\n".join([
        batch.raw_text or "",
        *[file.extracted_text or "" for file in files],
    ]).strip()
    batch.status = "parsed"


def _stale_result(import_file_id: int) -> dict:
    return {"ok": False, "stale": True, "file_id": import_file_id}


def _locked_import_file(db, import_file_id: int):
    batch_id = db.scalar(
        select(ImportFile.import_batch_id).where(ImportFile.id == import_file_id)
    )
    if batch_id is None:
        return None, None
    _batch, files = lock_import_batch_files(db, batch_id)
    return batch_id, next((item for item in files if item.id == import_file_id), None)


def _commit_parse_result(db) -> None:
    db.commit()


def _record_failure_if_owned(
    db,
    import_file_id: int,
    claim_token: str,
    error: Exception,
) -> bool:
    db.rollback()
    batch_id, failed_item = _locked_import_file(db, import_file_id)
    if (
        failed_item is None
        or failed_item.parse_claim_token != claim_token
        or failed_item.parse_status != "processing"
    ):
        db.rollback()
        return False
    failed_item.parse_status = "failed"
    failed_item.parse_error = str(error)
    failed_item.recognition_status = "failed"
    failed_item.recognition_error = str(error)
    failed_item.recognized_title = None
    failed_item.content_summary = None
    failed_item.content_signature_json = None
    failed_item.parse_claim_token = None
    db.flush()
    _finish_batch_if_complete(db, batch_id)
    db.commit()
    return True


@celery_app.task(name="parse_import_file")
def parse_import_file(
    import_file_id: int,
    claim_token: str | None = None,
) -> dict:
    db = SessionLocal()
    worker_token = claim_token
    try:
        batch_id, item = _locked_import_file(db, import_file_id)
        if item is None:
            db.rollback()
            return {"ok": False, "error": "file not found"}
        if claim_token is None:
            legacy_states = {None, "", "pending", "failed"}
            if (
                item.parse_claim_token is not None
                or item.parse_status not in legacy_states
                or item.recognition_status not in legacy_states
            ):
                db.rollback()
                return _stale_result(import_file_id)
            worker_token = uuid4().hex
            item.parse_claim_token = worker_token
        elif (
            item.parse_claim_token != claim_token
            or item.parse_status != "queued"
            or item.recognition_status != "queued"
        ):
            db.rollback()
            return _stale_result(import_file_id)

        item.parse_status = "processing"
        item.parse_error = None
        item.recognition_status = "processing"
        item.recognition_error = None
        local_path = str(local_path_for_import_file(item))
        file_type = item.file_type
        document_role = item.document_role or "homework"
        db.commit()

        extracted_text = (
            extract_text_from_file(local_path, file_type)
            or extract_text_from_document(local_path, file_type)
            or ""
        ).strip()
        if not extracted_text:
            raise ValueError("未提取到可识别内容")
        analysis = analyze_import_content(extracted_text, document_role)

        _batch, files = lock_import_batch_files(db, batch_id)
        item = next((row for row in files if row.id == import_file_id), None)
        if (
            item is None
            or item.parse_claim_token != worker_token
            or item.parse_status != "processing"
            or item.recognition_status != "processing"
        ):
            db.rollback()
            return _stale_result(import_file_id)

        item.extracted_text = extracted_text
        item.parse_status = "success"
        item.parse_error = None
        item.recognized_title = analysis["recognized_title"]
        item.recognition_status = analysis["recognition_status"]
        item.content_signature_json = json.dumps(
            analysis["signature"],
            ensure_ascii=False,
        )
        item.content_summary = analysis["signature"].get("content_summary")
        item.recognition_error = (
            None
            if item.recognition_status == "success"
            else "内容识别置信度不足"
        )
        item.parse_claim_token = None
        if document_role == "homework":
            item.match_status = "not_required"
            item.matched_homework_file_id = None
        else:
            item.match_status = "pending"
        db.flush()
        match_batch_answers(db, batch_id, commit=False)
        _finish_batch_if_complete(db, batch_id)
        _commit_parse_result(db)
        return {"ok": True, "file_id": item.id}
    except Exception as exc:
        if worker_token and _record_failure_if_owned(
            db,
            import_file_id,
            worker_token,
            exc,
        ):
            raise
        return _stale_result(import_file_id)
    finally:
        db.close()
