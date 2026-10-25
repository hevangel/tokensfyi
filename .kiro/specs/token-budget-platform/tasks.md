# Implementation Plan: Token Budget Platform

## Overview

Implement the Python 3.13/Django 5.2 modular monolith incrementally: establish shared domain and local infrastructure first, add governance and public-read behavior, then contribution/moderation/privacy journeys, cross-cutting accessibility and operations, deterministic verification, and AWS deployment. Every implementation task references acceptance criteria; optional test subtasks turn the design's 37 correctness properties and critical examples into automated checks.

## Tasks

- [ ] 1. Establish the application foundation and reproducible local runtime
  - [x] 1.1 Bootstrap the pinned Python/Django project and modular boundaries
    - Create the Django project, domain/application modules, HTML/API transports, adapter packages, settings schema, pinned lockfile, OCI images, and vendored frontend asset pipeline.
    - Configure externalized environment settings and keep developer secrets in ignored files while exposing only safe names/examples to startup code.
    - _Requirements: 13.1, 13.2, 13.3, 13.8, 15.4_

  - [x] 1.2 Implement core persistence models, constraints, migrations, and repository boundaries
    - Create organizations, controlled values, records/versions, normalized metrics, aggregates, drafts, capabilities, contacts, evidence, moderation, staff, audit/security events, jobs/outbox, idempotency, and operational-gate models.
    - Add UUIDv7 identifiers, timestamps, optimistic versions, database checks, restrictive foreign keys, public-eligibility repositories, search/filter indexes, and monthly partition support.
    - _Requirements: 1.3, 4.2, 7.9, 7.10, 10.11, 12.4, 16.2_

  - [x] 1.3 Implement infrastructure ports, transactional outbox, and idempotent worker runtime
    - Define object storage, scanning, notification, key management, clock, telemetry, and backup-gate protocols with local adapters.
    - Implement identifier-only jobs, leases, retries, terminal outcomes, replay safety, cache invalidation, and atomic outbox enqueueing.
    - _Requirements: 6.1, 7.1, 9.7, 10.7, 10.8, 14.2, 14.3, 15.3_

  - [x] 1.4 Build the one-command local environment, baseline seed, reset, and debug support
    - Wire Docker Compose for PostgreSQL, MinIO, ClamAV, Mailpit, web, and worker without AWS calls; add finite setup/reset commands and configuration preflight.
    - Seed at least 20 deterministic records spanning three organizations, three workload categories, and every verification status; preserve source paths, browser source maps, and opt-in localhost debugger attachment.
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.7, 13.8_

  - [~] 1.5 Write the property test for complete configuration diagnostics
    - **Property 33: Configuration diagnostics enumerate every omission**
    - Generate every subset of missing required settings and assert exact diagnostics plus the safe example-configuration reference.
    - **Validates: Requirements 13.7**

  - [~] 1.6 Write foundation integration tests
    - Test migrations/constraints, local adapter contracts, worker lease recovery, idempotent jobs, deterministic seed/reset checksums, no-AWS startup, and ignored-secret behavior.
    - _Requirements: 13.2, 13.3, 13.4, 13.5, 13.8, 14.2, 14.3_

