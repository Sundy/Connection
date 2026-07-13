import json

from sqlalchemy.orm import Session

from backend.app.models import QuestionResult, Submission, SubmissionMedia


def _question_payload(question: QuestionResult) -> dict:
    try:
        annotations = json.loads(question.annotations_json or "[]")
    except json.JSONDecodeError:
        annotations = []
    return {
        "question_no": question.question_no,
        "is_correct": question.is_correct,
        "recognized_answer": question.recognized_answer,
        "expected_answer": question.expected_answer,
        "explanation": question.explanation,
        "confidence_score": question.confidence_score,
        "annotations": annotations if isinstance(annotations, list) else [],
    }


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
        page_questions = by_media.get(item.id, [])
        has_correction = bool(page_questions)
        pages.append({
            "media_id": item.id,
            "page_number": page_number,
            "image_url": f"/submissions/media/{item.id}/content",
            "has_correction": has_correction,
            "review_message": None if has_correction else (
                "本页未生成批改结果，不能判断为全对，请重新批改或人工复核"
            ),
            "summary": {
                "correct_question_nos": [q.question_no for q in page_questions if q.is_correct is True],
                "incorrect_question_nos": [q.question_no for q in page_questions if q.is_correct is False],
                "review_question_nos": [q.question_no for q in page_questions if q.is_correct is None],
            },
            "questions": [_question_payload(question) for question in page_questions],
        })
    return pages
