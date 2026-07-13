from sqlalchemy.orm import Session

from backend.app.models import FamilyMember, Student, User


def can_access_student(db: Session, user: User, student: Student) -> bool:
    if student.user_id == user.id:
        return True
    return db.query(FamilyMember).filter(
        FamilyMember.user_id == user.id,
        FamilyMember.family_id == student.family_id,
        FamilyMember.status == "active",
    ).first() is not None
