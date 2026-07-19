import json

from sqlalchemy import or_

from backend.app.core.database import SessionLocal
from backend.app.models import ImportBatch, ImportFile
from backend.app.services.answer_matching_service import match_batch_answers
from backend.app.services.import_content_service import analyze_import_content
from backend.app.services.local_file_service import local_path_for_import_file
from backend.app.services.document_extract_service import extract_text_from_document
from backend.app.services.ocr_service import extract_text_from_file
from backend.app.worker.celery_app import celery_app


def _finish_batch_if_complete(db, batch_id: int) -> None:
    remaining = db.query(ImportFile).filter(
        ImportFile.import_batch_id == batch_id,
        or_(
            ImportFile.parse_status.is_(None),
            ImportFile.recognition_status.is_(None),
            ImportFile.parse_status.in_(("pending", "queued", "processing")),
            ImportFile.recognition_status.in_(("pending", "queued", "processing")),
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


@celery_app.task(name="parse_import_file")
def parse_import_file(import_file_id: int) -> dict:
    db = SessionLocal()
    try:
        item = db.get(ImportFile, import_file_id)
        if not item:
            return {"ok": False, "error": "file not found"}
        item.parse_status = "processing"
        item.parse_error = None
        item.recognition_status = "processing"
        item.recognition_error = None
        db.commit()
        local_path = str(local_path_for_import_file(item))
        extracted_text = (
            extract_text_from_file(local_path, item.file_type)
            or extract_text_from_document(local_path, item.file_type)
            or ""
        ).strip()
        if not extracted_text:
            raise ValueError("未提取到可识别内容")
        item.extracted_text = extracted_text
        analysis = analyze_import_content(
            item.extracted_text,
            item.document_role or "homework",
        )
        item.parse_status = "success"
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
        if (item.document_role or "homework") == "homework":
            item.match_status = "not_required"
            item.matched_homework_file_id = None
        else:
            item.match_status = "pending"
        db.commit()
        match_batch_answers(db, item.import_batch_id)
        _finish_batch_if_complete(db, item.import_batch_id)
        db.commit()
        return {"ok": True, "file_id": item.id}
    except Exception as exc:
        db.rollback()
        failed_item = db.get(ImportFile, import_file_id)
        if failed_item:
            failed_item.parse_status = "failed"
            failed_item.parse_error = str(exc)
            failed_item.recognition_status = "failed"
            failed_item.recognition_error = str(exc)
            failed_item.recognized_title = None
            failed_item.content_summary = None
            failed_item.content_signature_json = None
            db.flush()
            _finish_batch_if_complete(db, failed_item.import_batch_id)
            db.commit()
        raise
    finally:
        db.close()
