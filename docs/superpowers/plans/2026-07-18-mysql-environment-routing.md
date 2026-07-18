# MySQL Environment Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route development, production, and pytest processes to their designated MySQL URLs, with a hard safety boundary around the `connection_dev` test database and no SQLite fallback.

**Architecture:** `backend.app.core.config.Settings` owns environment selection, URL validation, and driver normalization, exposing one resolved `database_url` property. `backend.app.core.database` consumes only that validated URL and creates a MySQL SQLAlchemy engine with connection liveness checks. A root pytest configuration selects `APP_ENV=test` before application modules are imported.

**Tech Stack:** Python 3, pydantic-settings 2.7.0, SQLAlchemy 2.0.36, PyMySQL 1.2.0, pytest 8.3.4

## Global Constraints

- `APP_ENV=development` reads only `DB_PROD_OUT`.
- `APP_ENV=production` reads only `DATABASE_URL_PRODUCTION`.
- `APP_ENV=test` reads only `DATABASE_URL_TEST`.
- `DATABASE_URL_TEST` must target a database named exactly `connection_dev`.
- Missing or invalid environment-specific configuration must fail before opening a connection and must never fall back to another environment URL.
- All selected URLs must use MySQL; `mysql://` is normalized to `mysql+pymysql://`.
- Secrets must remain in the untracked `.env` or deployment environment and must never appear in errors, documentation, commits, or test output.
- SQLite support and its schema compatibility logic are removed.
- `Base.metadata.create_all()` remains; database migrations and test-data cleanup are outside scope.

## File Structure

- Create `backend/tests/conftest.py`: force pytest collection and execution into the test database environment before application imports.
- Create `backend/tests/test_database_config.py`: unit coverage for environment routing, URL normalization, fail-closed behavior, test database protection, and engine settings.
- Modify `backend/app/core/config.py`: define database environment inputs and expose the validated, normalized URL.
- Modify `backend/app/core/database.py`: remove SQLite branches and configure a MySQL engine with `pool_pre_ping`.
- Modify `requirements.txt`: install the pinned synchronous MySQL driver.
- Modify `README.md`: document local, production, and test configuration and the absence of SQLite fallback.

---

### Task 1: Fail-closed database configuration

**Files:**
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/test_database_config.py`
- Modify: `backend/app/core/config.py`

**Interfaces:**
- Consumes: environment values loaded by `pydantic_settings.BaseSettings`.
- Produces: `Settings.app_env: Literal["development", "production", "test"]` and `Settings.database_url: str`.
- Produces: normalized URLs suitable for `sqlalchemy.create_engine`, without opening a connection.

- [ ] **Step 1: Force pytest to select the test environment before test modules import the app**

Create `backend/tests/conftest.py`:

```python
import os


os.environ["APP_ENV"] = "test"
```

- [ ] **Step 2: Write failing configuration tests**

Create `backend/tests/test_database_config.py`:

```python
import pytest

from backend.app.core.config import Settings


@pytest.mark.parametrize(
    ("app_env", "field_name", "expected_database"),
    [
        ("development", "db_prod_out", "connection"),
        ("production", "database_url_production", "connection"),
        ("test", "database_url_test", "connection_dev"),
    ],
)
def test_database_url_selects_only_the_current_environment(
    app_env,
    field_name,
    expected_database,
):
    values = {
        "app_env": app_env,
        "db_prod_out": "",
        "database_url_production": "",
        "database_url_test": "",
        field_name: f"mysql://user:secret@db.example.com/{expected_database}",
    }

    settings = Settings(_env_file=None, **values)

    assert settings.database_url == (
        f"mysql+pymysql://user:secret@db.example.com/{expected_database}"
    )


@pytest.mark.parametrize(
    ("app_env", "variable_name"),
    [
        ("development", "DB_PROD_OUT"),
        ("production", "DATABASE_URL_PRODUCTION"),
        ("test", "DATABASE_URL_TEST"),
    ],
)
def test_database_url_rejects_a_missing_environment_url(app_env, variable_name):
    settings = Settings(
        _env_file=None,
        app_env=app_env,
        db_prod_out="",
        database_url_production="",
        database_url_test="",
    )

    with pytest.raises(ValueError, match=variable_name):
        settings.database_url


def test_database_url_rejects_non_mysql_urls():
    settings = Settings(
        _env_file=None,
        app_env="development",
        db_prod_out="postgresql://user:secret@db.example.com/connection",
    )

    with pytest.raises(ValueError, match="MySQL"):
        settings.database_url


