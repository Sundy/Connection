from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.api.deps import get_current_user
from backend.app.core.database import get_db
from backend.app.core.responses import ok
from backend.app.models import Notification, User

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
def list_notifications(
    status: str | None = None,
    student_id: int | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(Notification).filter(Notification.user_id == user.id)
    if status:
        query = query.filter(Notification.status == status)
    if student_id:
        query = query.filter(Notification.student_id == student_id)
    rows = query.order_by(Notification.id.desc()).all()
    return ok([
        {
            "id": item.id,
            "student_id": item.student_id,
            "type": item.type,
            "title": item.title,
            "content": item.content,
            "status": item.status,
            "created_at": item.created_at.isoformat() if item.created_at else None,
        }
        for item in rows
    ])


@router.post("/{notification_id}/read")
def read(
    notification_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    item = db.get(Notification, notification_id)
    if not item or item.user_id != user.id:
        raise HTTPException(status_code=404, detail="Notification not found")
    item.status = "read"
    db.commit()
    return ok({"id": notification_id, "status": "read"})
