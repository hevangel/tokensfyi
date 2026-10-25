"""Shared Django settings; deployment-specific values come from the environment."""

import os
from pathlib import Path

from token_budget_platform.config.environment import load_environment

BASE_DIR = Path(__file__).resolve().parents[2]
ENVIRONMENT = load_environment()

SECRET_KEY = ENVIRONMENT.secret_key
DEBUG = ENVIRONMENT.debug
ALLOWED_HOSTS = ENVIRONMENT.allowed_hosts
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "token_budget_platform.adapters.django.apps.AdapterConfig",
    "token_budget_platform.transports.html",
    "token_budget_platform.transports.api",
]

MIDDLEWARE = [
    "token_budget_platform.transports.middleware.correlation.CorrelationIdMiddleware",
    "token_budget_platform.transports.middleware.error_translation.SafeErrorTranslationMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "token_budget_platform.transports.middleware.rate_limit.SourceScopedRateLimitMiddleware",
    "token_budget_platform.transports.middleware.backup_gate.BackupMutationGateMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]

DATABASES = {"default": ENVIRONMENT.database_config}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
CSRF_COOKIE_SECURE = ENVIRONMENT.secure_cookies
SESSION_COOKIE_SECURE = ENVIRONMENT.secure_cookies
CSRF_COOKIE_HTTPONLY = False
SESSION_COOKIE_HTTPONLY = True
SECURE_SSL_REDIRECT = ENVIRONMENT.secure_cookies

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
}

# Deployment metadata for structured telemetry (Req 16.5)
DEPLOYMENT_VERSION = os.environ.get("DEPLOYMENT_VERSION", "local-dev")

# Structured logging with sanitization (Req 9.8, 16.5)
from token_budget_platform.adapters.logging import get_structured_logging_config  # noqa: E402

LOGGING = get_structured_logging_config(
    deployment_version=DEPLOYMENT_VERSION,
    environment=os.environ.get("ENVIRONMENT", "local"),
)
