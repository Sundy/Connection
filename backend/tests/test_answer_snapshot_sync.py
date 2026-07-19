import json
from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.app.core.database import SessionLocal
from backend.app.models import (
    AssignmentBatch,
    AssignmentItem,
    DailyTask,
    Family,
    ImportBatch,
    ImportFile,
    Student,
    User,
)
from backend.app.services.answer_matching_service import match_batch_answers
from backend.app.services.import_file_service import (
    StagedImportDeleteError,
    delete_staged_import_file,
)
from backend.app.services.planning_service import (
    confirm_plan,
    generate_plan_from_import,
)
from backend.app.services.task_payload_service import task_payload


@dataclass(frozen=True)
class SnapshotOwner:
    marker: str
    user_id: int
    family_id: int
    student_id: int


@pytest.fixture
def snapshot_owner():
    marker = f"answer-snapshot-{uuid4().hex}"
    with SessionLocal() as db:
        user = User(openid=marker, role="parent", nickname=marker)
        db.add(user)
        db.flush()
        family = Family(name=marker, created_by=user.id)
        db.add(family)
        db.flush()
        student = Student(
            family_id=family.id,
            user_id=user.id,
            name=marker,
            grade="五年级",
        )
        db.add(student)
        db.commit()
        owner = SnapshotOwner(marker, user.id, family.id, student.id)

    try:
        yield owner
    finally:
        with SessionLocal() as db:
            batch_ids = [
                row.id for row in db.query(ImportBatch.id).filter(
                    ImportBatch.created_by == owner.user_id
                )
            ]
            plan_ids = [
                row.id for row in db.query(AssignmentBatch.id).filter(
                    AssignmentBatch.student_id == owner.student_id
                )
            ]
            file_ids = [
                row.id for row in db.query(ImportFile.id).filter(
                    ImportFile.import_batch_id.in_(batch_ids)
                )
            ] if batch_ids else []
            item_ids = [
                row.id for row in db.query(AssignmentItem.id).filter(
                    AssignmentItem.assignment_batch_id.in_(plan_ids)
                )
            ] if plan_ids else []
            task_ids = [
                row.id for row in db.query(DailyTask.id).filter(
                    DailyTask.assignment_batch_id.in_(plan_ids)
                )
            ] if plan_ids else []

            if file_ids:
                db.query(ImportFile).filter(ImportFile.id.in_(file_ids)).update(
                    {"matched_homework_file_id": None},
                    synchronize_session=False,
                )
                db.flush()
            if task_ids:
                db.query(DailyTask).filter(DailyTask.id.in_(task_ids)).delete(
                    synchronize_session=False,
                )
                db.flush()
            if item_ids:
                db.query(AssignmentItem).filter(
                    AssignmentItem.id.in_(item_ids)
                ).delete(synchronize_session=False)
                db.flush()
            if plan_ids:
                db.query(AssignmentBatch).filter(
                    AssignmentBatch.id.in_(plan_ids)
                ).update(
                    {"target_assignment_batch_id": None},
                    synchronize_session=False,
                )
                db.flush()
                db.query(AssignmentBatch).filter(
                    AssignmentBatch.id.in_(plan_ids)
                ).delete(synchronize_session=False)
                db.flush()
            if file_ids:
                db.query(ImportFile).filter(ImportFile.id.in_(file_ids)).delete(
                    synchronize_session=False,
                )
                db.flush()
            if batch_ids:
                db.query(ImportBatch).filter(ImportBatch.id.in_(batch_ids)).delete(
                    synchronize_session=False,
                )
                db.flush()
            db.query(Student).filter(Student.id == owner.student_id).delete(
                synchronize_session=False
            )
            db.query(Family).filter(Family.id == owner.family_id).delete(
                synchronize_session=False
            )
            db.query(User).filter(User.id == owner.user_id).delete(
                synchronize_session=False
            )
            db.commit()

        with SessionLocal() as db:
            marker_rows = (
                db.query(User).filter(User.openid == marker).count()
                + db.query(ImportBatch).filter(
                    ImportBatch.title.contains(marker)
                ).count()
                + db.query(AssignmentBatch).filter(
                    AssignmentBatch.title.contains(marker)
                ).count()
                + db.query(ImportFile).filter(
                    ImportFile.file_name.contains(marker)
                ).count()
            )
            assert marker_rows == 0


def _signature(subject: str, chapter: str, *, is_answer: bool = False) -> str:
    return json.dumps({
        "subject": subject,
        "grade_hint": "五年级",
        "chapter": chapter,
        "question_start": 1,
        "question_end": 10,
        "question_count": 10,
        "keywords": [subject, chapter],
        "is_answer": is_answer,
    }, ensure_ascii=False)