- [ ] 2. Implement governed classifications, normalization, aggregates, and organization identity
  - [x] 2.1 Implement versioned controlled vocabularies, aliases, and canonical organization merging
    - Expose only active provider, product, workload, size-band, and region values to new drafts while preserving historical references.
    - Resolve normalized aliases and merge organizations transactionally without changing record IDs, versions, cases, or audit targets.
    - _Requirements: 12.1, 12.2, 12.3_

  - [x] 2.2 Implement exact normalization and methodology versioning
    - Calculate inclusive-day 30-day, per-seat, per-execution, usage, and unavailable metrics with Decimal values, formula IDs, source values, and no imputation.
    - Persist active methodology versions and enqueue deterministic recalculation when a rule changes.
    - _Requirements: 3.3, 3.4, 3.5, 12.7, 12.8_

  - [x] 2.3 Implement privacy-threshold aggregates and calendar staleness
    - Compute eligible-record count/minimum/maximum/median only for groups of at least three, including dimensions and rule disclosure.
    - Implement the documented 12-calendar-month boundary with month clamping.
    - _Requirements: 4.3, 4.4, 4.5, 9.9_

  - [~] 2.4 Write the property test for exact source-preserving normalization
    - **Property 8: Normalization is exact and preserves sources**
    - **Validates: Requirements 3.3, 3.5, 12.7, 14.4**

  - [~] 2.5 Write the property test for explicit missing normalization inputs
    - **Property 9: Missing normalization inputs are explicit**
    - **Validates: Requirements 3.4, 14.4**

  - [~] 2.6 Write the property test for privacy-threshold aggregate accuracy
    - **Property 10: Aggregates are accurate only above the privacy threshold**
    - **Validates: Requirements 4.3, 4.4**

  - [~] 2.7 Write the property test for calendar staleness
    - **Property 11: Staleness follows the calendar boundary**
    - **Validates: Requirements 4.5**

  - [~] 2.8 Write the property test for controlled-value temporal behavior
    - **Property 30: Controlled-value versions affect only subsequent submissions**
    - **Validates: Requirements 12.1**

  - [~] 2.9 Write the property test for alias and merge preservation
    - **Property 31: Alias resolution and organization merge preserve identity-linked data**
    - **Validates: Requirements 12.2, 12.3**

  - [~] 2.10 Write the property test for deterministic methodology recalculation
    - **Property 32: Methodology changes deterministically recalculate affected metrics**
    - **Validates: Requirements 12.8**

  - [~] 2.11 Write governance unit and integration tests
    - Cover Decimal rounding boundaries, leap dates, median cases, database invariants, vocabulary deactivation, alias cycles, merge transactions, and recalculation job idempotency.
    - _Requirements: 3.3, 3.4, 4.3, 4.4, 4.5, 12.1, 12.2, 12.3, 12.7, 12.8_

- [ ] 3. Build the public catalog, details, search, and comparison journeys
  - [x] 3.1 Implement the allowlisted public projection and landing/browse endpoints
    - Build the sole private-to-public DTO boundary, scope explanation, published count/update date, fixed headings, status definitions, 25-record maximum pages, and dataset empty state.
    - Ensure no private model or serializer can enter public templates or JSON responses.
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 4.2, 5.8, 6.5, 9.3, 9.5_

  - [x] 3.2 Implement public search, filters, stable sorting, counts, and signed URL state
    - Support case-insensitive organization/provider/product containment, every documented filter and sort, deterministic cursor pagination, canonical query encoding, matching counts, and clear-active-criteria empty states.
    - Reject invalid, tampered, or criteria-mismatched cursors with safe errors.
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_

  - [x] 3.3 Implement organization profiles and record detail queries
    - Render canonical organization metadata, eligible records, aggregate disclosures/suppression explanations, provenance dates, methodology notes, correction dates, and stale/disputed indicators.
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 9.9, 12.5, 12.6_

  - [x] 3.4 Implement ordered comparison sets and accessible aligned output
    - Accept two through five distinct eligible IDs, preserve requested order, align every required field, show exact source/formula/missing-input details, preserve original values, and encode IDs in the URL.
    - Provide a semantic table/text equivalent as the authoritative representation of any visual enhancement.
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 11.4_

  - [~] 3.5 Write the property test for the public projection boundary
    - **Property 1: Public projection is complete and privacy preserving**
    - **Validates: Requirements 1.3, 4.2, 5.6, 5.8, 9.3, 9.5**

  - [~] 3.6 Write the property test for bounded, complete pagination and counts
    - **Property 2: Pagination is bounded, complete, and counted**
    - **Validates: Requirements 1.5, 2.5**

  - [~] 3.7 Write the property test for normalized containment search
    - **Property 3: Search equals case-insensitive containment**
    - **Validates: Requirements 2.1**

  - [~] 3.8 Write the property test for filter intersection
    - **Property 4: Filters equal predicate intersection**
    - **Validates: Requirements 2.2**

  - [~] 3.9 Write the property test for stable monotonic sorting
    - **Property 5: Sorts are stable and monotonic**
    - **Validates: Requirements 2.3**

  - [~] 3.10 Write the property test for URL codec round trips
    - **Property 6: Shareable URL state round-trips**
    - **Validates: Requirements 2.4, 3.6**

  - [~] 3.11 Write the property test for exact comparison selection and alignment
    - **Property 7: Comparison selection and alignment are exact**
    - **Validates: Requirements 3.1, 3.2**

  - [~] 3.12 Write the property test for dispute-controlled public status
    - **Property 12: An open dispute controls public verification status**
    - **Validates: Requirements 4.6**

  - [~] 3.13 Write public catalog API and repository integration tests
    - Cover zero/25/26 records, fixed headings, query plans, cursor signatures, eligibility predicates, public sentinel leakage, status copy, correction display, and comparison error cases.
    - _Requirements: 1.1–1.6, 2.1–2.6, 3.1–3.6, 4.1–4.6, 6.5, 9.3, 9.9_

