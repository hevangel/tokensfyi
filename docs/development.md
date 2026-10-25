# Local development

## Prerequisites

Install Python 3.13 and Docker Desktop (Windows 11 or macOS) or Docker Engine plus Compose (Ubuntu LTS). Copy `.env.example` to `.env` and replace the local placeholder values. `.env` is ignored by Git and must never contain production credentials.

## Start

From the repository root, run one command:

```text
docker compose up --build
```

The web service is available at <http://localhost:8000>. Liveness is available at `/health/live`; API liveness is available at `/api/v1/health/live`. The local Compose services are PostgreSQL, MinIO, ClamAV, Mailpit, web, and worker. They are development substitutes and do not make AWS calls.

For a dependency-free Django smoke run, set `DJANGO_SETTINGS_MODULE=config.settings.local` and run `uv run python manage.py runserver` (local settings default to SQLite when `DATABASE_URL` is absent).

## Configuration

All runtime settings are read from environment variables. Production settings fail closed and list every missing required variable with a link to `.env.example`; developer secrets stay outside version control. Do not put credentials in source, Dockerfiles, fixtures, or logs.

## Frontend assets

The small TypeScript enhancement bundle is built with `npm --prefix frontend run build`. HTMX is checked into `static/vendor/htmx`; no CDN is required for local or production startup. Server-rendered HTML remains usable when JavaScript is disabled.

## Debugging

Compose mounts the repository into the web container and exposes port 5678 for an opt-in debugger. Browser source remains in `static/src` and is not minified in development. Attach a Python debugger to the web process only from a trusted local machine; do not expose the debug port in shared networks.
