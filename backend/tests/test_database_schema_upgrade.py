from contextlib import contextmanager
import importlib

import pytest
from sqlalchemy.exc import OperationalError

import backend.app.core.database as database


ALL_COLUMNS = {
    "question_results": {"id", "section_no", "subquestion_no"},
    "import_files": {
        "id",
        "document_role",
        "recognized_title",
        "recognition_status",
        "recognition_error",
        "content_summary",
        "content_signature_json",
        "match_status",
        "matched_homework_file_id",
        "match_confidence",
        "match_reason",
    },
    "assignment_batches": {"id", "target_assignment_batch_id"},
}

ALL_INDEXES = {
    "question_results": set(),
    "import_files": {
        "ix_import_files_recognition_status",
        "uq_import_files_matched_homework_file_id",
    },
    "assignment_batches": {"ix_assignment_batches_target_assignment_batch_id"},
}

ALL_FOREIGN_KEYS = {
    "question_results": set(),
    "import_files": {("matched_homework_file_id", "import_files")},
    "assignment_batches": {
        ("target_assignment_batch_id", "assignment_batches")
    },
}


class FakeInspector:
    def __init__(self, bind):
        self.bind = bind

    def get_table_names(self):
        return list(self.bind.existing_columns)

    def get_columns(self, table_name):
        return [
            {"name": name}
            for name in self.bind.existing_columns.get(table_name, set())
        ]

    def get_indexes(self, table_name):
        return [
            {"name": name}
            for name in self.bind.existing_indexes.get(table_name, set())
        ]

    def get_foreign_keys(self, table_name):
        return [
            {
                "constrained_columns": [column],
                "referred_table": referred_table,
            }
            for column, referred_table in self.bind.existing_foreign_keys.get(
                table_name, set()
            )
        ]


class FakeConnection:
    def __init__(self, executed):
        self.executed = executed

    def execute(self, statement):
        self.executed.append(str(statement))


class FakeBind:
    def __init__(
        self,
        existing_columns,
        executed,
        existing_indexes=None,
        existing_foreign_keys=None,
    ):
        self.existing_columns = existing_columns
        self.existing_indexes = existing_indexes or {}
        self.existing_foreign_keys = existing_foreign_keys or {}
        self.executed = executed

    @contextmanager
    def begin(self):
        yield FakeConnection(self.executed)


class RacedSchemaBind(FakeBind):
    @contextmanager
    def begin(self):
        class RacedSchemaError(Exception):
            pass

        class RacedSchemaConnection:
            def execute(inner_self, statement):
                sql = str(statement)
                if "ADD COLUMN" in sql:
                    error_code = 1060
                elif "INDEX" in sql:
                    error_code = 1061
                else:
                    error_code = 1826
                raise OperationalError(
                    sql,
                    {},
                    RacedSchemaError(error_code, "Concurrent schema change"),
                )

        yield RacedSchemaConnection()


class UnexpectedErrorBind(FakeBind):
    @contextmanager
    def begin(self):
        class UnexpectedSchemaError(Exception):
            pass

        class UnexpectedErrorConnection:
            def execute(inner_self, statement):
                raise OperationalError(
                    str(statement),
                    {},
                    UnexpectedSchemaError(9999, "Unexpected schema error"),
                )

        yield UnexpectedErrorConnection()


def install_fake_inspector(monkeypatch):
    monkeypatch.setattr(database, "inspect", FakeInspector, raising=False)


def test_compatibility_schema_upgrade_adds_missing_schema(monkeypatch):
    executed = []
    fake_bind = FakeBind(
        existing_columns={
            "question_results": {"id", "section_no"},
            "import_files": {"id"},
            "assignment_batches": {"id"},
        },
        executed=executed,
    )
    install_fake_inspector(monkeypatch)

    database.ensure_compatibility_schema(fake_bind)

    assert "ALTER TABLE import_files ADD COLUMN document_role VARCHAR(32) NULL" in executed
    assert "ALTER TABLE import_files ADD COLUMN recognized_title VARCHAR(255) NULL" in executed
    assert "ALTER TABLE import_files ADD COLUMN matched_homework_file_id INTEGER NULL" in executed
    assert "ALTER TABLE assignment_batches ADD COLUMN target_assignment_batch_id INTEGER NULL" in executed
    assert (
        "CREATE UNIQUE INDEX uq_import_files_matched_homework_file_id "
        "ON import_files (matched_homework_file_id)"
    ) in executed
    assert (
        "ALTER TABLE import_files "
        "ADD CONSTRAINT fk_import_files_matched_homework_file_id "
        "FOREIGN KEY (matched_homework_file_id) REFERENCES import_files (id)"
    ) in executed
    assert (
        "ALTER TABLE assignment_batches "
        "ADD CONSTRAINT fk_assignment_batches_target_assignment_batch_id "
        "FOREIGN KEY (target_assignment_batch_id) REFERENCES assignment_batches (id)"
    ) in executed


def test_compatibility_schema_upgrade_is_idempotent(monkeypatch):
    executed = []
    fake_bind = FakeBind(
        existing_columns=ALL_COLUMNS,
        existing_indexes=ALL_INDEXES,
        existing_foreign_keys=ALL_FOREIGN_KEYS,
        executed=executed,
    )
    install_fake_inspector(monkeypatch)

    database.ensure_compatibility_schema(fake_bind)
    database.ensure_compatibility_schema(fake_bind)

    assert executed == []


def test_compatibility_schema_upgrade_tolerates_concurrent_duplicates(
    monkeypatch,
):
    fake_bind = RacedSchemaBind(
        existing_columns={
            "question_results": {"id"},
            "import_files": {"id"},
            "assignment_batches": {"id"},
        },
        existing_indexes={},
        existing_foreign_keys={},
        executed=[],
    )
    install_fake_inspector(monkeypatch)

    database.ensure_compatibility_schema(fake_bind)


def test_compatibility_schema_upgrade_reraises_other_operational_errors(
    monkeypatch,
):
    fake_bind = UnexpectedErrorBind(
        existing_columns={"question_results": {"id"}},
        executed=[],
    )
    install_fake_inspector(monkeypatch)

    with pytest.raises(OperationalError):
        database.ensure_compatibility_schema(fake_bind)


def test_celery_worker_initializes_database_schema(monkeypatch):
    worker_app = importlib.import_module("backend.app.worker.celery_app")
    calls = []
    monkeypatch.setattr(worker_app, "init_db", lambda: calls.append("init"), raising=False)

    worker_app.initialize_worker_database()

    assert calls == ["init"]
