"""One-command local environment setup.

Runs preflight checks, applies migrations, and loads seed data. Designed to
be idempotent: running it again after a successful setup is safe.
"""

from __future__ import annotations

import os

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Run migrations, verify object-storage settings, and load seed data for local development."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--skip-seed",
            action="store_true",
            help="Skip loading seed data.",
        )

    def handle(self, *args, **options) -> None:
        self.stdout.write("  Running configuration preflight...")
        call_command("preflight")

        self.stdout.write("\n  Applying migrations...")
        call_command("migrate", verbosity=1)

        self._check_object_storage_settings()

        if not options["skip_seed"]:
            from token_budget_platform.adapters.django.models import TokenBudgetRecord

            if TokenBudgetRecord.objects.exists():
                self.stdout.write(
                    "  Seed data already present; skipping. Use `manage.py reset` to reload."
                )
            else:
                self.stdout.write("\n  Loading seed data...")
                call_command("seed")

        self.stdout.write(self.style.SUCCESS("\n  Setup complete. http://localhost:8000"))

    def _check_object_storage_settings(self) -> None:
        """Report object-storage configuration without making network calls.

        The Compose stack provisions the MinIO bucket itself; bucket
        provisioning over HTTP belongs to the object-storage adapter, which
        authenticates properly, not to this bootstrap command.
        """
        from django.conf import settings

        configured = bool(getattr(settings, "OBJECT_STORAGE_ENDPOINT", "")) or bool(
            os.environ.get("MINIO_ENDPOINT", "")
        )
        if configured:
            self.stdout.write("  Object storage endpoint configured.")
        else:
            self.stdout.write(
                "  Object storage not configured; evidence features will use the"
                " in-process local adapter."
            )
