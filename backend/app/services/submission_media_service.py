from sqlalchemy.orm import Session

from backend.app.models import SubmissionMedia


def homework_images_for_annotation(
    db: Session,
    submission_id: int,
    *,
    limit: int | None = None,
) -> list[SubmissionMedia]:
    query = db.query(SubmissionMedia).filter(
        SubmissionMedia.submission_id == submission_id,
        SubmissionMedia.purpose == "homework",
        SubmissionMedia.media_type == "image",
    ).order_by(SubmissionMedia.sort_order, SubmissionMedia.id)
    if limit is not None:
        query = query.limit(limit)
    return query.all()
