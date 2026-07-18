import json

from sqlalchemy.orm import Session

from backend.app.models import QuestionResult, Submission, SubmissionMedia


def _question_payload(question: QuestionResult) -> dict:
    try:
        annotations = json.loads(question.annotations_json or "[]")
    except json.JSONDecodeError:
        annotations = []
    return {
        "source_media_id": question.source_media_id,
        "section_no": question.section_no,
        "question_no": question.question_no,
        "subquestion_no": question.subquestion_no,
        "question_type": question.question_type,
        "is_correct": question.is_correct,
        "recognized_answer": question.recognized_answer,
        "expected_answer": question.expected_answer,
        "score": question.score,
        "explanation": question.explanation,
        "confidence_score": question.confidence_score,
        "annotations": annotations if isinstance(annotations, list) else [],
    }


def aggregate_question_status(statuses: list[bool | None]) -> bool | None:
    if any(status is False for status in statuses):
        return False
    if any(status is None for status in statuses):
        return None
    return True


def aggregate_question_results(
    questions: list[QuestionResult],
) -> list[dict]:
    grouped: dict[
        tuple[int | None, str | None, str],
        list[dict],
    ] = {}
    for question in questions:
        leaf = _question_payload(question)
        key = (
            question.source_media_id,
            question.section_no,
            question.question_no,
        )
        grouped.setdefault(key, []).append(leaf)

    result = []
    for (media_id, section_no, question_no), leaves in grouped.items():
        subquestions = [
            leaf
            for leaf in leaves
            if leaf["subquestion_no"] is not None
        ]
        only_leaf = leaves[0]

        def combined(field: str):
            values = []
            for leaf in leaves:
                value = leaf.get(field)
                if value in (None, ""):
                    continue
                prefix = (
                    f"({leaf['subquestion_no']}) "
                    if leaf["subquestion_no"]
                    else ""
                )
                values.append(f"{prefix}{value}")
            return "；".join(values) or None

        confidence_values = [
            leaf["confidence_score"]
            for leaf in leaves
            if leaf["confidence_score"] is not None
        ]
        score_values = [
            leaf["score"]
            for leaf in leaves
            if leaf["score"] is not None
        ]
        result.append({
            "source_media_id": media_id,
            "section_no": section_no,
            "question_no": question_no,
            "question_type": only_leaf["question_type"],
            "is_correct": aggregate_question_status([
                leaf["is_correct"]
                for leaf in leaves
            ]),
            "recognized_answer": (
                only_leaf["recognized_answer"]
                if len(leaves) == 1
                else combined("recognized_answer")
            ),
            "expected_answer": (
                only_leaf["expected_answer"]
                if len(leaves) == 1
                else combined("expected_answer")
            ),
            "score": (
                only_leaf["score"]
                if len(leaves) == 1
                else sum(score_values) if score_values else None
            ),
            "explanation": (
                only_leaf["explanation"]
                if len(leaves) == 1
                else combined("explanation")
            ),
            "confidence_score": (
                min(confidence_values)
                if confidence_values
                else None
            ),
            "annotations": [
                annotation
                for leaf in leaves
                for annotation in leaf["annotations"]
            ],
            "subquestions": subquestions,
        })
    return result


def build_result_pages(db: Session, submission: Submission, questions: list[QuestionResult]) -> list[dict]:
    media = db.query(SubmissionMedia).filter(
        SubmissionMedia.submission_id == submission.id,
        SubmissionMedia.purpose == "homework",
        SubmissionMedia.media_type == "image",
    ).order_by(SubmissionMedia.sort_order, SubmissionMedia.id).all()
    by_media: dict[int, list[QuestionResult]] = {}
    for question in questions:
        if question.source_media_id:
            by_media.setdefault(question.source_media_id, []).append(question)
    pages = []
    for page_number, item in enumerate(media, start=1):
        page_questions = aggregate_question_results(by_media.get(item.id, []))
        has_correction = bool(page_questions)
        pages.append({
            "media_id": item.id,
            "page_number": page_number,
            "total_pages": len(media),
            "image_url": f"/submissions/media/{item.id}/content",
            "has_correction": has_correction,
            "review_message": None if has_correction else (
                "本页未生成批改结果，不能判断为全对，请重新批改或人工复核"
            ),
            "summary": {
                "correct_question_nos": [
                    q["question_no"]
                    for q in page_questions
                    if q["is_correct"] is True
                ],
                "incorrect_question_nos": [
                    q["question_no"]
                    for q in page_questions
                    if q["is_correct"] is False
                ],
                "review_question_nos": [
                    q["question_no"]
                    for q in page_questions
                    if q["is_correct"] is None
                ],
            },
            "questions": page_questions,
        })
    return pages
