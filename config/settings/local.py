"""Developer settings. Local defaults use SQLite and never contain secrets."""

from .base import *  # noqa: F403

DEBUG = True
INTERNAL_IPS = ["127.0.0.1"]
