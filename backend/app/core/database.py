from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.app.core.config import settings


connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from backend.app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    if settings.database_url.startswith("sqlite"):
        inspector = inspect(engine)
        import_file_columns = {column["name"] for column in inspector.get_columns("import_files")}
        assignment_item_columns = {column["name"] for column in inspector.get_columns("assignment_items")}
        submission_columns = {column["name"] for column in inspector.get_columns("submissions")}
        submission_media_columns = {column["name"] for column in inspector.get_columns("submission_media")}
        correction_result_columns = {column["name"] for column in inspector.get_columns("correction_results")}
        with engine.begin() as connection:
            if "storage_path" not in import_file_columns:
                connection.execute(text("ALTER TABLE import_files ADD COLUMN storage_path VARCHAR(1024)"))
            if "answer_text" not in assignment_item_columns:
                connection.execute(text("ALTER TABLE assignment_items ADD COLUMN answer_text TEXT"))
            if "import_file_id" not in assignment_item_columns:
                connection.execute(text("ALTER TABLE assignment_items ADD COLUMN import_file_id INTEGER"))
            if "source_file_name" not in assignment_item_columns:
                connection.execute(text("ALTER TABLE assignment_items ADD COLUMN source_file_name VARCHAR(255)"))
            if "answer_text" not in submission_columns:
                connection.execute(text("ALTER TABLE submissions ADD COLUMN answer_text TEXT"))
            if "error_code" not in submission_columns:
                connection.execute(text("ALTER TABLE submissions ADD COLUMN error_code VARCHAR(64)"))
            if "error_message" not in submission_columns:
                connection.execute(text("ALTER TABLE submissions ADD COLUMN error_message TEXT"))
            if "purpose" not in submission_media_columns:
                connection.execute(text("ALTER TABLE submission_media ADD COLUMN purpose VARCHAR(32) DEFAULT 'homework'"))
            if "storage_path" not in submission_media_columns:
                connection.execute(text("ALTER TABLE submission_media ADD COLUMN storage_path VARCHAR(1024)"))
            if "review_status" not in correction_result_columns:
                connection.execute(text("ALTER TABLE correction_results ADD COLUMN review_status VARCHAR(32)"))
            if "review_note" not in correction_result_columns:
                connection.execute(text("ALTER TABLE correction_results ADD COLUMN review_note TEXT"))
            if "reviewed_at" not in correction_result_columns:
                connection.execute(text("ALTER TABLE correction_results ADD COLUMN reviewed_at DATETIME"))
