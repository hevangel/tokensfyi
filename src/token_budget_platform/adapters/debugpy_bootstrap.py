"""Optional debugpy attachment for local container development.

When DEBUGPY_ENABLE=1 is set, this module starts the debugpy listener on
the configured port (default 5678). Source maps are preserved because Compose
mounts the repository at /app, keeping original file paths intact for both
server-side breakpoints and browser devtools.

Import this module early in the WSGI/manage.py entrypoint when local settings
are active. It is safe to import when debugpy is not installed (production image)
because the import is guarded.
"""

from __future__ import annotations

import os


def maybe_attach_debugger() -> None:
    """Start debugpy listener if opt-in environment variable is set.

    Only activates when DEBUGPY_ENABLE=1 and debugpy is importable.
    The listener binds to 0.0.0.0 so Docker-mapped port 5678 is reachable
    from the host IDE.
    """
    if os.environ.get("DEBUGPY_ENABLE", "0") not in ("1", "true", "yes"):
        return

    try:
        import debugpy
    except ImportError:
        return

    port = int(os.environ.get("DEBUGPY_PORT", "5678"))
    if not debugpy.is_client_connected():
        debugpy.listen(("0.0.0.0", port))  # noqa: S104
        print(f"  debugpy: listening on 0.0.0.0:{port} — attach your IDE debugger.")
