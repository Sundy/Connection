from sqlalchemy.orm import Session

from backend.app.models import ImportBatch, Student, User
from backend.app.services.access_service import can_access_student


class ImportAccessError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def require_import_batch_access(
    db: Session,
    user: User,
    batch_id: int,
) -> ImportBatch:
    batch = db.get(ImportBatch, batch_id)
    if not batch:
        raise ImportAccessError(404, "Import batch not found")

    student = db.get(Student, batch.student_id)
    if not student:
        raise ImportAccessError(404, "Import batch student not found")
    if not can_access_student(db, user, student):
        raise ImportAccessError(403, "Import batch access forbidden")
    return batch
