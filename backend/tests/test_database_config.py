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
