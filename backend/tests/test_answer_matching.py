import json
from dataclasses import dataclass
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from backend.app.core.database import engine
from backend.app.models import Family, ImportBatch, ImportFile, Student, User
from backend.app.services.answer_matching_service import (
    match_batch_answers,
    score_answer_match,
)


@dataclass
class MatchingDatabase:
    db: Session
    marker: str
    family: Family
    student: Student
    user: User

    def make_batch(self, suffix: str) -> ImportBatch:
        batch = ImportBatch(
            family_id=self.family.id,
            student_id=self.student.id,
            title=f"matching-{suffix}",
            created_by=self.user.id,
        )
        self.db.add(batch)
        self.db.flush()
        return batch

    def make_file(
        self,
        batch: ImportBatch,
        suffix: str,
        role: str,
        signature: dict,
        *,
        recognition_status: str = "success",
    ) -> ImportFile:
        import_file = ImportFile(
            import_batch_id=batch.id,
            file_name=f"{suffix}.jpg",
            file_url=f"https://example.invalid/{self.marker}/{suffix}.jpg",
            document_role=role,
            recognition_status=recognition_status,
            content_signature_json=json.dumps(signature, ensure_ascii=False),
        )
        self.db.add(import_file)
        self.db.flush()
        return import_file


def _cleanup_persisted_fixture(marker: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "DELETE import_files FROM import_files "
                "JOIN import_batches ON import_batches.id = import_files.import_batch_id "
                "JOIN users ON users.id = import_batches.created_by "
                "WHERE users.openid = :marker"
            ),
            {"marker": marker},
        )
        connection.execute(
            text(
                "DELETE import_batches FROM import_batches "
                "JOIN users ON users.id = import_batches.created_by "
                "WHERE users.openid = :marker"
            ),
            {"marker": marker},
        )
        connection.execute(
            text(
                "DELETE students FROM students "
                "JOIN users ON users.id = students.user_id "
                "WHERE users.openid = :marker"
            ),
            {"marker": marker},
        )
        connection.execute(
            text(
                "DELETE families FROM families "
                "JOIN users ON users.id = families.created_by "
                "WHERE users.openid = :marker"
            ),
            {"marker": marker},
        )
        connection.execute(
            text("DELETE FROM users WHERE openid = :marker"),
            {"marker": marker},
        )


@pytest.fixture
def matching_database():
    marker = f"answer-match-test-{uuid4()}"
    connection = engine.connect()
    outer_transaction = connection.begin()
    db = Session(bind=connection, join_transaction_mode="rollback_only")
    try:
        user = User(openid=marker, nickname="answer matching test")
        db.add(user)
        db.flush()
        family = Family(name="answer matching test", created_by=user.id)
        db.add(family)
        db.flush()
        student = Student(
            family_id=family.id,
            user_id=user.id,
            name="answer matching test",
            grade="五年级",
        )
        db.add(student)
        db.flush()

        yield MatchingDatabase(db, marker, family, student, user)
    finally:
        transaction_was_active = outer_transaction.is_active
        db.close()
        if outer_transaction.is_active:
            outer_transaction.rollback()
        connection.close()

        with engine.connect() as verification_connection:
            persisted_rows = verification_connection.scalar(
                select(func.count()).select_from(User).where(User.openid == marker)
            )
        if persisted_rows:
            _cleanup_persisted_fixture(marker)
        assert transaction_was_active, "service commit escaped the rollback-only test transaction"
        assert persisted_rows == 0, "matching fixture rows persisted after rollback"


def signature(
    subject: str,
    start: int,
    end: int,
    *,
    chapter: str = "第3单元",
    grade_hint: str = "五年级",
    keywords: list[str] | None = None,
    is_answer: bool | None = None,
) -> dict:
    value = {
        "subject": subject,
        "grade_hint": grade_hint,
        "chapter": chapter,
        "question_start": start,
        "question_end": end,
        "question_count": end - start + 1,
        "keywords": keywords or ["小数", "加减法"],
    }
    if is_answer is not None:
        value["is_answer"] = is_answer
    return value


def test_matching_subject_chapter_and_question_range_scores_high():
    score, reason = score_answer_match(
        {
            "subject": "数学",
            "chapter": "第3单元",
            "question_start": 1,
            "question_end": 20,
            "question_count": 20,
            "keywords": ["小数", "加减法"],
        },
        {
            "subject": "数学",
            "chapter": "第3单元",
            "question_start": 1,
            "question_end": 20,
            "question_count": 20,
            "keywords": ["小数", "加减法"],
            "is_answer": True,
        },
    )

    assert score >= 0.8
    assert "题号范围一致" in reason


def test_different_question_ranges_do_not_match():
    score, reason = score_answer_match(
        {"subject": "数学", "question_start": 21, "question_end": 40},
        {
            "subject": "数学",
            "question_start": 1,
            "question_end": 20,
            "is_answer": True,
        },
    )

    assert score < 0.8
    assert "题号范围不一致" in reason


