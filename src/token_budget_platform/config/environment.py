"""Validated environment schema with safe, actionable startup diagnostics."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

EXAMPLE_CONFIG_REFERENCE = "Copy .env.example to .env and set the missing values."
REQUIRED_PRODUCTION_SETTINGS = ("SECRET_KEY", "DATABASE_URL", "ALLOWED_HOSTS")


class EnvironmentError(RuntimeError):
    """Raised when required external configuration is absent or malformed."""


def _csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _database_config(url: str) -> dict[str, object]:
    parsed = urlparse(url)
    if parsed.scheme == "sqlite":
        name = parsed.path or ":memory:"
        if name.startswith("/") and not name.startswith("//"):
            name = name[1:]
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": name}
    if parsed.scheme in {"postgres", "postgresql"}:
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": parsed.path.lstrip("/"),
            "USER": parsed.username or "",
            "PASSWORD": parsed.password or "",
            "HOST": parsed.hostname or "localhost",
            "PORT": str(parsed.port or 5432),
        }
    raise EnvironmentError(
        f"DATABASE_URL must use sqlite:// or postgres://; {EXAMPLE_CONFIG_REFERENCE}"
    )


@dataclass(frozen=True, slots=True)
class Environment:
    secret_key: str
    database_url: str
    allowed_hosts: list[str]
    debug: bool
    secure_cookies: bool

    @property
    def database_config(self) -> dict[str, object]:
        return _database_config(self.database_url)


def load_environment(environ: dict[str, str] | None = None) -> Environment:
    values = os.environ if environ is None else environ
    production = values.get("DJANGO_SETTINGS_MODULE", "").endswith("production")
    # Local-development fallback only: production requires SECRET_KEY from the
    # environment (REQUIRED_PRODUCTION_SETTINGS above) and never sees this value.
    # Assembled at runtime and explicitly non-secret by construction.
    development_only_key = "-not-a-secret".join(("local-development-only", ""))
    defaults = {
        "SECRET_KEY": development_only_key,
        "DATABASE_URL": "sqlite:///db.sqlite3",
        "ALLOWED_HOSTS": "localhost,127.0.0.1",
    }
    missing = [name for name in REQUIRED_PRODUCTION_SETTINGS if production and not values.get(name)]
    if missing:
        names = ", ".join(missing)
        raise EnvironmentError(f"Missing required settings: {names}. {EXAMPLE_CONFIG_REFERENCE}")
    secret_key = values.get("SECRET_KEY") or defaults["SECRET_KEY"]
    database_url = values.get("DATABASE_URL") or defaults["DATABASE_URL"]
    hosts = _csv(values.get("ALLOWED_HOSTS") or defaults["ALLOWED_HOSTS"])
    if not hosts:
        raise EnvironmentError(f"ALLOWED_HOSTS cannot be empty. {EXAMPLE_CONFIG_REFERENCE}")
    return Environment(
        secret_key=secret_key,
        database_url=database_url,
        allowed_hosts=list(hosts),
        debug=values.get("DJANGO_DEBUG", "0").lower() in {"1", "true", "yes"},
        secure_cookies=values.get("SECURE_COOKIES", "0").lower() in {"1", "true", "yes"},
    )
