"""Django entry point for the finite or continuously polling worker."""

from __future__ import annotations

import time

from django.core.management.base import BaseCommand

from token_budget_platform.application.jobs import JobRuntime, TransactionalOutbox


class Command(BaseCommand):
    help = "Dispatch transactional outbox events and process leased jobs."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--once", action="store_true", help="Run one dispatch and one job poll, then exit."
        )
        parser.add_argument(
            "--poll-interval", type=float, default=1.0, help="Seconds between polls."
        )
        parser.add_argument(
            "--worker-id", default=None, help="Stable identifier used for job leases."
        )

    def handle(self, *args, **options):
        runtime = JobRuntime(worker_id=options["worker_id"])
        outbox = TransactionalOutbox()
        while True:
            outbox.dispatch()
            processed = runtime.run_once()
            if options["once"]:
                self.stdout.write(
                    self.style.SUCCESS(f"Worker poll complete; processed={int(processed)}.")
                )
                return
            if not processed:
                time.sleep(max(0.05, options["poll_interval"]))
