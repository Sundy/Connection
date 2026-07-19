from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import ImportBatch, ImportFile, Student


def lock_student(db: Session, student_id: int) -> Student | None:
    return db.scalar(
        select(Student)
        .where(Student.id == student_id)
        .with_for_update()
    )


def lock_import_batch_files(
    db: Session,
    batch_id: int,
) -> tuple[ImportBatch | None, list[ImportFile]]:
    batch = db.scalar(
        select(ImportBatch)
        .where(ImportBatch.id == batch_id)
        .with_for_update()
    )
    files = list(db.scalars(
        select(ImportFile)
        .where(ImportFile.import_batch_id == batch_id)
        .order_by(ImportFile.id)
        .with_for_update()
    ))
    return batch, files
