from sqlalchemy.orm import Session

from fastapi import HTTPException

from backend.app.models import Family, FamilyMember, Student, User
from backend.app.schemas.requests import ProfileUpdateIn


def _active_member(db: Session, user_id: int) -> FamilyMember | None:
    return db.query(FamilyMember).filter(
        FamilyMember.user_id == user_id,
        FamilyMember.status == "active",
    ).order_by(FamilyMember.id.desc()).first()


def _create_default_family_for_parent(db: Session, user: User) -> None:
    family = Family(name=f"{user.nickname}的家庭", created_by=user.id)
    db.add(family)
    db.flush()
    db.add(FamilyMember(family_id=family.id, user_id=user.id, relation="guardian"))


def login_or_create_user(db: Session, code: str, role: str, client_openid: str | None = None) -> User:
    openid = client_openid or f"mock-openid-{code}"
    user = db.query(User).filter(User.openid == openid).first()
    if user:
        user.role = role
        if role == "parent" and not _active_member(db, user.id):
            _create_default_family_for_parent(db, user)
        db.commit()
        db.refresh(user)
        return user

    user = User(openid=openid, role=role, nickname="家长" if role == "parent" else "学生")
    db.add(user)
    db.flush()

    if role == "parent":
        _create_default_family_for_parent(db, user)

    db.commit()
    db.refresh(user)
    return user


def get_user_context(db: Session, user: User) -> dict:
    member = _active_member(db, user.id)
    family = db.get(Family, member.family_id) if member else None
    students = db.query(Student).filter(
        Student.family_id == family.id,
        Student.user_id.is_not(None),
    ).all() if family else []
    members = db.query(FamilyMember).filter(
        FamilyMember.family_id == family.id,
        FamilyMember.status == "active",
    ).all() if family else []
    return {
        "user": {"id": user.id, "role": user.role, "nickname": user.nickname},
        "family": {"id": family.id, "name": family.name} if family else None,
        "students": [{"id": s.id, "user_id": s.user_id, "name": s.name, "grade": s.grade, "school": s.school} for s in students],
        "members": [{"id": m.id, "user_id": m.user_id, "relation": m.relation} for m in members],
    }


def update_user_profile(db: Session, user: User, payload: ProfileUpdateIn) -> dict:
    nickname = payload.nickname.strip()
    if not nickname:
        raise HTTPException(status_code=400, detail="Nickname is required")

    user.nickname = nickname

    if user.role == "student":
        student = db.query(Student).filter(Student.user_id == user.id).order_by(Student.id.desc()).first()
        if student:
            student.name = nickname
            student.grade = (payload.grade or "").strip()
            student.school = (payload.school or "").strip() or None

    db.commit()
    db.refresh(user)
    return get_user_context(db, user)
