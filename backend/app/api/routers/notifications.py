from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.api.deps import get_current_user
from backend.app.core.database import get_db
from backend.app.core.responses import ok
from backend.app.models import Notification, User

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
def list_notifications(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(Notification).filter(Notification.user_id == user.id).order_by(Notification.id.desc()).all()
    return ok([{"id": item.id, "type": item.type, "title": item.title, "content": item.content, "status": item.status} for item in rows])


@router.post("/{notification_id}/read")
def read(notification_id: int, db: Session = Depends(get_db)):
    item = db.get(Notification, notification_id)
    if item:
        item.status = "read"
        db.commit()
    return ok({"id": notification_id, "status": "read"})
