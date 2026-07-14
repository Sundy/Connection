from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.api.deps import get_current_user
from backend.app.core.database import get_db
from backend.app.core.responses import ok
from backend.app.models import Family, FamilyMember, Student, User
from backend.app.schemas.requests import FamilyJoinIn
from backend.app.services.auth_service import get_user_context

router = APIRouter(prefix="/families", tags=["families"])


def _encode_invite_code(family_id: int) -> str:
    return f"FAM-{family_id:06d}"


def _decode_invite_code(invite_code: str) -> int:
    normalized = invite_code.strip().upper()
    if not normalized.startswith("FAM-"):
        raise HTTPException(status_code=400, detail="Invalid family invite code")
    try:
        return int(normalized.replace("FAM-", "", 1))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid family invite code") from exc


def _active_member(db: Session, user_id: int) -> FamilyMember | None:
    return db.query(FamilyMember).filter(
        FamilyMember.user_id == user_id,
        FamilyMember.status == "active",
    ).order_by(FamilyMember.id.desc()).first()


@router.post("/invite-code")
def create_invite_code(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    member = _active_member(db, user.id)
    if not member:
        raise HTTPException(status_code=404, detail="Current user has no active family")

    family = db.get(Family, member.family_id)
    if not family:
        raise HTTPException(status_code=404, detail="Family not found")

    return ok({"family_id": family.id, "invite_code": _encode_invite_code(family.id)})


@router.post("/join")
def join_family(payload: FamilyJoinIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role == "parent":
        raise HTTPException(status_code=400, detail="Parents should share invite code with students instead")

    family_id = _decode_invite_code(payload.invite_code)
    family = db.get(Family, family_id)
    if not family:
        raise HTTPException(status_code=404, detail="Family invite code not found")

    relation = "student"
    active_memberships = db.query(FamilyMember).filter(
        FamilyMember.user_id == user.id,
        FamilyMember.status == "active",
    ).all()
    target_member = next((member for member in active_memberships if member.family_id == family.id), None)

    if not target_member:
        for member in active_memberships:
            member.status = "inactive"
        db.add(FamilyMember(family_id=family.id, user_id=user.id, relation=relation))
    else:
        target_member.relation = relation

    if payload.student_id:
        student = db.get(Student, payload.student_id)
        if not student or student.family_id != family.id:
            raise HTTPException(status_code=404, detail="Student profile not found in this family")
        if student.user_id and student.user_id != user.id:
            raise HTTPException(status_code=409, detail="Student profile is already bound")
        student.user_id = user.id
        student.name = user.nickname
    else:
        unbound_student = db.query(Student).filter(
            Student.family_id == family.id,
            Student.user_id.is_(None),
        ).order_by(Student.id).first()
        if unbound_student:
            unbound_student.user_id = user.id
            unbound_student.name = user.nickname
        else:
            db.add(Student(family_id=family.id, user_id=user.id, name=user.nickname, grade=""))

    db.commit()
    db.refresh(user)
    return ok(get_user_context(db, user))
