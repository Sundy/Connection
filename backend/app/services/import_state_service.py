from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import AssignmentBatch, ImportBatch, ImportFile
from backend.app.services.import_lock_service import (
    lock_import_batch_files,
    lock_student,
)


IMMUTABLE_IMPORT_DETAIL = {
    "code": "import_batch_immutable",
    "message": "该批作业已确认，不能再修改",
}


class ImportBatchImmutableError(Exception):
    def __init__(self) -> None:
        super().__init__(IMMUTABLE_IMPORT_DETAIL["message"])
        self.detail = IMMUTABLE_IMPORT_DETAIL


def lock_mutable_import_batch(
    db: Session,
    batch_id: int,
) -> tuple[ImportBatch | None, list[ImportFile], list[AssignmentBatch]]:
    batch, files = lock_import_batch_files(db, batch_id)
    if not batch:
        return None, files, []
    lock_student(db, batch.student_id)
    plans = list(db.scalars(
        select(AssignmentBatch)
        .where(AssignmentBatch.import_batch_id == batch_id)
        .order_by(AssignmentBatch.id)
        .execution_options(populate_existing=True)
        .with_for_update()
    ))
    if batch.status == "confirmed" or any(
        plan.status != "pending_confirm" for plan in plans
    ):
        raise ImportBatchImmutableError()
    return batch, files, plans


def import_batch_read_state(
    db: Session,
    batch: ImportBatch,
) -> tuple[bool, int | None]:
    plans = db.query(AssignmentBatch).filter(
        AssignmentBatch.import_batch_id == batch.id
    ).order_by(AssignmentBatch.id).all()
    immutable = batch.status == "confirmed" or any(
        plan.status != "pending_confirm" for plan in plans
    )
    canonical_plan_id = None
    for plan in plans:
        if plan.status == "active":
            canonical_plan_id = plan.id
            break
        if plan.status == "merged" and plan.target_assignment_batch_id:
            target = db.get(AssignmentBatch, plan.target_assignment_batch_id)
            if (
                target
                and target.status == "active"
                and target.student_id == plan.student_id
                and target.period_type == plan.period_type
                and target.start_date == plan.start_date
                and target.end_date == plan.end_date
            ):
                canonical_plan_id = target.id
                break
    return not immutable, canonical_plan_id
