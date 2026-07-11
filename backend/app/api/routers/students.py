from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.api.deps import get_current_user
from backend.app.core.database import get_db
from backend.app.core.responses import ok
from backend.app.models import FamilyMember, Student, User
from backend.app.schemas.requests import StudentCreateIn

router = APIRouter(prefix="/students", tags=["students"])


def _active_family_member(db: Session, user_id: int) -> FamilyMember | None:
    return db.query(FamilyMember).filter(
        FamilyMember.user_id == user_id,
        FamilyMember.status == "active",
    ).order_by(FamilyMember.id.desc()).first()


@router.get("")
def list_students(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    member = _active_family_member(db, user.id)
    students = db.query(Student).filter(Student.family_id == member.family_id).all() if member else []
    return ok([{"id": s.id, "name": s.name, "grade": s.grade, "school": s.school} for s in students])


@router.post("")
def create_student(payload: StudentCreateIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    member = _active_family_member(db, user.id)
    if not member:
        raise HTTPException(status_code=404, detail="Current user has no active family")
    student = Student(family_id=member.family_id, name=payload.name, grade=payload.grade, school=payload.school)
    db.add(student)
    db.commit()
    db.refresh(student)
    return ok({"id": student.id, "name": student.name, "grade": student.grade, "school": student.school})


@router.post("/select")
def select_student(payload: dict):
    return ok({"student_id": payload.get("student_id")})