- [x] 4. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 5. Implement contribution drafts, validation, preview, and confirmation
  - [x] 5.1 Implement server-side submission drafts and shared validation
    - Build HTML/API draft steps for every required field plus coherent optional usage, cost, allocation, attribution, evidence, and contact choices.
    - Apply documented Unicode, controlled-value, integer, Decimal, date/timezone, URL, currency, length, and cross-field rules; preserve valid sibling values and return field-linked corrective errors.
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 9.1, 9.2, 9.4, 10.6, 11.3_

  - [x] 5.2 Implement exact preview and atomic idempotent confirmation
    - Generate previews through the public projection and atomically create one Unverified record, immutable initial version, moderation queue case, random private reference, hashed management capability, and outbox events.
    - Return only the raw one-time management token to the contributor and never persist or log it.
    - _Requirements: 5.6, 5.7, 5.8, 5.9, 7.1, 8.1, 9.3_

  - [x] 5.3 Implement versioned duplicate detection
    - Canonicalize organization, scope, product, token budget, and period into a fingerprint and flag exact matches for moderator review without auto-rejection.
    - _Requirements: 5.10_

  - [~] 5.4 Write the property test for complete corrective validation
    - **Property 13: Validation rejects precisely with corrective errors**
    - Generate valid and invalid boundary payloads and assert every invalid/missing field is identified without losing valid siblings.
    - **Validates: Requirements 5.4, 5.5, 14.5**

  - [~] 5.5 Write the property test for atomic idempotent confirmation
    - **Property 14: Confirmation is atomic and idempotent**
    - **Validates: Requirements 5.7, 5.8, 7.1, 8.1**

  - [~] 5.6 Write the property test for duplicate fingerprints
    - **Property 15: Duplicate flags equal the versioned fingerprint rule**
    - **Validates: Requirements 5.10**

  - [~] 5.7 Write submission form and API integration tests
    - Cover all required/optional field combinations, preview equality, anonymous attribution, transaction rollback, repeated idempotency keys, queue creation, reference secrecy, and accessible error focus.
    - _Requirements: 5.1–5.10, 7.1, 8.1, 9.1–9.4, 11.3_

