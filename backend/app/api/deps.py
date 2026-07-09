from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.models import User


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not authorization:
        user = db.query(User).order_by(User.id).first()
        if user:
            return user
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    token = authorization.replace("Bearer ", "")
    if not token.startswith("dev-token-"):
        raise HTTPException(status_code=401, detail="Invalid token")

    user_id = int(token.replace("dev-token-", ""))
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user
