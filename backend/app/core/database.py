from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.app.core.config import settings


engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


COMPATIBILITY_COLUMNS = {
    "question_results": {
        "section_no": "VARCHAR(32) NULL",
        "subquestion_no": "VARCHAR(32) NULL",
    },
    "import_files": {
        "document_role": "VARCHAR(32) NULL",
        "recognized_title": "VARCHAR(255) NULL",
        "recognition_status": "VARCHAR(32) NULL",
        "recognition_error": "TEXT NULL",
        "content_summary": "TEXT NULL",
        "content_signature_json": "TEXT NULL",
        "match_status": "VARCHAR(32) NULL",
        "matched_homework_file_id": "INTEGER NULL",
        "match_confidence": "FLOAT NULL",
        "match_reason": "TEXT NULL",
    },
    "assignment_batches": {
        "target_assignment_batch_id": "INTEGER NULL",
    },
}

COMPATIBILITY_INDEXES = {
    "import_files": {
        "ix_import_files_recognition_status": (
            "CREATE INDEX ix_import_files_recognition_status "
            "ON import_files (recognition_status)"
        ),
        "uq_import_files_matched_homework_file_id": (
            "CREATE UNIQUE INDEX uq_import_files_matched_homework_file_id "
            "ON import_files (matched_homework_file_id)"
        ),
    },
    "assignment_batches": {
        "ix_assignment_batches_target_assignment_batch_id": (
            "CREATE INDEX ix_assignment_batches_target_assignment_batch_id "
            "ON assignment_batches (target_assignment_batch_id)"
        ),
    },
}

COMPATIBILITY_FOREIGN_KEYS = {
    "import_files": {
        "columns": ["matched_homework_file_id"],
        "referred_table": "import_files",
        "sql": (
            "ALTER TABLE import_files "
            "ADD CONSTRAINT fk_import_files_matched_homework_file_id "
            "FOREIGN KEY (matched_homework_file_id) REFERENCES import_files (id)"
        ),
    },
    "assignment_batches": {
        "columns": ["target_assignment_batch_id"],
        "referred_table": "assignment_batches",
        "sql": (
            "ALTER TABLE assignment_batches "
            "ADD CONSTRAINT fk_assignment_batches_target_assignment_batch_id "
            "FOREIGN KEY (target_assignment_batch_id) REFERENCES assignment_batches (id)"
        ),
    },
}


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _execute_compatibility_ddl(bind, sql: str, duplicate_error_code: int) -> None:
    try:
        with bind.begin() as connection:
            connection.execute(text(sql))
    except OperationalError as exc:
        error_args = getattr(exc.orig, "args", ())
        if not error_args or error_args[0] != duplicate_error_code:
            raise


def ensure_compatibility_schema(bind=engine) -> None:
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())

    for table_name, column_definitions in COMPATIBILITY_COLUMNS.items():
        if table_name not in existing_tables:
            continue
        existing_columns = {
            column["name"] for column in inspector.get_columns(table_name)
        }
        for column_name, sql_type in column_definitions.items():
            if column_name in existing_columns:
                continue
            _execute_compatibility_ddl(
                bind,
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {sql_type}",
                1060,
            )

    for table_name, index_definitions in COMPATIBILITY_INDEXES.items():
        if table_name not in existing_tables:
            continue
        existing_indexes = {
            index["name"] for index in inspector.get_indexes(table_name)
        }
        for index_name, sql in index_definitions.items():
            if index_name in existing_indexes:
                continue
            _execute_compatibility_ddl(bind, sql, 1061)

    for table_name, foreign_key_definition in COMPATIBILITY_FOREIGN_KEYS.items():
        if table_name not in existing_tables:
            continue
        foreign_key_exists = any(
            foreign_key["constrained_columns"]
            == foreign_key_definition["columns"]
            and foreign_key["referred_table"]
            == foreign_key_definition["referred_table"]
            for foreign_key in inspector.get_foreign_keys(table_name)
        )
        if not foreign_key_exists:
            _execute_compatibility_ddl(
                bind,
                foreign_key_definition["sql"],
                1826,
            )


def init_db() -> None:
    from backend.app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    ensure_compatibility_schema(engine)
