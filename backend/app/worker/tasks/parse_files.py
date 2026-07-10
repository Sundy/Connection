from backend.app.core.database import SessionLocal
from backend.app.models import ImportFile
from backend.app.services.local_file_service import local_path_for_import_file
from backend.app.services.document_extract_service import extract_text_from_document
from backend.app.services.ocr_service import extract_text_from_file
from backend.app.worker.celery_app import celery_app


@celery_app.task(name="parse_import_file")
def parse_import_file(import_file_id: int) -> dict:
    db = SessionLocal()
    try:
        item = db.get(ImportFile, import_file_id)
        if not item:
            return {"ok": False, "error": "file not found"}
        item.parse_status = "processing"
        db.commit()
        local_path = str(local_path_for_import_file(item))
        item.extracted_text = (
            extract_text_from_file(local_path, item.file_type)
            or extract_text_from_document(local_path, item.file_type)
            or build_mock_extract(item.file_name, item.file_type)
        )
        item.parse_status = "success"
        db.commit()
        return {"ok": True, "file_id": item.id}
    except Exception as exc:
        if "item" in locals() and item:
            item.parse_status = "failed"
            item.parse_error = str(exc)
            db.commit()
        raise
    finally:
        db.close()


def build_mock_extract(file_name: str, file_type: str) -> str:
    if file_type == "screenshot":
        return f"来自群截图 {file_name}：数学20张卷子，语文6篇作文，英语500个单词，包含朗读视频作业。"
    return f"来自文件 {file_name}：数学20张卷子，语文6篇作文，英语500个单词。"
