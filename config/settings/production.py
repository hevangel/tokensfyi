"""Production settings with strict external configuration.

All runtime values come from environment variables or AWS parameter store.
The same image serves web, worker, and scanner roles; SERVICE_ROLE selects behavior.
"""

from .base import *  # noqa: F403

DEBUG = False
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# AWS runtime adapter is built lazily so Django can start without boto3 in test
AWS_RUNTIME_ENABLED = True
