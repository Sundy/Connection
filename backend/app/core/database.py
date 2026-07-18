from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError
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
    for name, sql_type in missing:
        try:
            with bind.begin() as connection:
                connection.execute(text(
                    f"ALTER TABLE question_results "
                    f"ADD COLUMN {name} {sql_type}"
                ))
        except OperationalError as exc:
            error_args = getattr(exc.orig, "args", ())
            if not error_args or error_args[0] != 1060:
                raise


def init_db() -> None:
    from backend.app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    ensure_question_result_hierarchy_columns(engine)
