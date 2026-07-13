from sqlalchemy import inspect

from backend.app.core.config import Settings
from backend.app.core.database import engine, init_db


def test_annotation_schema_and_default_threshold_exist():
    init_db()
    inspector = inspect(engine)
    submission_columns = {column["name"] for column in inspector.get_columns("submissions")}
    question_columns = {column["name"] for column in inspector.get_columns("question_results")}

    assert {"processing_stage", "processing_message"} <= submission_columns
    assert {"source_media_id", "annotations_json"} <= question_columns
    assert Settings().annotation_confidence_threshold == 0.65
