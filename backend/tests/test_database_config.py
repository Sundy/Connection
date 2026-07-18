import pytest
from pydantic import ValidationError

from backend.app.core.config import Settings


@pytest.mark.parametrize(
    ("app_env", "field_name", "expected_database"),
    [
        ("development", "db_prod_out", "connection"),
        ("production", "database_url_production", "connection"),
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
    ],
)
def test_database_url_rejects_a_missing_environment_url(app_env, variable_name):
    settings = Settings(
        _env_file=None,
        app_env=app_env,
        db_prod_out="",
        database_url_production="",
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


def test_settings_rejects_the_removed_test_environment():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="test")


def test_database_engine_uses_mysql_with_connection_liveness_checks():
    from backend.app.core.database import engine

    assert engine.url.drivername == "mysql+pymysql"
    assert engine.url.database == "connection"
    assert engine.pool._pre_ping is True
