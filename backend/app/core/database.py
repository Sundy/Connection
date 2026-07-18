from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.app.core.config import settings


engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


QUESTION_RESULT_HIERARCHY_COLUMNS = {
    "section_no": "VARCHAR(32) NULL",
    "subquestion_no": "VARCHAR(32) NULL",
}


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_question_result_hierarchy_columns(bind=engine) -> None:
    inspector = inspect(bind)
    if "question_results" not in inspector.get_table_names():
        return
    existing = {
        column["name"]
        for column in inspector.get_columns("question_results")
    }
    missing = [
        (name, sql_type)
        for name, sql_type in QUESTION_RESULT_HIERARCHY_COLUMNS.items()
        if name not in existing
    ]
    if not missing:
        return
    with bind.begin() as connection:
        for name, sql_type in missing:
            connection.execute(text(
                f"ALTER TABLE question_results ADD COLUMN {name} {sql_type}"
            ))


def init_db() -> None:
    from backend.app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    ensure_question_result_hierarchy_columns(engine)