def _make_batch(db, owner: SnapshotOwner, suffix: str) -> ImportBatch:
    batch = ImportBatch(
        family_id=owner.family_id,
        student_id=owner.student_id,
        title=f"{owner.marker}-{suffix}",
        created_by=owner.user_id,
        status="parsed",
        period_type="daily",
        start_date=date.today(),
        end_date=date.today(),
    )
    db.add(batch)
    db.flush()
    return batch


def _make_homework(
    db,
    owner: SnapshotOwner,
    batch: ImportBatch,
    suffix: str,
    subject: str,
    chapter: str,
) -> ImportFile:
    homework = ImportFile(
        import_batch_id=batch.id,
        file_name=f"{owner.marker}-{suffix}-misleading-answer-name.jpg",
        file_url="",
        extracted_text=f"{subject}{chapter}作业原文",
        parse_status="success",
        document_role="homework",
        recognized_title=f"{subject}{chapter}练习",
        recognition_status="success",
        match_status="not_required",
        content_signature_json=_signature(subject, chapter),
    )
    db.add(homework)
    db.flush()
    return homework


def _make_answer(
    db,
    owner: SnapshotOwner,
    batch: ImportBatch,
    suffix: str,
    subject: str,
    chapter: str,
    answer_text: str,
) -> ImportFile:
    answer = ImportFile(
        import_batch_id=batch.id,
        file_name=f"{owner.marker}-{suffix}-opaque.bin",
        file_url="",
        extracted_text=answer_text,
        parse_status="success",
        document_role="answer",
        recognition_status="success",
        content_signature_json=_signature(subject, chapter, is_answer=True),
    )
    db.add(answer)
    db.flush()
    return answer


def _file_item(db, plan_id: int, file_id: int) -> AssignmentItem:
    return db.query(AssignmentItem).filter(
        AssignmentItem.assignment_batch_id == plan_id,
        AssignmentItem.import_file_id == file_id,
    ).one()


def test_deleted_matched_answer_clears_snapshot_before_unblocked_confirmation(
    snapshot_owner,
):
    with SessionLocal() as db:
        batch = _make_batch(db, snapshot_owner, "delete-before-confirm")
        homework = _make_homework(
            db, snapshot_owner, batch, "homework", "数学", "第一单元"
        )
        answer = _make_answer(
            db,
            snapshot_owner,
            batch,
            "answer",
            "数学",
            "第一单元",
            "旧标准答案：1.A 2.B",
        )
        answer.match_status = "matched"
        answer.matched_homework_file_id = homework.id
        db.commit()
        batch_id = batch.id
        homework_id = homework.id
        answer_id = answer.id

        plan = generate_plan_from_import(db, batch_id)
        plan_id = plan.id
        item = _file_item(db, plan_id, homework_id)
        item_id = item.id
        task_id = db.query(DailyTask.id).filter(
            DailyTask.assignment_item_id == item_id
        ).scalar()
        assert item.answer_text == "旧标准答案：1.A 2.B"

        deleted_ids = delete_staged_import_file(
            db, db.get(User, snapshot_owner.user_id), answer_id
        )
        assert deleted_ids == [answer_id]
        assert db.get(AssignmentItem, item_id).answer_text is None

        db.get(AssignmentItem, item_id).answer_text = "确认前残留的旧标准答案"
        db.flush()
        confirmed = confirm_plan(db, plan_id)
        assert confirmed.status == "active"
        assert db.get(AssignmentItem, item_id).answer_text is None
        assert task_payload(db, db.get(DailyTask, task_id))["has_answer"] is False


