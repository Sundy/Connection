import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.models import ImportFile


MATCH_WEIGHTS = {
    "subject": 0.25,
    "grade_hint": 0.10,
    "chapter": 0.15,
    "question_range": 0.25,
    "question_count": 0.10,
    "keywords": 0.15,
}

_CONFLICT_SCORE_CAP = 0.79


def _normalized_text(value: Any) -> str:
    return str(value or "").strip().casefold()


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def score_answer_match(
    homework_signature: dict, answer_signature: dict
) -> tuple[float, str]:
    score = 0.0
    reasons: list[str] = []
    has_hard_conflict = answer_signature.get("is_answer") is not True

    for feature, label in (
        ("subject", "学科"),
        ("grade_hint", "年级"),
        ("chapter", "章节"),
    ):
        homework_value = _normalized_text(homework_signature.get(feature))
        answer_value = _normalized_text(answer_signature.get(feature))
        if homework_value and answer_value:
            if homework_value == answer_value:
                score += MATCH_WEIGHTS[feature]
                reasons.append(f"{label}一致")
            else:
                reasons.append(f"{label}不一致")
                if feature == "subject":
                    has_hard_conflict = True

    homework_start = _integer(homework_signature.get("question_start"))
    homework_end = _integer(homework_signature.get("question_end"))
    answer_start = _integer(answer_signature.get("question_start"))
    answer_end = _integer(answer_signature.get("question_end"))
    if None not in (homework_start, homework_end, answer_start, answer_end):
        if (homework_start, homework_end) == (answer_start, answer_end):
            score += MATCH_WEIGHTS["question_range"]
            reasons.append("题号范围一致")
        elif homework_end < answer_start or answer_end < homework_start:
            has_hard_conflict = True
            reasons.append("题号范围不一致")
        else:
            reasons.append("题号范围部分重叠")

    homework_count = _integer(homework_signature.get("question_count"))
    answer_count = _integer(answer_signature.get("question_count"))
    if homework_count is not None and answer_count is not None:
        if homework_count == answer_count:
            score += MATCH_WEIGHTS["question_count"]
            reasons.append("题目数量一致")
        else:
            reasons.append("题目数量不一致")

    homework_keywords = {
        _normalized_text(keyword)
        for keyword in homework_signature.get("keywords") or []
        if _normalized_text(keyword)
    }
    answer_keywords = {
        _normalized_text(keyword)
        for keyword in answer_signature.get("keywords") or []
        if _normalized_text(keyword)
    }
    if homework_keywords and answer_keywords:
        overlap = homework_keywords & answer_keywords
        if overlap:
            score += MATCH_WEIGHTS["keywords"] * (
                len(overlap) / len(homework_keywords | answer_keywords)
            )
            reasons.append("关键词有重合")
        else:
            reasons.append("关键词不一致")

    if answer_signature.get("is_answer") is not True:
        reasons.append("答案标记无效")

    if has_hard_conflict:
        score = min(score, _CONFLICT_SCORE_CAP)

    return round(score, 6), "；".join(reasons) or "无可比对特征"


def _load_signature(import_file: ImportFile) -> dict:
    try:
        value = json.loads(import_file.content_signature_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def match_batch_answers(db: Session, batch_id: int) -> list[ImportFile]:
    current_answers = list(
        db.scalars(
            select(ImportFile)
            .where(
                ImportFile.import_batch_id == batch_id,
                ImportFile.document_role == "answer",
            )
            .order_by(ImportFile.id)
        )
    )
    recognized_answers = [
        answer for answer in current_answers if answer.recognition_status == "success"
    ]
    homeworks = list(
        db.scalars(
            select(ImportFile)
            .where(
                ImportFile.import_batch_id == batch_id,
                ImportFile.document_role == "homework",
                ImportFile.recognition_status == "success",
            )
            .order_by(ImportFile.id)
        )
    )

    for answer in current_answers:
        answer.match_status = None
        answer.matched_homework_file_id = None
        answer.match_confidence = None
        answer.match_reason = None
    db.flush()

    if not homeworks:
        for answer in recognized_answers:
            answer.match_status = "pending"
            answer.match_reason = "当前批次暂无已识别作业"
        db.commit()
        return recognized_answers

    homework_ids = [homework.id for homework in homeworks]
    occupied_homework_ids = set(
        db.scalars(
            select(ImportFile.matched_homework_file_id).where(
                ImportFile.matched_homework_file_id.in_(homework_ids)
            )
        )
    )
    pair_scores: dict[int, list[tuple[float, int, str, ImportFile]]] = {}
    ambiguous_answer_ids: set[int] = set()
    for answer in recognized_answers:
        answer_signature = _load_signature(answer)
        answer_scores: list[tuple[float, int, str, ImportFile]] = []
        for homework in homeworks:
            score, reason = score_answer_match(
                _load_signature(homework), answer_signature
            )
            answer_scores.append((score, homework.id, reason, homework))
        answer_scores.sort(key=lambda item: (-item[0], item[1]))
        pair_scores[answer.id] = answer_scores
        if (
            len(answer_scores) > 1
            and round(answer_scores[0][0] - answer_scores[1][0], 6) < 0.10
        ):
            ambiguous_answer_ids.add(answer.id)

    candidates: list[tuple[float, int, int, str, ImportFile, ImportFile]] = []
    for answer in recognized_answers:
        if answer.id in ambiguous_answer_ids:
            continue
        for score, homework_id, reason, homework in pair_scores[answer.id]:
            if score >= settings.answer_match_confidence_threshold:
                candidates.append(
                    (score, answer.id, homework_id, reason, answer, homework)
                )
    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))

    assigned_answer_ids: set[int] = set()
    assigned_homework_ids: set[int] = set(occupied_homework_ids)
    for score, answer_id, homework_id, reason, answer, _homework in candidates:
        if answer_id in assigned_answer_ids or homework_id in assigned_homework_ids:
            continue
        answer.match_status = "matched"
        answer.matched_homework_file_id = homework_id
        answer.match_confidence = score
        answer.match_reason = reason
        assigned_answer_ids.add(answer_id)
        assigned_homework_ids.add(homework_id)

    for answer in recognized_answers:
        if answer.id in assigned_answer_ids:
            continue
        answer.match_status = "unmatched"
        answer.matched_homework_file_id = None
        answer.match_confidence = None
        answer_scores = pair_scores[answer.id]
        if answer.id in ambiguous_answer_ids:
            best_score = answer_scores[0][0]
            second_score = answer_scores[1][0]
            answer.match_confidence = best_score
            answer.match_reason = (
                f"候选匹配存在歧义（最高分 {best_score:.2f}，"
                f"次高分 {second_score:.2f}）"
            )
        elif answer_scores:
            answer.match_confidence = answer_scores[0][0]
            answer.match_reason = answer_scores[0][2]
        else:
            answer.match_reason = "当前批次作业均已被其他答案占用"

    db.commit()
    return recognized_answers