- [ ] 6. Implement encrypted evidence handling and confidence transitions
  - [x] 6.1 Implement evidence intake, quarantine storage, and scanning jobs
    - Accept private file/link/explanation evidence; enforce the 10 MiB PDF/PNG/JPEG/plain-text/CSV allowlist using declared and detected types while preserving retry opportunity after rejection.
    - Stream with per-evidence envelope encryption into private quarantine, block access while pending/error, scan through ClamAV, record sanitized security outcomes, and retain failed files quarantined.
    - _Requirements: 6.1, 6.4, 9.3, 10.2, 10.7, 10.8, 10.9_

  - [x] 6.2 Implement moderator evidence access and verification decisions
    - Authorize resource-scoped streaming only for clean evidence; prevent object URLs and safe-encode download headers.
    - Apply Evidence_Submitted, Verified, and Rejected transitions and expose rejection reasons only through contributor management.
    - _Requirements: 6.2, 6.3, 6.6, 6.7, 10.4, 10.7_

  - [~] 6.3 Write the property test for evidence and verification state transitions
    - **Property 16: Evidence and verification transitions obey the state machine**
    - **Validates: Requirements 6.3, 6.6, 6.7, 10.9**

  - [~] 6.4 Write the property test for role-and-scan-gated evidence access
    - **Property 17: Evidence access requires both role and clean scan state**
    - **Validates: Requirements 6.2, 10.7**

  - [~] 6.5 Write evidence adapter and lifecycle integration tests
    - Exercise MinIO encryption, size/type rejection cleanup, ClamAV clean/infected/error paths, retries, security events, moderator authorization, and absence from public DTOs.
    - _Requirements: 6.1–6.7, 9.3, 10.2, 10.7, 10.8, 10.9_

- [ ] 7. Implement staff identity, moderation, reports, and independent auditing
  - [x] 7.1 Implement staff authentication, MFA, exact lockout, and RBAC
    - Add Argon2id credentials, TOTP/recovery codes, secure staff sessions, five-failures-in-15-minutes lockout, Administrator provisioning, and explicit role/resource policies.
    - Denied moderation requests must change no domain state and emit sanitized authorization events.
    - _Requirements: 7.13, 10.3, 10.4, 10.5_

  - [x] 7.2 Implement moderation queue leases and publication decisions
    - Build priority ordering, claim/release leases, optimistic version checks, approval, rejection, change requests, and discrepancy amendment requests with structured and contributor-visible reasons.
    - Apply public eligibility consistently across browse, search, comparison, profiles, detail, and aggregates.
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6_

  - [x] 7.3 Implement community reports, synchronous sensitive suppression, and dispute resolution
    - Validate category/explanation, create linked cases, set Disputed status for ordinary reports, suppress privacy-sensitive records before returning, purge caches, and resolve only to allowed states with complete decision metadata.
    - _Requirements: 4.6, 7.7, 7.8, 7.9, 9.5_

  - [x] 7.4 Implement append-only audit records with independent failure handling
    - Attempt audit append exactly once after every privileged transaction without rolling back a committed action.
    - On audit failure, emit only sanitized high-severity operational metadata and preserve immutable audit storage/permissions.
    - _Requirements: 7.10, 7.11, 7.12, 9.8, 10.11_

  - [x] 7.5 Build moderator and administrator HTML/API workflows
    - Wire queue filters, case decisions, evidence decisions, organization merges, controlled-value administration, and authorization-safe error handling through shared application commands.
    - _Requirements: 6.2, 6.6, 6.7, 7.2, 7.9, 7.13, 10.4, 12.1, 12.3_

  - [~] 7.6 Write the property test for moderation-controlled public eligibility
    - **Property 18: Moderation transitions enforce public eligibility**
    - **Validates: Requirements 7.3, 7.4**

  - [~] 7.7 Write the property test for report case creation and synchronous suppression
    - **Property 19: Reports create cases and sensitive reports suppress synchronously**
    - **Validates: Requirements 7.7, 7.8**

  - [~] 7.8 Write the property test for traceable valid case resolution
    - **Property 20: Case resolution is traceable and state-valid**
    - **Validates: Requirements 7.9**

  - [~] 7.9 Write the property test for immutable privileged-action audit attempts
    - **Property 21: Privileged actions attempt immutable audit records**
    - **Validates: Requirements 7.10**

  - [~] 7.10 Write the property test for audit-failure transaction independence
    - **Property 22: Audit failure never rolls back the privileged action**
    - **Validates: Requirements 7.11, 7.12**

  - [~] 7.11 Write the property test for the authorization policy matrix
    - **Property 23: Authorization matches the role/resource policy**
    - **Validates: Requirements 7.13, 10.4**

  - [~] 7.12 Write the property test for exact privileged authentication lockout
    - **Property 27: Privileged authentication lockout follows the exact window**
    - **Validates: Requirements 10.5**

  - [~] 7.13 Write moderation and identity integration tests
    - Cover MFA/recovery, session rotation, concurrent leases/decisions, all publication states, contributor feedback, sensitive/non-sensitive reports, cache invalidation, immutable auditing, and injected audit failure.
    - _Requirements: 4.6, 6.2, 6.6, 6.7, 7.1–7.13, 9.5, 10.3–10.5, 10.11_