def test_matches_only_the_compatible_answer_in_one_batch(matching_database):
    batch = matching_database.make_batch("normal")
    math_homework = matching_database.make_file(
        batch, "math-homework", "homework", signature("数学", 1, 20)
    )
    matching_database.make_file(
        batch, "english-homework", "homework", signature("英语", 1, 20)
    )
    matched_answer = matching_database.make_file(
        batch,
        "math-answer",
        "answer",
        signature("数学", 1, 20, is_answer=True),
    )
    unmatched_answer = matching_database.make_file(
        batch,
        "unmatched-answer",
        "answer",
        signature("数学", 21, 40, is_answer=True),
    )

    matched = match_batch_answers(matching_database.db, batch.id)

    assert [item.id for item in matched] == [matched_answer.id, unmatched_answer.id]
    assert matched_answer.match_status == "matched"
    assert matched_answer.matched_homework_file_id == math_homework.id
    assert unmatched_answer.match_status == "unmatched"
    assert unmatched_answer.matched_homework_file_id is None
    assert unmatched_answer.match_reason


def test_answer_without_recognized_homework_remains_pending(matching_database):
    batch = matching_database.make_batch("pending")
    answer = matching_database.make_file(
        batch,
        "early-answer",
        "answer",
        signature("数学", 1, 20, is_answer=True),
    )

    match_batch_answers(matching_database.db, batch.id)

    assert answer.match_status == "pending"
    assert answer.matched_homework_file_id is None
    assert answer.match_reason


def test_two_answers_competing_for_one_homework_assign_highest_score(matching_database):
    batch = matching_database.make_batch("competition")
    homework = matching_database.make_file(
        batch, "homework", "homework", signature("数学", 1, 20)
    )
    best_answer = matching_database.make_file(
        batch,
        "best-answer",
        "answer",
        signature("数学", 1, 20, is_answer=True),
    )
    lower_answer = matching_database.make_file(
        batch,
        "lower-answer",
        "answer",
        signature("数学", 1, 20, chapter="第4单元", is_answer=True),
    )

    match_batch_answers(matching_database.db, batch.id)

    assert best_answer.matched_homework_file_id == homework.id
    assert best_answer.match_status == "matched"
    assert lower_answer.matched_homework_file_id is None
    assert lower_answer.match_status == "unmatched"


def test_homework_occupied_by_another_batch_is_not_assigned_again(matching_database):
    current_batch = matching_database.make_batch("occupied-current")
    other_batch = matching_database.make_batch("occupied-other")
    homework = matching_database.make_file(
        current_batch, "occupied-homework", "homework", signature("数学", 1, 20)
    )
    occupying_answer = matching_database.make_file(
        other_batch,
        "occupying-answer",
        "answer",
        signature("数学", 1, 20, is_answer=True),
    )
    occupying_answer.match_status = "matched"
    occupying_answer.matched_homework_file_id = homework.id
    occupying_answer.match_confidence = 1.0
    occupying_answer.match_reason = "existing match"
    current_answer = matching_database.make_file(
        current_batch,
        "current-answer",
        "answer",
        signature("数学", 1, 20, is_answer=True),
    )
    matching_database.db.flush()

    match_batch_answers(matching_database.db, current_batch.id)

    assert current_answer.match_status == "unmatched"
    assert current_answer.matched_homework_file_id is None
    assert occupying_answer.match_status == "matched"
    assert occupying_answer.matched_homework_file_id == homework.id
    assert occupying_answer.match_reason == "existing match"


def test_homework_outside_current_batch_is_never_a_candidate(matching_database):
    current_batch = matching_database.make_batch("isolated-current")
    other_batch = matching_database.make_batch("isolated-other")
    answer = matching_database.make_file(
        current_batch,
        "current-answer",
        "answer",
        signature("数学", 1, 20, is_answer=True),
    )
    matching_database.make_file(
        other_batch, "other-homework", "homework", signature("数学", 1, 20)
    )

    match_batch_answers(matching_database.db, current_batch.id)

    assert answer.match_status == "pending"
    assert answer.matched_homework_file_id is None


def test_ambiguous_answer_stays_unmatched_before_stable_id_tiebreak(matching_database):
    batch = matching_database.make_batch("ambiguous")
    matching_database.make_file(
        batch, "first-homework", "homework", signature("数学", 1, 20)
    )
    matching_database.make_file(
        batch, "second-homework", "homework", signature("数学", 1, 20)
    )
    answer = matching_database.make_file(
        batch,
        "answer",
        "answer",
        signature("数学", 1, 20, is_answer=True),
    )

    match_batch_answers(matching_database.db, batch.id)

    assert answer.match_status == "unmatched"
    assert answer.matched_homework_file_id is None
    assert "歧义" in answer.match_reason


def test_exact_tenth_score_gap_is_not_ambiguous(matching_database):
    batch = matching_database.make_batch("exact-gap")
    best_homework = matching_database.make_file(
        batch, "best-homework", "homework", signature("数学", 1, 20)
    )
    matching_database.make_file(
        batch,
        "second-homework",
        "homework",
        signature("数学", 1, 20, grade_hint="六年级"),
    )
    answer = matching_database.make_file(
        batch,
        "answer",
        "answer",
        signature("数学", 1, 20, is_answer=True),
    )

    match_batch_answers(matching_database.db, batch.id)

    assert answer.match_status == "matched"
    assert answer.matched_homework_file_id == best_homework.id
