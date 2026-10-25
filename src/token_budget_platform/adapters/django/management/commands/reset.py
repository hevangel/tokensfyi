"""Reset the local environment to baseline state.

Flushes all application data, re-runs migrations, and reloads the
deterministic seed. This is the documented reset command for local development.
"""

from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Reset local database to the documented baseline seed state."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--no-seed",
            action="store_true",
            help="Flush and migrate but skip seeding.",
        )
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Skip the confirmation prompt.",
        )

    def handle(self, *args, **options) -> None:
        if not options["yes"]:
            confirm = input("This will destroy all local data and reload seed. Continue? [y/N] ")
            if confirm.lower() not in ("y", "yes"):
                self.stdout.write("  Aborted.")
                return

        self.stdout.write("  Flushing database...")
        call_command("flush", "--no-input", verbosity=0)

        self.stdout.write("  Running migrations...")
        call_command("migrate", "--run-syncdb", verbosity=0)

        if not options["no_seed"]:
            self.stdout.write("  Loading seed data...")
            call_command("seed")

        self.stdout.write(self.style.SUCCESS("  Reset complete."))