- [x] 8. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 9. Implement contributor management, privacy controls, and retention
  - [x] 9.1 Implement secure capability exchange and scoped management sessions
    - Issue 256-bit fragment capabilities, persist only versioned HMAC digests, compare in constant time, enforce scope/expiry/revocation, rotate safely, and redirect to a clean session URL.
    - Return non-enumerating denial/recovery behavior for invalid or expired links and an accessible script-blocked path.
    - _Requirements: 8.1, 8.2, 8.3, 8.6, 10.6, 11.1_

  - [x] 9.2 Implement submission status, amendment, withdrawal, and recovery commands
    - Show the submission, verification/publication state, moderation feedback, and withdrawn records to the owning management session.
    - Append amendment versions and requeue; remove withdrawals from every public repository within 24 hours; send recovery only to the previously bound email using non-enumerating responses.
    - _Requirements: 7.5, 7.6, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 12.4, 12.5_

  - [-] 9.3 Implement privacy erasure, redaction, and data minimization
    - Restrict contact collection to purpose-bound optional email, encrypt it separately, process deletion/irreversible anonymization within 30 days, and destroy key references so backup copies are unreadable.
    - Warn on free text, block likely secrets/personal data, and redact confirmed exposure from every public representation without exposing private history.
    - _Requirements: 8.8, 9.1, 9.2, 9.3, 9.4, 9.5_

  - [~] 9.4 Implement executable retention policy and idempotent cleanup jobs
    - Generate the public retention page from version-controlled policy data covering submissions, evidence, personal data, cases, backups, logs, and audits.
    - Delete/crypto-erase evidence at 365 days after final resolution unless legally held, enforce audit minimum retention, and record sanitized counts/overdue alerts.
    - _Requirements: 9.6, 9.7, 10.11, 15.8_

  - [~] 9.5 Implement safe free-text handling and contributor-visible rendering
    - Normalize and bound text, render it inert under every HTML/JSON context, keep status/correction indicators non-color-dependent, and never expose management capabilities, evidence, contact, or unreviewed text.
    - _Requirements: 6.5, 9.3, 9.4, 10.6, 11.6_

  - [~] 9.6 Write the property test for amendment history and review re-entry
    - **Property 24: Amendments preserve history and re-enter review**
    - **Validates: Requirements 8.4, 12.4, 12.5**

  - [~] 9.7 Write the property test for evidence/audit retention boundaries
    - **Property 25: Retention respects age and legal hold**
    - **Validates: Requirements 9.7, 10.11**

  - [~] 9.8 Write the property test for inert untrusted text
    - **Property 28: Untrusted text cannot become executable markup**
    - **Validates: Requirements 10.6**

  - [~] 9.9 Write contributor-management and privacy integration tests
    - Cover capability secrecy/rotation, invalid/expired links, withdrawn access, amendment races, 24-hour withdrawal, recovery binding, erasure deadlines, crypto-erasure, legal holds, retention boundaries, and public redaction.
    - _Requirements: 8.1–8.8, 9.1–9.7, 10.6, 10.11, 12.4, 12.5_