def test_database_url_preserves_an_explicit_mysql_driver():
    settings = Settings(
        _env_file=None,
        app_env="development",
        db_prod_out="mysql+pymysql://user:secret@db.example.com/connection",
    )

    assert settings.database_url == (
        "mysql+pymysql://user:secret@db.example.com/connection"
    )


def test_test_environment_requires_connection_dev_without_leaking_url():
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url_test="mysql://user:top-secret@db.example.com/connection",
    )

    with pytest.raises(ValueError) as exc_info:
        settings.database_url

    assert "connection_dev" in str(exc_info.value)
    assert "top-secret" not in str(exc_info.value)
```

- [ ] **Step 3: Run the tests and verify the expected failure**

Run:

```bash
pytest backend/tests/test_database_config.py -q
```

Expected: FAIL because `Settings` does not yet accept `app_env` or the three environment-specific URL fields, and its current `database_url` still resolves to SQLite.

- [ ] **Step 4: Implement environment selection, validation, and normalization**

At the top of `backend/app/core/config.py`, add the imports and replace the existing `database_url` field with the environment-specific fields:

```python
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


DATABASE_ENV_FIELDS = {
    "development": ("db_prod_out", "DB_PROD_OUT"),
    "production": ("database_url_production", "DATABASE_URL_PRODUCTION"),
    "test": ("database_url_test", "DATABASE_URL_TEST"),
}


class Settings(BaseSettings):
    app_name: str = "Homework Agent API"
    app_env: Literal["development", "production", "test"] = "development"
    db_prod_out: str = ""
    database_url_production: str = ""
    database_url_test: str = ""
```

Keep all existing non-database fields and `model_config` unchanged. Add this property immediately before `model_config`:

```python
    @property
    def database_url(self) -> str:
        field_name, variable_name = DATABASE_ENV_FIELDS[self.app_env]
        raw_url = getattr(self, field_name).strip()
        if not raw_url:
            raise ValueError(f"{variable_name} must be set when APP_ENV={self.app_env}")

        try:
            url = make_url(raw_url)
        except (ArgumentError, TypeError):
            raise ValueError(f"{variable_name} must be a valid MySQL URL") from None
        if not url.drivername.startswith("mysql"):
            raise ValueError(f"{variable_name} must be a MySQL URL")
        if self.app_env == "test" and url.database != "connection_dev":
            raise ValueError("DATABASE_URL_TEST must use the connection_dev database")
        if url.drivername == "mysql":
            url = url.set(drivername="mysql+pymysql")
        return url.render_as_string(hide_password=False)
```

- [ ] **Step 5: Run the focused tests and verify they pass**

Run:

```bash
pytest backend/tests/test_database_config.py -q
```

Expected: `9 passed` (three environment cases, three missing-variable cases, and three standalone validation cases).

- [ ] **Step 6: Commit the configuration boundary**

```bash
git add backend/app/core/config.py backend/tests/conftest.py backend/tests/test_database_config.py
git commit -m "配置 MySQL 环境路由"
```

---

### Task 2: MySQL-only SQLAlchemy engine

**Files:**
- Modify: `backend/tests/test_database_config.py`
- Modify: `backend/app/core/database.py`
- Modify: `requirements.txt`

**Interfaces:**
- Consumes: `settings.database_url: str` from Task 1.
- Produces: module-level `engine` using PyMySQL and connection liveness checks.
- Preserves: `SessionLocal`, `Base`, `get_db()`, and `init_db()` call signatures.

- [ ] **Step 1: Write the failing engine test**

Append to `backend/tests/test_database_config.py`:

```python
def test_database_engine_uses_mysql_with_connection_liveness_checks():
    from backend.app.core.database import engine

    assert engine.url.drivername == "mysql+pymysql"
    assert engine.url.database == "connection_dev"
    assert engine.pool._pre_ping is True
```

- [ ] **Step 2: Run the engine test and verify it fails against the old SQLite engine**

Run with a syntactically valid test URL; engine construction does not open a connection:

```bash
DATABASE_URL_TEST='mysql://user:secret@127.0.0.1/connection_dev' \
  pytest backend/tests/test_database_config.py::test_database_engine_uses_mysql_with_connection_liveness_checks -q