def test_rematch_moves_snapshot_from_homework_a_to_homework_b(snapshot_owner):
    with SessionLocal() as db:
        batch = _make_batch(db, snapshot_owner, "rematch")
        homework_a = _make_homework(
            db, snapshot_owner, batch, "homework-a", "数学", "第一单元"
        )
        homework_b = _make_homework(
            db, snapshot_owner, batch, "homework-b", "英语", "第二单元"
        )
        answer = _make_answer(
            db,
            snapshot_owner,
            batch,
            "answer",
            "数学",
            "第一单元",
            "A 的旧答案",
        )
        answer.match_status = "matched"
        answer.matched_homework_file_id = homework_a.id
        db.commit()
        batch_id = batch.id
        homework_a_id = homework_a.id
        homework_b_id = homework_b.id
        answer_id = answer.id

        plan = generate_plan_from_import(db, batch_id)
        item_a = _file_item(db, plan.id, homework_a_id)
        item_b = _file_item(db, plan.id, homework_b_id)
        assert item_a.answer_text == "A 的旧答案"
        assert item_b.answer_text is None

        answer = db.get(ImportFile, answer_id)
        answer.extracted_text = "A 的当前修订答案"
        db.flush()
        regenerated = generate_plan_from_import(db, batch_id)
        assert regenerated.id == plan.id
        assert db.get(AssignmentItem, item_a.id).answer_text == "A 的当前修订答案"

        answer = db.get(ImportFile, answer_id)
        answer.extracted_text = "B 的当前答案"
        answer.content_signature_json = _signature(
            "英语", "第二单元", is_answer=True
        )
        db.flush()
        match_batch_answers(db, batch_id)

        assert db.get(ImportFile, answer_id).matched_homework_file_id == homework_b_id
        assert db.get(AssignmentItem, item_a.id).answer_text is None
        assert db.get(AssignmentItem, item_b.id).answer_text == "B 的当前答案"
        assert db.query(AssignmentItem).filter(
            AssignmentItem.assignment_batch_id == plan.id,
            AssignmentItem.answer_text.is_not(None),
        ).count() == 1


def test_snapshot_sync_never_mutates_active_or_historical_items(snapshot_owner):
    with SessionLocal() as db:
        batch = _make_batch(db, snapshot_owner, "history")
        homework = _make_homework(
            db, snapshot_owner, batch, "homework", "数学", "第三单元"
        )
        answer = _make_answer(
            db,
            snapshot_owner,
            batch,
            "answer",
            "数学",
            "第三单元",
            "当前答案",
        )
        answer.match_status = "matched"
        answer.matched_homework_file_id = homework.id
        plans = [
            AssignmentBatch(
                student_id=snapshot_owner.student_id,
                import_batch_id=batch.id,
                title=f"{snapshot_owner.marker}-{status}",
                status=status,
            )
            for status in ("pending_confirm", "active", "archived")
        ]
        db.add_all(plans)
        db.flush()
        items = [
            AssignmentItem(
                assignment_batch_id=plan.id,
                subject="数学",
                title=f"{plan.status} item",
                import_file_id=homework.id,
                answer_text=f"{plan.status} 旧快照",
            )
            for plan in plans
        ]
        db.add_all(items)
        db.commit()
        batch_id = batch.id
        pending_id, active_id, archived_id = [item.id for item in items]

        match_batch_answers(db, batch_id)

        assert db.get(AssignmentItem, pending_id).answer_text is None
        assert (
            db.get(AssignmentItem, active_id).answer_text
            == "active 旧快照"
        )
        assert (
            db.get(AssignmentItem, archived_id).answer_text
            == "archived 旧快照"
        )


def test_failed_delete_rematch_rolls_back_answer_snapshot(snapshot_owner, monkeypatch):
    with SessionLocal() as db:
        batch = _make_batch(db, snapshot_owner, "compensation")
        homework = _make_homework(
            db, snapshot_owner, batch, "homework", "数学", "第四单元"
        )
        first_answer = _make_answer(
            db,
            snapshot_owner,
            batch,
            "first-answer",
            "数学",
            "第四单元",
            "事务前答案",
        )
        second_answer = _make_answer(
            db,
            snapshot_owner,
            batch,
            "second-answer",
            "数学",
            "第四单元",
            "不得泄漏的重匹配答案",
        )
        first_answer.match_status = "matched"
        first_answer.matched_homework_file_id = homework.id
        second_answer.match_status = "unmatched"
        db.commit()
        batch_id = batch.id
        homework_id = homework.id
        first_answer_id = first_answer.id
        second_answer_id = second_answer.id

        plan = generate_plan_from_import(db, batch_id)
        item = _file_item(db, plan.id, homework_id)
        item_id = item.id
        assert item.answer_text == "事务前答案"

        def fail_commit():
            raise RuntimeError("forced snapshot commit failure")

        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(
            StagedImportDeleteError, match="forced snapshot commit failure"
        ):
            delete_staged_import_file(
                db,
                db.get(User, snapshot_owner.user_id),
                first_answer_id,
            )
        monkeypatch.undo()

    with SessionLocal() as verification_db:
        assert verification_db.get(ImportFile, first_answer_id) is not None
        assert (
            verification_db.get(ImportFile, first_answer_id).matched_homework_file_id
            == homework_id
        )
        assert verification_db.get(ImportFile, second_answer_id).match_status == "unmatched"
        assert verification_db.get(AssignmentItem, item_id).answer_text == "事务前答案"