- [ ] 10. Apply accessible, responsive presentation to every core journey
  - [x] 10.1 Build shared semantic page, form, table, status, and error components
    - Implement skip links, unique titles, one primary heading, descriptive links, native labeled controls, logical focus, visible focus indicators, field-linked errors, focused summaries, live regions, and accessible status names.
    - Reflow non-table content from 320–1920 CSS pixels, support table overflow instructions, reduced motion, forced colors, text spacing, and keyboard-safe dialog behavior.
    - _Requirements: 11.1, 11.2, 11.3, 11.5, 11.6, 11.7_

  - [x] 10.2 Wire accessible components through all core HTML journeys
    - Apply the components to browse, search/filter, comparison, organization/detail, submission, management, community report, Moderator, and Administrator flows.
    - Keep comparison/chart data available in a semantic table/text source and preserve core behavior without client-side enhancement.
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7_

  - [~] 10.3 Write automated accessibility component tests
    - Assert titles/headings/landmarks, labels/descriptions, error-summary focus, status names, keyboard order, chart/table equivalence, and responsive markup contracts.
    - _Requirements: 11.1–11.7, 14.6_

  - [~] 10.4 Write Playwright and axe-core journey tests
    - Exercise every core journey at representative 320–1920 widths with keyboard/focus assertions and axe checks; emit the versioned manual WCAG 2.2 AA checklist as a verification artifact.
    - _Requirements: 11.1–11.7, 14.6_

- [ ] 11. Implement cross-cutting API security, safe failures, observability, and service controls
  - [-] 11.1 Implement versioned API contracts and request safety middleware
    - Add checked-in OpenAPI-backed `/api/v1` schemas, UUID/date/decimal conventions, CSRF, secure cookies/headers, host/origin/body limits, optimistic `If-Match`, idempotency-key replay/conflict behavior, and typed problem responses.
    - Require TLS 1.2+ in deployed settings and neutralize executable markup without bypassing context-sensitive escaping.
    - _Requirements: 10.1, 10.4, 10.6, 14.7, 14.8, 16.11_

  - [x] 11.2 Implement source-scoped submission/report rate limits
    - Use keyed network-source pseudonyms and deterministic windows; return safe 429 responses with a positive `Retry-After` while leaving other sources unaffected.
    - _Requirements: 10.10_

  - [x] 11.3 Implement correlation, structurally sanitized telemetry, and safe error translation
    - Propagate correlation IDs across requests, SQL, outbox/jobs, scans, and notifications; emit allowlisted structured logs/events with deployment and operation metadata.
    - Redact credentials, capabilities, personal data, evidence, bodies, raw private text, and exception details from all operational output.
    - _Requirements: 7.12, 9.8, 16.5, 16.11_

  - [x] 11.4 Implement liveness/readiness and dependency health endpoints
    - Return deployment version plus coarse dependency and backup-gate states without private details; distinguish read availability from blocked data-changing readiness.
    - _Requirements: 15.5, 16.3_

  - [x] 11.5 Implement signed backup-state ingestion and the production mutation gate
    - Close the gate on required backup failure, reject every non-exempt mutation without state change, and reopen only after a succeeding required-backup event.
    - _Requirements: 16.7, 16.8_

  - [~] 11.6 Implement bounded operational metrics, monthly reports, alerts, and maintenance notices
    - Instrument request/latency/error, moderation queue, storage, scans, jobs, backups, and availability without sensitive metric labels.
    - Produce Service_Month metrics, alarm on five one-minute readiness failures with timely operator notification, and enforce 48-hour planned-maintenance notice data.
    - _Requirements: 16.1, 16.4, 16.6, 16.10_

  - [~] 11.7 Implement scalable public-read behavior and performance gates
    - Add safe anonymous caching/invalidation, connection bounds, indexed query paths, synthetic million-record loading, plan assertions, and a reproducible 100-RPS search/detail profile with p95 measurement.
    - _Requirements: 16.1, 16.2_

  - [~] 11.8 Write the property test for structurally sanitized operational output
    - **Property 26: Operational output is structurally sanitized**
    - **Validates: Requirements 7.12, 9.8, 16.5**

  - [~] 11.9 Write the property test for source-scoped rate limiting
    - **Property 29: Rate limiting is source-scoped and communicates retry time**
    - **Validates: Requirements 10.10**

  - [~] 11.10 Write the property test for backup-controlled mutations
    - **Property 36: Backup events gate production mutations safely**
    - **Validates: Requirements 16.8**

  - [~] 11.11 Write the property test for safe classified public failures
    - **Property 37: Public failures have safe classified responses**
    - **Validates: Requirements 16.11**

  - [~] 11.12 Write API, security, observability, resilience, and performance tests
    - Validate OpenAPI compatibility, headers/CSRF/sessions, encryption configuration, idempotency/ETags, log sentinel redaction, health privacy, alarm delivery, backup-gate faults, monthly calculations, query plans, and the million-record/100-RPS p95 gate.
    - _Requirements: 9.8, 10.1, 10.2, 10.4, 10.6, 10.10, 14.7, 14.8, 16.1–16.8, 16.10, 16.11_

