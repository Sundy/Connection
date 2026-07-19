from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import AssignmentBatch, AssignmentItem, ImportFile


def sync_pending_file_answer_snapshots(
    db: Session,
    batch_id: int,
    batch_files: Sequence[ImportFile],
    *,
    locked_plans: Sequence[AssignmentBatch] | None = None,
    locked_items: Sequence[AssignmentItem] | None = None,
) -> list[AssignmentItem]:
    """Mirror current answer matches into locked, unconfirmed file items."""
    if locked_plans is None:
        locked_plans = list(db.scalars(
            select(AssignmentBatch)
            .where(
                AssignmentBatch.import_batch_id == batch_id,
                AssignmentBatch.status == "pending_confirm",
            )
            .order_by(AssignmentBatch.id)
            .execution_options(populate_existing=True)
            .with_for_update()
        ))
    pending_plan_ids = {
        plan.id
        for plan in locked_plans
        if plan.import_batch_id == batch_id
        and plan.status == "pending_confirm"
    }
    if not pending_plan_ids:
        db.flush()
        return []

    if locked_items is None:
        locked_items = list(db.scalars(
            select(AssignmentItem)
            .where(
                AssignmentItem.assignment_batch_id.in_(pending_plan_ids),
                AssignmentItem.import_file_id.is_not(None),
            )
            .order_by(AssignmentItem.id)
            .execution_options(populate_existing=True)
            .with_for_update()
        ))

    homework_ids = {
        item.id
        for item in batch_files
        if (item.document_role or "homework") == "homework"
    }
    answers_by_homework: dict[int, list[ImportFile]] = {}
    for item in batch_files:
        homework_id = item.matched_homework_file_id
        if (
            item.document_role == "answer"
            and item.match_status == "matched"
            and homework_id in homework_ids
        ):
            answers_by_homework.setdefault(homework_id, []).append(item)

    synced_items = [
        item
        for item in locked_items
        if item.assignment_batch_id in pending_plan_ids
        and item.import_file_id is not None
    ]
    for item in synced_items:
        matches = answers_by_homework.get(item.import_file_id, [])
        item.answer_text = matches[0].extracted_text if len(matches) == 1 else None
    db.flush()
    return synced_items
