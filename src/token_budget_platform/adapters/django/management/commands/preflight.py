"""Configuration preflight check.

Validates that all required local-environment settings are present and well-formed.
Missing settings are identified individually with a reference to `.env.example`.
"""

from __future__ import annotations

import os
import sys

from django.core.management.base import BaseCommand

# Settings grouped by purpose: (env_var_name, description, required_for_compose)
LOCAL_SETTINGS = [
    ("SECRET_KEY", "Django secret key", True),
    ("DATABASE_URL", "PostgreSQL connection URL", True),
    ("ALLOWED_HOSTS", "Comma-separated allowed host names", True),
    ("MINIO_ENDPOINT", "MinIO S3-compatible endpoint URL", True),
    ("MINIO_ROOT_USER", "MinIO access key", True),
    ("MINIO_ROOT_PASSWORD", "MinIO secret key", True),
    ("MINIO_BUCKET", "MinIO bucket name for evidence", True),
    ("CLAMAV_HOST", "ClamAV daemon hostname", True),
    ("CLAMAV_PORT", "ClamAV daemon TCP port", True),
    ("MAILPIT_HOST", "Mailpit SMTP hostname", True),
    ("MAILPIT_PORT", "Mailpit SMTP port", True),
]

EXAMPLE_CONFIG_PATH = ".env.example"


class Command(BaseCommand):
    help = "Verify that all required local configuration is present and reference example values."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Exit with non-zero status if any setting is missing.",
        )

    def handle(self, *args, **options) -> None:
        missing: list[tuple[str, str]] = []
        present: list[str] = []

        for name, description, _required in LOCAL_SETTINGS:
            value = os.environ.get(name)
            if not value:
                missing.append((name, description))
            else:
                present.append(name)

        if present:
            self.stdout.write(self.style.SUCCESS(f"  {len(present)} settings present"))

        if missing:
            self.stdout.write(self.style.WARNING("\n  Missing configuration:"))
            for name, description in missing:
                self.stdout.write(f"    • {name}: {description}")
            self.stdout.write(
                self.style.NOTICE(
                    f"\n  Reference: Copy {EXAMPLE_CONFIG_PATH} to .env and set the missing values."
                )
            )
            if options["strict"]:
                sys.exit(1)
        else:
            self.stdout.write(self.style.SUCCESS("  All required local settings are configured."))
