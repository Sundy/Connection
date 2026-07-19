from sqlalchemy.orm import Session

from backend.app.models import (
    AssignmentBatch,
    DailyTask,
    FamilyMember,
    Notification,
    Student,
    Submission,
    User,
)


def create_notification(
    db: Session,
    *,
    user_id: int,
    student_id: int | None,
    notification_type: str,
    title: str,
    content: str,
) -> Notification:
    notification = Notification(
        user_id=user_id,
        student_id=student_id,
        type=notification_type,
        title=title,
        content=content,
        status="pending",
    )
    db.add(notification)
    return notification


def notify_assignment_updated(db: Session, plan: AssignmentBatch) -> None:
    student = db.get(Student, plan.student_id)
    if not student or not student.user_id:
        return
    create_notification(
        db,
        user_id=student.user_id,
        student_id=student.id,
        notification_type="assignment_updated",
        title="作业已更新",
        content=f"{plan.title} 已确认，今日任务已更新。",
    )


def notify_submission_uploaded(db: Session, submission: Submission, task: DailyTask) -> None:
    student = db.get(Student, submission.student_id)
    if not student:
        return
    guardian_user_ids = [
        row.user_id
        for row in db.query(FamilyMember)
        .join(User, FamilyMember.user_id == User.id)
        .filter(
            FamilyMember.family_id == student.family_id,
            FamilyMember.status == "active",
            FamilyMember.relation == "guardian",
            User.role == "parent",
        )
        .all()
    ]
    for user_id in guardian_user_ids:
        create_notification(
            db,
            user_id=user_id,
            student_id=student.id,
            notification_type="submission_uploaded",
            title="学生已提交作业",
            content=f"{student.name} 已提交 {task.title}，家长端进度已更新。",
        )
