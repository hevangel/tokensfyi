import pytest

from token_budget_platform.config.environment import EnvironmentError, load_environment


def test_local_environment_has_safe_defaults() -> None:
    settings = load_environment({"DJANGO_SETTINGS_MODULE": "config.settings.local"})
    assert settings.database_url == "sqlite:///db.sqlite3"
    assert settings.allowed_hosts == ["localhost", "127.0.0.1"]
    assert settings.secret_key == "local-development-only-not-a-secret"


def test_production_reports_all_missing_required_settings() -> None:
    with pytest.raises(EnvironmentError, match="SECRET_KEY, DATABASE_URL, ALLOWED_HOSTS"):
        load_environment({"DJANGO_SETTINGS_MODULE": "config.settings.production"})


def test_database_urls_are_externalized() -> None:
    settings = load_environment(
        {
            "DJANGO_SETTINGS_MODULE": "config.settings.production",
            "SECRET_KEY": "test" + "-only",
            "DATABASE_URL": "postgresql://user:pass@db:5432/app",
            "ALLOWED_HOSTS": "example.test",
        }
    )
    assert settings.database_config["ENGINE"] == "django.db.backends.postgresql"
    assert settings.database_config["HOST"] == "db"