- [x] 12. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 13. Automate finite verification and releasable-artifact gating
  - [~] 13.1 Implement the single finite verification command and isolated test orchestration
    - Wire formatting, lint, type checks, unit/property/integration/API, Playwright/axe, infrastructure assertions, and report generation into `uv run poe verify` with deterministic schemas, buckets, mailboxes, clocks, and teardown.
    - Skip only explicitly AWS-tagged tests when credentials are absent and run all other checks once without watch mode.
    - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5, 14.6_

  - [~] 13.2 Implement checked schema, test-data, and accessibility verification artifacts
    - Validate generated OpenAPI against checked-in contracts, preserve minimized Hypothesis counterexamples as safe CI artifacts, and emit automated accessibility results plus the manual WCAG checklist.
    - _Requirements: 14.2, 14.4, 14.5, 14.6_

  - [~] 13.3 Implement CI release gates and immutable artifact promotion
    - Require verification, static analysis, dependency/container vulnerability scanning, secret scanning, and infrastructure policy checks before building/signing an image and associating its SBOM/test evidence.
    - Prevent promotion and report the exact failed check whenever any required gate fails.
    - _Requirements: 14.7, 14.8_

  - [~] 13.4 Write end-to-end tests for all local roles and core journeys
    - Drive public, contributor, management, report, Moderator, and Administrator flows against deterministic local services, including accessible errors and non-JavaScript core behavior.
    - _Requirements: 11.1–11.7, 13.2, 13.3, 14.1, 14.2, 14.3, 14.6_

  - [~] 13.5 Write release-gate failure and no-AWS verification tests
    - Inject each required check failure and assert no releasable artifact; remove AWS credentials and assert every non-AWS suite completes.
    - _Requirements: 14.3, 14.7, 14.8_

