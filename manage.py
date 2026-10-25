#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""

import os
import sys


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

    # Opt-in debugpy attachment for local container development (Req 13.6).
    from token_budget_platform.adapters.debugpy_bootstrap import maybe_attach_debugger

    maybe_attach_debugger()

    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
