from datetime import date, timedelta
import math
from pathlib import Path
import re

from sqlalchemy.orm import Session

from backend.app.models import AssignmentBatch, AssignmentItem, DailyTask, ImportBatch, ImportFile
from backend.app.services.llm_service import extract_assignment_items_with_llm


SUBJECTS = ["数学", "语文", "英语", "物理", "化学", "科学", "阅读", "口语"]
SUBJECT_KEYWORDS = {
    "数学": ["口算", "计算", "应用题", "竖式", "数学题", "卷子"],
    "语文": ["课文", "作文", "阅读", "摘抄", "生字", "古诗", "背诵"],
    "英语": ["单词", "英语", "口语", "听力", "默写", "朗读"],
    "科学": ["实验", "科学"],
}


def infer_subject(text: str, file_name: str = "") -> str:
    haystack = f"{file_name}\n{text}"
    for subject in SUBJECTS:
        if subject in haystack:
            return subject
        if any(keyword in haystack for keyword in SUBJECT_KEYWORDS.get(subject, [])):
            return subject
    return "综合"


def _title_from_file_name(file_name: str, subject: str) -> str:
    stem = Path(file_name).stem.strip()
    if stem:
        return stem
    return f"{subject}作业"


def merge_import_texts(db: Session, batch: ImportBatch) -> str:
    files = db.query(ImportFile).filter(ImportFile.import_batch_id == batch.id).order_by(ImportFile.sort_order).all()
    parts = [batch.raw_text or ""]
    parts.extend(file.extracted_text or "" for file in files)
    merged = "\n".join(part for part in parts if part.strip())
    batch.merged_text = merged or "数学20张卷子，语文6篇作文，英语500个单词"
    batch.status = "parsed"
    db.commit()
    return batch.merged_text


def _segment_for_subject(text: str, subject: str) -> str | None:
    if subject in text:
        start = text.find(subject)
        return text[start:start + 40]

    for keyword in SUBJECT_KEYWORDS.get(subject, []):
        if keyword in text:
            start = max(text.find(keyword) - 12, 0)
            return text[start:start + 52]
    return None


def _item_from_segment(subject: str, segment: str) -> dict:
        quantity_match = re.search(r"(\d+(?:\.\d+)?)", segment)
        quantity = float(quantity_match.group(1)) if quantity_match else 1
        unit = "项"
        for candidate in ["道", "张", "篇", "个", "页", "遍", "次", "套"]:
            if candidate in segment:
                unit = candidate
                break
        task_type = "recitation" if "背" in segment or "朗读" in segment else "written"
        submit_type = "video" if task_type == "recitation" else "photo"
        title = f"{subject}{int(quantity) if quantity.is_integer() else quantity}{unit}"
        return {
            "subject": subject,
            "title": title,
            "task_type": task_type,
            "submit_type": submit_type,
            "source_text": segment,
            "total_quantity": quantity,
            "unit": unit,
            "estimated_minutes_total": int(quantity * (20 if unit in ["个", "页"] else 45)),
            "need_confirmation": quantity == 1 and not quantity_match,
            "confidence_score": 0.86 if quantity_match else 0.52,
        }


def extract_items_with_local_rules(text: str) -> list[dict]:
    items: list[dict] = []
    seen_subjects: set[str] = set()
    for subject in SUBJECTS:
        segment = _segment_for_subject(text, subject)
        if not segment or subject in seen_subjects:
            continue
        items.append(_item_from_segment(subject, segment))
        seen_subjects.add(subject)
    if not items:
        items.append({
            "subject": "综合",
            "title": "完成导入作业",
            "task_type": "mixed",
            "submit_type": "mixed",
            "source_text": text[:80],
            "total_quantity": 1,
            "unit": "项",
            "estimated_minutes_total": 45,
            "need_confirmation": True,
            "confidence_score": 0.45,
        })
    return items


def extract_items(text: str) -> list[dict]:
    try:
        llm_items = extract_assignment_items_with_llm(text)
    except Exception:
        llm_items = []
    return llm_items or extract_items_with_local_rules(text)


def extract_items_from_files(files: list[ImportFile]) -> list[dict]:
    items: list[dict] = []
    for file in files:
        text = (file.extracted_text or "").strip()
        subject = infer_subject(text, file.file_name)
        task_type = "recitation" if any(keyword in text for keyword in ["背", "朗读", "口语"]) else "written"
        items.append({
            "subject": subject,
            "title": _title_from_file_name(file.file_name, subject),
            "task_type": task_type,
            "submit_type": "video" if task_type == "recitation" else "photo",
            "source_text": text[:500] or f"来自文件：{file.file_name}",
            "import_file_id": file.id,
            "source_file_name": file.file_name,
            "total_quantity": 1,
            "unit": "份",
            "estimated_minutes_total": 60,
            "need_confirmation": False,
            "confidence_score": 0.9 if subject != "综合" else 0.55,
        })
    return items


