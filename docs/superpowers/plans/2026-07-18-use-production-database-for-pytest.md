# Use Production Database for Pytest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the dedicated test database mode so local startup and pytest both use `DB_PROD_OUT` to connect to the MySQL `connection` database.

**Architecture:** `Settings` supports only `development` and `production`. pytest no longer overrides `APP_ENV`; it inherits `.env` just like a local backend process. The SQLAlchemy engine remains MySQL-only and continues to consume the resolved configuration URL.

**Tech Stack:** Python 3, pydantic-settings 2.7.0, SQLAlchemy 2.0.36, PyMySQL 1.2.0, pytest 8.3.4

## Global Constraints

- Add `APP_ENV=development` to the untracked project-root `.env`.
- `APP_ENV=development` reads only `DB_PROD_OUT`, whose current database is `connection`.
- `APP_ENV=production` reads only `DATABASE_URL_PRODUCTION`, whose current database is also `connection`.
- Remove `APP_ENV=test`, `DATABASE_URL_TEST`, and the `connection_dev` database-name guard from application code and tests.
- pytest must not override `APP_ENV`; it uses `DB_PROD_OUT` and may persist test records in `connection`.
- The user explicitly accepts that pytest writes persistent test data to `connection` without automatic cleanup.
- Do not copy data from SQLite or `connection_dev`, and do not drop `connection_dev`.
- Verification must avoid a full pytest run so this configuration-only change does not add another set of business test records to `connection`.

---

### Task 1: Remove the dedicated test environment

**Files:**
- Delete: `backend/tests/conftest.py`
- Modify: `backend/tests/test_database_config.py`
- Modify: `backend/app/core/config.py`
- Modify (untracked local configuration): `.env`

**Interfaces:**
- Produces: `Settings.app_env: Literal["development", "production"]`.
- Preserves: `Settings.database_url: str`, selecting `DB_PROD_OUT` for development and `DATABASE_URL_PRODUCTION` for production.
- Removes: `Settings.database_url_test` and the `test` entry in `DATABASE_ENV_FIELDS`.

- [ ] **Step 1: Stop pytest from overriding the application environment**

Delete `backend/tests/conftest.py` entirely.

- [ ] **Step 2: Write the failing configuration test and update expectations**

In `backend/tests/test_database_config.py`:

- Remove the `test` rows from both parameterized environment lists.
- Delete `test_test_environment_requires_connection_dev_without_leaking_url`.
- Change the engine database assertion from `connection_dev` to `connection`.
- Add this test:

```python
from pydantic import ValidationError


def test_settings_rejects_the_removed_test_environment():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="test")
```

- [ ] **Step 3: Run the focused tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest backend/tests/test_database_config.py -q
```

Expected: exactly one failure because the current `Settings` still accepts `APP_ENV=test`; the remaining development, production, URL-validation, and engine tests pass using `DB_PROD_OUT` without opening a database connection.

- [ ] **Step 4: Remove the test environment from Settings**

In `backend/app/core/config.py`, change the mapping and fields to:

```python
DATABASE_ENV_FIELDS = {
    "development": ("db_prod_out", "DB_PROD_OUT"),
    "production": ("database_url_production", "DATABASE_URL_PRODUCTION"),
}


class Settings(BaseSettings):
    app_name: str = "Homework Agent API"
    app_env: Literal["development", "production"] = "development"
    db_prod_out: str = ""
    database_url_production: str = ""
```

Remove this test-specific validation from `database_url`:

```python
        if self.app_env == "test" and url.database != "connection_dev":
            raise ValueError("DATABASE_URL_TEST must use the connection_dev database")
```

- [ ] **Step 5: Add the explicit local environment selector to `.env`**

Add this non-secret line to the project-root `.env` without changing existing URLs or credentials:

```text
APP_ENV=development
```

- [ ] **Step 6: Run the focused tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest backend/tests/test_database_config.py -q
```

Expected: `8 passed` with no database connection opened.

- [ ] **Step 7: Verify pytest collection resolves to `connection` without running business tests**

Run:

```bash
.venv/bin/python -m pytest backend/tests --collect-only -q
```

Expected: collection succeeds, reports 55 tests, and application imports connect through `DB_PROD_OUT`. Module-level `create_all()` calls are no-ops because the 15 production tables already exist; test functions do not run.

- [ ] **Step 8: Commit tracked configuration and test changes**

```bash
git add backend/app/core/config.py backend/tests/conftest.py backend/tests/test_database_config.py
git commit -m "移除独立测试数据库配置"
```

The ignored `.env` change remains local and must not be staged.

---

### Task 2: Document pytest production-database behavior

**Files:**
- Modify: `README.md`

**Interfaces:**
- Documents the same two-environment routing exposed by `Settings`.
- Explicitly states that pytest uses `connection` and persists data there.

- [ ] **Step 1: Replace the dedicated test database instructions**

Replace the README paragraph and example that describe `APP_ENV=test` and `DATABASE_URL_TEST` with:

```markdown
pytest uses the same `development` configuration as local startup, so it reads `DB_PROD_OUT` and connects to the `connection` database. The test suite writes persistent business records and does not automatically clean them up.
```

- [ ] **Step 2: Verify no active documentation or application code references the removed test mode**

Run:

```bash
rg -n "DATABASE_URL_TEST|APP_ENV=test|connection_dev" README.md backend/app backend/tests || true
git diff --check
```

Expected: no matches and no whitespace errors.

- [ ] **Step 3: Re-run the safe focused verification**

Run:

```bash
.venv/bin/python -m pytest backend/tests/test_database_config.py -q
node --test miniapp/tests/*.test.js
```

Expected: 8 backend configuration tests and 39 mini-program tests pass. Do not run the full backend suite because the user has accepted production writes but this routing-only change does not require generating another batch of test data.

- [ ] **Step 4: Commit documentation**

```bash
git add README.md
git commit -m "说明 pytest 使用 connection 数据库"
```

- [ ] **Step 5: Verify final state**

Run:

```bash
git status --short
git log -5 --oneline
awk -F= '/^APP_ENV=/{print $1 "=" $2}' .env
```

Expected: tracked worktree clean, recent focused commits present, and local output contains only `APP_ENV=development`.
