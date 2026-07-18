from contextlib import contextmanager

import backend.app.core.database as database


class FakeInspector:
    def __init__(self, columns):
        self.columns = columns

    def get_table_names(self):
        return ["question_results"]

    def get_columns(self, table_name):
        assert table_name == "question_results"
        return [{"name": name} for name in self.columns]


class FakeConnection:
    def __init__(self, executed):
        self.executed = executed

    def execute(self, statement):
        self.executed.append(str(statement))


class FakeBind:
    def __init__(self, existing_columns, executed):
        self.existing_columns = existing_columns
        self.executed = executed

    @contextmanager
    def begin(self):
        yield FakeConnection(self.executed)


def test_hierarchy_schema_upgrade_adds_only_missing_columns(monkeypatch):
    executed = []
    fake_bind = FakeBind(
        existing_columns={"id", "question_no", "section_no"},
        executed=executed,
    )
    monkeypatch.setattr(
        database,
        "inspect",
        lambda bind: FakeInspector(bind.existing_columns),
        raising=False,
    )

    database.ensure_question_result_hierarchy_columns(fake_bind)

    assert executed == [
        "ALTER TABLE question_results "
        "ADD COLUMN subquestion_no VARCHAR(32) NULL"
    ]


def test_hierarchy_schema_upgrade_is_idempotent(monkeypatch):
    executed = []
    fake_bind = FakeBind(
        existing_columns={
            "id",
            "question_no",
            "section_no",
            "subquestion_no",
        },
        executed=executed,
    )
    monkeypatch.setattr(
        database,
        "inspect",
        lambda bind: FakeInspector(bind.existing_columns),
        raising=False,
    )

    database.ensure_question_result_hierarchy_columns(fake_bind)

    assert executed == []
