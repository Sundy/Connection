from datetime import date, timedelta
import math
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import (
    AssignmentBatch,
    AssignmentItem,
    DailyTask,
    ImportBatch,
    ImportFile,
    Student,
    User,
)
from backend.app.services.access_service import can_access_student
from backend.app.services.import_file_service import (
    StagedImportDeleteError,
    delete_staged_import_file,
)
from backend.app.services.import_lock_service import (
    lock_import_batch_files,
    lock_student,
)
from backend.app.services.answer_snapshot_service import (
    sync_pending_file_answer_snapshots,
)
from backend.app.services.llm_service import extract_assignment_items_with_llm


SUBJECTS = ["数学", "语文", "英语", "物理", "化学", "科学", "阅读", "口语"]
SUBJECT_KEYWORDS = {
    "数学": ["口算", "计算", "应用题", "竖式", "数学题", "卷子"],
    "语文": ["课文", "作文", "阅读", "摘抄", "生字", "古诗", "背诵"],
    "英语": ["单词", "英语", "口语", "听力", "默写", "朗读"],
    "科学": ["实验", "科学"],
}


class PlanConfirmationBlocked(Exception):
    def __init__(self, blockers: list[dict]) -> None:
        super().__init__("Plan confirmation is blocked")
        self.blockers = blockers


