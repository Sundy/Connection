from sqlalchemy.orm import Session

from backend.app.models import Family, FamilyMember, Student, User


def login_or_create_user(db: Session, code: str, role: str) -> User:
    openid = f"mock-openid-{code}"
    user = db.query(User).filter(User.openid == openid).first()
    if user:
        return user

    user = User(openid=openid, role=role, nickname="家长" if role == "parent" else "学生")
    db.add(user)
    db.flush()

    family = Family(name=f"{user.nickname}的家庭", created_by=user.id)
    db.add(family)
    db.flush()
    db.add(FamilyMember(family_id=family.id, user_id=user.id, relation="guardian" if role == "parent" else "student"))

    if role == "parent":
        db.add(Student(family_id=family.id, name="默认学生", grade="四年级"))

    db.commit()
    db.refresh(user)
    return user


def get_user_context(db: Session, user: User) -> dict:
    member = db.query(FamilyMember).filter(
        FamilyMember.user_id == user.id,
        FamilyMember.status == "active",
    ).order_by(FamilyMember.id.desc()).first()
    family = db.get(Family, member.family_id) if member else None
    students = db.query(Student).filter(Student.family_id == family.id).all() if family else []
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
