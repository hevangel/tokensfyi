# Token Budget Platform

A privacy-preserving benchmark for organizational AI token budgets. The application is a Python 3.13/Django 5.2 modular monolith with server-rendered HTML, a versioned API transport, and replaceable local/AWS adapters.

## Start locally

Prerequisites are Python 3.13 and Docker Desktop/Engine with Compose. From the repository root, run:

```text
docker compose up --build
```

Open <http://localhost:8000>. See [`docs/development.md`](docs/development.md) for configuration, debugging, frontend assets, and local dependency substitutes. Developer secrets belong in the ignored `.env` file; `.env.example` contains safe names and placeholders only.

## Verification

```text
uv run poe verify
```

runs formatting checks, linting, type checking, and the full test suite with pinned dependencies. Useful shortcuts: `uv run poe format` (format), `uv run poe check-fix` (fix + re-verify). The lockfile is checked in and dependency versions are pinned. The same OCI application image is used for the web and worker roles.

## What is implemented

Core journeys are complete end-to-end on the server-rendered HTML transport and mirrored on the JSON API under `/api/v1`:

- **Public catalog** — landing summary, browse with case-insensitive containment search, filters, five stable sorts, 25-record pages with counts, distinct empty states, organization profiles with privacy-threshold aggregates and disclosure, record detail with provenance/correction/staleness/dispute indicators, and aligned two-to-five record comparison.
- **Contribution** — validated submission form (shared validator with HTML and API), exact public preview, atomic idempotent confirmation (record + immutable version + moderation case + hashed capability + outbox events), and a management link shown exactly once.
- **Contributor management** — session-scoped status view with moderation feedback, amendments that append immutable versions and re-enter review, immediate withdrawal with capability revocation, and non-enumerating email recovery.
- **Moderation** — staff sign-in (Argon2id + TOTP/recovery codes with exact five-failures lockout), priority queue with claim leases, approve/reject/changes/suppress decisions enforcing the publication state machine, community reports with synchronous suppression for sensitive categories and Disputed status for ordinary reports, and append-only audit records with independent failure handling.
- **Evidence** — encrypted quarantine intake with allowlist validation, ClamAV scanning through the job runtime, and role-and-scan-gated moderator downloads (`/api/v1/staff/evidence/<id>`).
- **Operations** — liveness/readiness endpoints, signed backup-event ingestion gating mutations, source-scoped rate limiting, correlation IDs, and structurally sanitized logging.
- **Local tooling** — `manage.py setup|seed|reset|preflight|worker` and a deterministic 24-record baseline seed; Compose provides PostgreSQL, MinIO, ClamAV, and Mailpit substitutes with no AWS calls.
- **AWS** — CDK stacks for isolated VPC/RDS/S3/KMS/IAM/backup plus ECS Fargate compute (web, worker, scanner roles on one image) with fail-closed environment policy validation.

Remaining work is tracked in [`.kiro/specs/token-budget-platform/tasks.md`](.kiro/specs/token-budget-platform/tasks.md): the Hypothesis property-test suite, Playwright/axe accessibility journeys, retention/erasure jobs, OpenAPI contract artifacts, CI release gates, and deployment automation.
