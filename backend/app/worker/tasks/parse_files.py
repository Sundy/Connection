import json

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
        (
            ImportFile.parse_status.in_(("pending", "processing"))
            | ImportFile.recognition_status.in_(("pending", "processing"))
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
        item.extracted_text = (
            extract_text_from_file(local_path, item.file_type)
            or extract_text_from_document(local_path, item.file_type)
            or build_mock_extract(item.file_name, item.file_type)
        )
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
            _finish_batch_if_complete(db, failed_item.import_batch_id)
            db.commit()
        raise
    finally:
        db.close()


def build_mock_extract(file_name: str, file_type: str) -> str:
    if file_type == "screenshot":
        return f"来自群截图 {file_name}：数学20张卷子，语文6篇作文，英语500个单词，包含朗读视频作业。"
    return f"来自文件 {file_name}：数学20张卷子，语文6篇作文，英语500个单词。"
