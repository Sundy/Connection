from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.api.deps import get_current_user
from backend.app.core.database import get_db
from backend.app.core.responses import ok
from backend.app.models import User
from backend.app.schemas.requests import WechatLoginIn
from backend.app.services.auth_service import get_user_context, login_or_create_user

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/wechat-login")
def wechat_login(payload: WechatLoginIn, db: Session = Depends(get_db)):
    user = login_or_create_user(db, payload.code, payload.role)
    return ok({"token": f"dev-token-{user.id}", "user": {"id": user.id, "role": user.role, "nickname": user.nickname}})


@router.get("/me")
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return ok(get_user_context(db, user))