class PlanStateConflict(Exception):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def infer_subject(text: str, file_name: str = "") -> str:
    haystack = f"{file_name}\n{text}"
    for subject in SUBJECTS:
        if subject in haystack:
            return subject
        if any(keyword in haystack for keyword in SUBJECT_KEYWORDS.get(subject, [])):
            return subject
    return "综合"


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
        title = (file.recognized_title or "").strip()
        subject = infer_subject(text, title)
        task_type = "recitation" if any(keyword in text for keyword in ["背", "朗读", "口语"]) else "written"
        items.append({
            "subject": subject,
            "title": title,
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
    batch, batch_files = lock_import_batch_files(db, batch_id)
    if not batch:
        raise ValueError("Import batch not found")
    if not lock_student(db, batch.student_id):
        raise ValueError("Import batch student not found")

    homework_files = [
        item for item in batch_files
        if (item.document_role or "homework") == "homework"
        and item.parse_status == "success"
        and item.recognition_status == "success"
        and bool((item.recognized_title or "").strip())
    ]
    text = batch.merged_text or batch.raw_text or ""
    plan = db.scalar(
        select(AssignmentBatch)
        .where(AssignmentBatch.import_batch_id == batch.id)
        .order_by(AssignmentBatch.id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if plan:
        if plan.status != "pending_confirm":
            return plan
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
    target = find_active_merge_target(db, plan, lock=True)
    plan.target_assignment_batch_id = target.id if target else None

    locked_plan_ids = [plan.id] + ([target.id] if target else [])
    locked_items = list(db.scalars(
        select(AssignmentItem)
        .where(AssignmentItem.assignment_batch_id.in_(locked_plan_ids))
        .order_by(AssignmentItem.id)
        .execution_options(populate_existing=True)
        .with_for_update()
    ))
    list(db.scalars(
        select(DailyTask)
        .where(DailyTask.assignment_batch_id.in_(locked_plan_ids))
        .order_by(DailyTask.id)
        .execution_options(populate_existing=True)
        .with_for_update()
    ))
    existing_items = [
        item for item in locked_items if item.assignment_batch_id == plan.id
    ]
    items_by_file_id = {
        item.import_file_id: item
        for item in existing_items
        if item.import_file_id is not None
    }

    if batch_files:
        for file in homework_files:
            existing = items_by_file_id.get(file.id)
            if existing:
                continue
            item_data = extract_items_from_files([file])[0]
            item_data["answer_text"] = None
            item = AssignmentItem(
                assignment_batch_id=plan.id,
                status="draft",
                **item_data,
            )
            db.add(item)
            db.flush()
            existing_items.append(item)
            create_daily_tasks(db, plan, item, len(existing_items) - 1)
    elif not any(item.import_file_id is None for item in existing_items):
        for index, item_data in enumerate(extract_items(text)):
            item = AssignmentItem(
                assignment_batch_id=plan.id,
                status="draft",
                **item_data,
            )
            db.add(item)
            db.flush()
            existing_items.append(item)
            create_daily_tasks(db, plan, item, index)

    db.flush()
    sync_pending_file_answer_snapshots(
        db,
        batch.id,
        batch_files,
        locked_plans=[plan] + ([target] if target else []),
        locked_items=locked_items + [
            item for item in existing_items if item not in locked_items
        ],
    )

    plan.total_estimated_minutes = sum(
        item.estimated_minutes_total for item in existing_items
    )
    batch.status = "pending_confirm"
    db.commit()
    db.refresh(plan)
    return plan


def create_daily_tasks(db: Session, plan: AssignmentBatch, item: AssignmentItem, day_offset: int = 0) -> None:
    start = plan.start_date or date.today()
    end = plan.end_date or (start + timedelta(days=14))
    days = max((end - start).days + 1, 1)
    if item.unit == "份" and item.total_quantity == 1:
        db.add(DailyTask(
            student_id=plan.student_id,
            assignment_batch_id=plan.id,
            assignment_item_id=item.id,
            task_date=start,
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


def plan_confirmation_blockers(
    db: Session,
    plan: AssignmentBatch,
) -> list[dict]:
    if not plan.import_batch_id:
        return []
    files = db.query(ImportFile).filter(
        ImportFile.import_batch_id == plan.import_batch_id,
    ).order_by(ImportFile.id).all()
    homework_ids = {
        item.id
        for item in files
        if (item.document_role or "homework") == "homework"
    }
    blockers: list[dict] = []
    for item in files:
        role = item.document_role or "homework"
        if (
            (item.parse_claim_token or "").strip()
            or item.parse_status in {None, "", "pending", "queued", "processing"}
        ):
            blockers.append({
                "code": "file_processing",
                "file_id": item.id,
                "message": "文件正在处理，请稍后确认",
            })
            continue
        if role == "homework":
            if (
                item.parse_status != "success"
                or item.recognition_status == "failed"
                or not (item.recognized_title or "").strip()
            ):
                blockers.append({
                    "code": "homework_title_unrecognized",
                    "file_id": item.id,
                    "message": "作业标题尚未识别",
                })
            elif item.recognition_status in {None, "", "pending", "queued", "processing"}:
                blockers.append({
                    "code": "file_processing",
                    "file_id": item.id,
                    "message": "文件正在处理，请稍后确认",
                })
            continue

        if (
            item.parse_status != "success"
            or item.recognition_status == "failed"
        ):
            blockers.append({
                "code": "answer_pending",
                "file_id": item.id,
                "message": "答案识别失败，请重新处理",
            })
        elif item.recognition_status in {None, "", "pending", "queued", "processing"}:
            blockers.append({
                "code": "answer_pending",
                "file_id": item.id,
                "message": "答案正在识别或匹配",
            })
        elif item.match_status in {None, "", "pending", "queued", "processing"}:
            blockers.append({
                "code": "answer_pending",
                "file_id": item.id,
                "message": "答案正在匹配",
            })
        elif item.match_status == "unmatched":
            blockers.append({
                "code": "answer_unmatched",
                "file_id": item.id,
                "message": "答案未匹配到当前作业",
            })
        elif (
            item.match_status != "matched"
            or item.matched_homework_file_id not in homework_ids
        ):
            blockers.append({
                "code": "answer_match_conflict",
                "file_id": item.id,
                "message": "答案匹配存在冲突",
            })
    return blockers


def find_active_merge_target(
    db: Session,
    staging_plan: AssignmentBatch,
    lock: bool = False,
) -> AssignmentBatch | None:
    statement = (
        select(AssignmentBatch)
        .where(
            AssignmentBatch.student_id == staging_plan.student_id,
            AssignmentBatch.period_type == staging_plan.period_type,
            AssignmentBatch.start_date == staging_plan.start_date,
            AssignmentBatch.end_date == staging_plan.end_date,
            AssignmentBatch.status == "active",
            AssignmentBatch.id != staging_plan.id,
        )
        .order_by(AssignmentBatch.id)
    )
    if lock:
        statement = statement.execution_options(
            populate_existing=True
        ).with_for_update()
    return db.scalars(statement).first()


def delete_staged_assignment_item(
    db: Session,
    user: User,
    plan_id: int,
    item_id: int,
) -> list[int]:
    plan = db.get(AssignmentBatch, plan_id)
    if not plan:
        raise StagedImportDeleteError(404, "Plan not found")
    student = db.get(Student, plan.student_id)
    if not student:
        raise StagedImportDeleteError(404, "Plan student not found")
    if not can_access_student(db, user, student):
        raise StagedImportDeleteError(403, "Plan access forbidden")
    item = db.get(AssignmentItem, item_id)
    if not item or item.assignment_batch_id != plan.id:
        raise StagedImportDeleteError(404, "Assignment item not found")
    if plan.status != "pending_confirm":
        raise StagedImportDeleteError(409, "Only pending draft items can be deleted")
    if not item.import_file_id:
        raise StagedImportDeleteError(409, "Draft item has no staged import file")
    return delete_staged_import_file(db, user, item.import_file_id)


def confirm_plan(db: Session, plan_id: int, adjustments: list[dict] | None = None) -> AssignmentBatch:
    plan = db.get(AssignmentBatch, plan_id)
    if not plan:
        raise ValueError("Plan not found")
    batch_files: list[ImportFile] = []
    if plan.import_batch_id:
        _batch, batch_files = lock_import_batch_files(db, plan.import_batch_id)
    if not lock_student(db, plan.student_id):
        raise ValueError("Plan student not found")
    plan = db.scalar(
        select(AssignmentBatch)
        .where(AssignmentBatch.id == plan_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if not plan:
        raise ValueError("Plan not found")
    if plan.status == "active":
        return plan
    if plan.status == "merged":
        target = db.scalar(
            select(AssignmentBatch)
            .where(AssignmentBatch.id == plan.target_assignment_batch_id)
            .execution_options(populate_existing=True)
            .with_for_update()
        ) if plan.target_assignment_batch_id else None
        if (
            target
            and target.status == "active"
            and target.student_id == plan.student_id
            and target.period_type == plan.period_type
            and target.start_date == plan.start_date
            and target.end_date == plan.end_date
        ):
            return target
        raise PlanStateConflict("Merged plan has no valid active target")
    if plan.status != "pending_confirm":
        raise PlanStateConflict(
            f"Plan status {plan.status!r} cannot be confirmed"
        )

    target = find_active_merge_target(db, plan, lock=True)
    plan.target_assignment_batch_id = target.id if target else None
    locked_plan_ids = [plan.id] + ([target.id] if target else [])
    staging_items = list(db.scalars(
        select(AssignmentItem)
        .where(AssignmentItem.assignment_batch_id.in_(locked_plan_ids))
        .order_by(AssignmentItem.id)
        .execution_options(populate_existing=True)
        .with_for_update()
    ))
    if plan.import_batch_id:
        db.flush()
        sync_pending_file_answer_snapshots(
            db,
            plan.import_batch_id,
            batch_files,
            locked_plans=[plan] + ([target] if target else []),
            locked_items=staging_items,
        )
    locked_tasks = list(db.scalars(
        select(DailyTask)
        .where(DailyTask.assignment_batch_id.in_(locked_plan_ids))
        .order_by(DailyTask.id)
        .execution_options(populate_existing=True)
        .with_for_update()
    ))
    staging_items = [
        item for item in staging_items if item.assignment_batch_id == plan.id
    ]
    tasks = [
        task for task in locked_tasks if task.assignment_batch_id == plan.id
    ]
    blockers = plan_confirmation_blockers(db, plan)
    if blockers:
        raise PlanConfirmationBlocked(blockers)
    _apply_item_adjustments(db, plan.id, adjustments or [])
    start = plan.start_date or date.today()
    if tasks and not any(task.task_date == start for task in tasks):
        earliest_date = min(task.task_date for task in tasks)
        for task in tasks:
            if task.task_date == earliest_date:
                task.task_date = start
    for item in staging_items:
        item.status = "confirmed"
    if target:
        for item in staging_items:
            item.assignment_batch_id = target.id
        for task in tasks:
            task.assignment_batch_id = target.id
        target.total_estimated_minutes += plan.total_estimated_minutes
        plan.status = "merged"
        final_plan = target
    else:
        plan.status = "active"
        final_plan = plan
    if plan.import_batch_id:
        batch = db.get(ImportBatch, plan.import_batch_id)
        if batch:
            batch.status = "confirmed"
    db.commit()
    db.refresh(final_plan)
    return final_plan