def generate_plan_from_import(db: Session, batch_id: int) -> AssignmentBatch:
    batch = db.get(ImportBatch, batch_id)
    if not batch:
        raise ValueError("Import batch not found")

    files = db.query(ImportFile).filter(
        ImportFile.import_batch_id == batch.id,
        ImportFile.parse_status == "success",
    ).order_by(ImportFile.sort_order).all()
    text = batch.merged_text or merge_import_texts(db, batch)
    plan = db.query(AssignmentBatch).filter(AssignmentBatch.import_batch_id == batch.id).first()
    if plan:
        if plan.status != "pending_confirm":
            return plan
        db.query(DailyTask).filter(DailyTask.assignment_batch_id == plan.id).delete()
        db.query(AssignmentItem).filter(AssignmentItem.assignment_batch_id == plan.id).delete()
        plan.total_estimated_minutes = 0
        db.flush()
    else:
        plan = AssignmentBatch(
            student_id=batch.student_id,
            import_batch_id=batch.id,
            title=batch.title,
            period_type=batch.period_type,
            start_date=batch.start_date,
            end_date=batch.end_date,
            status="pending_confirm",
        )
        db.add(plan)
        db.flush()

    total_minutes = 0
    item_source = extract_items_from_files(files) if files else extract_items(text)
    for index, item_data in enumerate(item_source):
        item = AssignmentItem(assignment_batch_id=plan.id, status="draft", **item_data)
        db.add(item)
        db.flush()
        total_minutes += item.estimated_minutes_total
        create_daily_tasks(db, plan, item, index)

    plan.total_estimated_minutes = total_minutes
    batch.status = "pending_confirm"
    db.commit()
    db.refresh(plan)
    return plan


def create_daily_tasks(db: Session, plan: AssignmentBatch, item: AssignmentItem, day_offset: int = 0) -> None:
    start = plan.start_date or date.today()
    end = plan.end_date or (start + timedelta(days=14))
    days = max((end - start).days + 1, 1)
    if item.unit == "份" and item.total_quantity == 1:
        task_date = start + timedelta(days=min(day_offset, days - 1))
        db.add(DailyTask(
            student_id=plan.student_id,
            assignment_batch_id=plan.id,
            assignment_item_id=item.id,
            task_date=task_date,
            subject=item.subject,
            title=f"{item.subject}：完成《{item.title}》",
            task_type=item.task_type,
            submit_type=item.submit_type,
            planned_quantity=1,
            unit=item.unit,
            estimated_minutes=max(item.estimated_minutes_total, 10),
        ))
        return

    work_days = max(days - 2, 1)
    chunks = max(min(math.ceil(item.total_quantity), work_days), 1)
    per_chunk = item.total_quantity / chunks

    for index in range(chunks):
        task_date = start + timedelta(days=index)
        db.add(DailyTask(
            student_id=plan.student_id,
            assignment_batch_id=plan.id,
            assignment_item_id=item.id,
            task_date=task_date,
            subject=item.subject,
            title=f"{item.subject}：完成 {round(per_chunk, 2)}{item.unit}",
            task_type=item.task_type,
            submit_type=item.submit_type,
            planned_quantity=per_chunk,
            unit=item.unit,
            estimated_minutes=max(int(item.estimated_minutes_total / chunks), 10),
        ))


def _apply_item_adjustments(db: Session, plan_id: int, adjustments: list[dict]) -> None:
    for adjustment in adjustments:
        item_id = adjustment.get("id")
        if not item_id:
            continue
        item = db.get(AssignmentItem, item_id)
        if not item or item.assignment_batch_id != plan_id:
            continue
        pass


def confirm_plan(db: Session, plan_id: int, adjustments: list[dict] | None = None) -> AssignmentBatch:
    plan = db.get(AssignmentBatch, plan_id)
    if not plan:
        raise ValueError("Plan not found")
    _apply_item_adjustments(db, plan.id, adjustments or [])
    plan.status = "active"
    db.query(AssignmentItem).filter(AssignmentItem.assignment_batch_id == plan.id).update({"status": "confirmed"})
    if plan.import_batch_id:
        batch = db.get(ImportBatch, plan.import_batch_id)
        if batch:
            batch.status = "confirmed"
    db.commit()
    db.refresh(plan)
    return plan