- [ ] 14. Wire the application into reproducible, isolated AWS environments
  - [x] 14.1 Implement CDK network, data, storage, encryption, and access constructs
    - Define per-environment VPC/subnets, private RDS PostgreSQL, private versioned S3, owner-controlled KMS keys, Secrets Manager/SSM values, least-privilege task roles, security groups/endpoints, backups, and policy assertions.
    - Reject public RDS/S3 settings, wildcard data-plane access, missing encryption, shared production identifiers, and any unauthenticated path to Evidence or Personal_Data before resource creation.
    - _Requirements: 10.1, 10.2, 15.2, 15.7, 15.9_

  - [x] 14.2 Implement shared-image AWS runtime and production adapters
    - Run the same web/worker/scanner image on private multi-AZ ECS Fargate tasks and connect RDS, S3/KMS, SES, signed backup events, CloudWatch/X-Ray, and SNS through the existing ports.
    - Keep validation, comparison, normalization, moderation, privacy, and safe-error rules in shared domain/application code.
    - _Requirements: 15.2, 15.3, 15.4, 16.1, 16.3, 16.4_

  - [~] 14.3 Implement one-command deployment, health validation, outputs, and rollback
    - Build, scan, sign, and push one digest; run compatible migrations; deploy blue/green; execute pre-traffic readiness/detail/database checks; and automatically restore the last healthy revision on failure.
    - Output endpoint, deployment version, and health endpoint from `uv run poe deploy --environment <name>`.
    - _Requirements: 15.1, 15.5, 15.6_

  - [~] 14.4 Implement environment isolation, decommission retention, and recovery automation
    - Give development, staging, and production distinct data, keys, secrets, policies, endpoints, logs, and backup vaults.
    - Retain required production backups/KMS material during explicit decommission and automate isolated restore, integrity checks, credential rotation, promotion, and RPO/RTO marker measurement.
    - _Requirements: 15.7, 15.8, 16.7, 16.9_

  - [~] 14.5 Write the property test for local/AWS domain-rule parity
    - **Property 34: Domain rules are environment invariant**
    - Normalize adapter-specific outputs and compare validation, comparison, normalization, moderation-state, and privacy decisions over generated inputs.
    - **Validates: Requirements 15.3**

  - [~] 14.6 Write the property test for private-storage network policy
    - **Property 35: Infrastructure policy rejects every public path to private storage**
    - Generate synthesized graph mutations across routes, policies, access points, security groups, and endpoints and require pre-creation rejection.
    - **Validates: Requirements 15.9**

  - [~] 14.7 Write infrastructure, deployment, rollback, and recovery tests
    - Add CDK snapshots/assertions for TLS, KMS, topology, least privilege, backups, alarms, isolation, outputs, and removal policies; tag real-AWS deployment/rollback/alert/decommission/restore tests separately.
    - _Requirements: 10.1, 10.2, 15.1–15.9, 16.1, 16.3, 16.4, 16.7, 16.9_

- [~] 15. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional test tasks and may be skipped for a faster MVP; core implementation tasks are never optional.
- Each correctness property has one dedicated Hypothesis test task and should use its own test module, at least 100 successful examples, and the exact design tag `Feature: token-budget-platform, Property <number>: <summary>`.
- Unit/integration/end-to-end tests complement property tests with concrete boundaries, adapters, browser behavior, infrastructure assertions, and failure injection.
- Requirement references use granular acceptance-criteria numbers for implementation traceability.
- The workflow creates planning artifacts only; it does not implement these tasks.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2"] },
    { "id": 2, "tasks": ["1.3", "10.1", "14.1"] },
    { "id": 3, "tasks": ["1.4", "2.1", "7.1", "11.1", "14.2"] },
    { "id": 4, "tasks": ["2.2", "5.1", "6.1", "9.1", "11.2", "11.3"] },
    { "id": 5, "tasks": ["2.3", "5.2", "6.2", "7.2", "9.3", "11.4", "11.5"] },
    { "id": 6, "tasks": ["3.1", "5.3", "7.3", "7.4", "9.2", "9.4", "11.6"] },
    { "id": 7, "tasks": ["3.2", "7.5", "9.5", "11.7", "14.3"] },
    { "id": 8, "tasks": ["3.3", "14.4"] },
    { "id": 9, "tasks": ["3.4", "10.2"] },
    { "id": 10, "tasks": ["1.5", "2.4", "2.5", "2.6", "2.7", "2.8", "2.9", "2.10", "3.5", "3.6", "3.7", "3.8", "3.9", "3.10", "3.11", "3.12", "5.4", "5.5", "5.6", "6.3", "6.4", "7.6", "7.7", "7.8", "7.9", "7.10", "7.11", "7.12", "9.6", "9.7", "9.8", "11.8", "11.9", "11.10", "11.11", "14.5", "14.6"] },
    { "id": 11, "tasks": ["1.6", "2.11", "3.13", "5.7", "6.5", "7.13", "9.9", "10.3", "11.12", "14.7"] },
    { "id": 12, "tasks": ["10.4"] },
    { "id": 13, "tasks": ["13.1"] },
    { "id": 14, "tasks": ["13.2"] },
    { "id": 15, "tasks": ["13.3"] },
    { "id": 16, "tasks": ["13.4", "13.5"] }
  ]
}
```