```

Expected: FAIL because the old engine either uses SQLite settings or does not enable `pool_pre_ping`.

- [ ] **Step 3: Add the MySQL driver dependency**

Add this line after `sqlalchemy==2.0.36` in `requirements.txt`:

```text
pymysql==1.2.0
```

Install the updated requirements before importing the new driver:

```bash
pip install -r requirements.txt
```

Expected: installation completes with `PyMySQL 1.2.0` available.

- [ ] **Step 4: Replace SQLite engine setup with the validated MySQL setup**

In `backend/app/core/database.py`, replace the imports and engine declaration with:

```python
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.app.core.config import settings


engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
```

Replace `init_db()` with:

```python
def init_db() -> None:
    from backend.app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
```

Do not change `Base` or `get_db()`. Remove the unused `inspect` and `text` imports, `connect_args`, the SQLite condition, and every SQLite-only `ALTER TABLE` statement.

- [ ] **Step 5: Run the focused configuration and engine tests**

Run:

```bash
DATABASE_URL_TEST='mysql://user:secret@127.0.0.1/connection_dev' \
  pytest backend/tests/test_database_config.py -q
```

Expected: all tests pass without attempting a network connection.

- [ ] **Step 6: Verify missing test configuration fails closed before connection**

Run:

```bash
env -u DATABASE_URL_TEST APP_ENV=test python -c \
  'from backend.app.core.database import engine'
```

Expected: non-zero exit with an error naming `DATABASE_URL_TEST`; output must not contain any database password or fallback URL.

- [ ] **Step 7: Commit the MySQL engine change**

```bash
git add backend/app/core/database.py backend/tests/test_database_config.py requirements.txt
git commit -m "切换后端数据库到 MySQL"
```

---

### Task 3: Operator documentation and end-to-end verification

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: the environment routing and safety behavior delivered by Tasks 1 and 2.
- Produces: exact local, production, and test setup instructions for maintainers.

- [ ] **Step 1: Replace the SQLite deployment paragraph in README**

Replace the sentence describing the SQLite default and `DATABASE_URL` with:

````markdown
The backend uses MySQL in every environment and has no SQLite fallback. Put secrets in the untracked `.env` file.

Local development defaults to `APP_ENV=development` and uses the external MySQL URL:

```bash
APP_ENV=development
DB_PROD_OUT=mysql://user:password@external-host:3306/connection
```

Production must select its environment explicitly and uses the production URL:

```bash
APP_ENV=production
DATABASE_URL_PRODUCTION=mysql://user:password@internal-host:3306/connection
```

pytest sets `APP_ENV=test` automatically. Configure a dedicated MySQL test database named exactly `connection_dev`:

```bash
DATABASE_URL_TEST=mysql://user:password@external-host:3306/connection_dev
```

If the URL required by the selected environment is missing, is not MySQL, or a test URL does not target `connection_dev`, startup stops without falling back to another database.
````

- [ ] **Step 2: Check the documentation and diff for accidental secrets or stale SQLite guidance**

Run:

```bash
rg -n "SQLite|DATABASE_URL[^_]|DB_PROD_OUT|DATABASE_URL_PRODUCTION|DATABASE_URL_TEST" README.md backend/app backend/tests
git diff --check
git diff -- README.md backend/app/core/config.py backend/app/core/database.py requirements.txt backend/tests
```

Expected: SQLite appears only where explicitly stating there is no fallback; the old generic `DATABASE_URL` guidance is gone; no real host, username, password, or `.env` value appears in the diff; `git diff --check` is clean.

- [ ] **Step 3: Confirm the real test URL points to the protected database without printing credentials**

Run:

```bash
APP_ENV=test python -c \
  'from backend.app.core.config import settings; from sqlalchemy.engine import make_url; print(make_url(settings.database_url).database)'
```

Expected: prints only `connection_dev`.

- [ ] **Step 4: Run the complete backend suite against the dedicated MySQL test database**

Run:

```bash
pytest backend/tests
```

Expected: all backend tests pass. The safety validation must complete before SQLAlchemy connects, so this command cannot run against a differently named database.

- [ ] **Step 5: Run the mini-program regression suite**

Run:

```bash
node --test miniapp/tests/*.test.js
```

Expected: all mini-program tests pass.

- [ ] **Step 6: Commit documentation and any verification-only corrections**

```bash
git add README.md
git commit -m "说明 MySQL 环境配置"
```

- [ ] **Step 7: Verify the final worktree and commit history**

Run:

```bash
git status --short
git log -4 --oneline
```

Expected: the worktree is clean and the recent history contains the design commit plus the three focused implementation commits.
