# RENTKOM SMART ENTERPRISE — RECOVERY BASELINE

**Date:** 2026-11-03
**Auditor:** Lead Principal Engineer (Antigravity)
**Repository:** `https://github.com/Eclipse-XS/rentkom-smart-enterprise`
**Worktree Path:** `c:\Users\Eclipse\Desktop\7 семестр\Інформаційні технології розумного підприємства`

---

## 1. Git Repository State

- **Current Branch:** `remediation/engineering-acceptance` (identical tip to `feature/portfolio-overhaul`)
- **HEAD SHA:** `ec679113e0b09f62a5e5ba67443220b4711bb981`
- **Remote Origin:** `https://github.com/Eclipse-XS/rentkom-smart-enterprise.git` (`origin/main` at `b607df6`)
- **History Divergence Analysis:**
  - `git merge-base HEAD origin/main` exited with code `1` (no common ancestor).
  - `git rev-list --left-right --count HEAD...origin/main` returned `12` (local) vs `7` (origin/main).
  - **Cause:** `origin/main` was initialized from a squashed clean snapshot commit (`6635339 feat: initial clean release of Rentkom Smart Enterprise`), whereas local branches preserve the historical multi-gate development lineage (`cd0a0de` .. `ec67911`).
  - **Policy Applied:** Per mandatory instructions, unrelated histories are NOT merged, rebased, or force-pushed. All work continues on the existing remediation branch.

---

## 2. Uncommitted Tracked Modifications (30 Files)

The interrupted agent modified 30 tracked files (`729 insertions(+), 276 deletions(-)`):

1. `.gitignore` — Added ignore rules for temporary artifacts.
2. `DEPLOYMENT.md` — Added security/environment deployment notes.
3. `Dockerfile` — Added unprivileged `appuser` / `appgroup` runtime user.
4. `README.md` — Partial documentation updates.
5. `SMART_ENTERPRISE/automation/n8n/feedback_to_telegram.json` — Partial URL update.
6. `SMART_ENTERPRISE/automation/n8n/forms_postgres_email.json` — Partial URL update.
7. `SMART_ENTERPRISE/automation/n8n/telegram_rule_bot.json` — Partial URL update.
8. `SMART_ENTERPRISE/database/init_db.sql` — Fixed `GROUP BY full_name, phone` in legacy migration and sanitized fallback email.
9. `SMART_ENTERPRISE/database/schema_v2.sql` — Fixed `GROUP BY full_name, phone` in legacy migration.
10. `SMART_ENTERPRISE/monitoring/dashboards/technical_observability.json` — Assigned PostgreSQL datasource to 2 panels that previously had `datasource: None`.
11. `SMART_ENTERPRISE/monitoring/provisioning/datasources.yml` — Removed hardcoded password, replaced with `${POSTGRES_PASSWORD:-postgres}` and `${POSTGRES_USER:-postgres}`.
12. `SMART_ENTERPRISE/scripts/verify_lr4_lr5_integrity.py` — Updated script check.
13. `compose.yaml` — Added `GF_INSTALL_PLUGINS: "marcusolsson-json-datasource"` and `BUSINESS_API_URL`.
14. `core/api/v1/endpoints/feedbacks.py` — Added `require_admin_auth` to `GET /api/v1/feedbacks/`.
15. `core/api/v1/endpoints/notifications.py` — Added `require_internal_or_admin_auth` to `POST /process` and `require_admin_auth` to `GET /outbox`.
16. `core/api/v1/endpoints/requests.py` — Added `require_admin_auth` to `GET /`, `GET /{id}`, and `PATCH /{id}/status`.
17. `core/api/v1/endpoints/telegram.py` — Replaced `hash()` with `hashlib.sha256` in mock dispatcher and wired `get_effective_internal_key` / `get_effective_test_secret`.
18. `core/config.py` — Added `SMTP_*`, `ADMIN_API_KEY`, `CORS_ORIGINS`, `TEST_DATABASE_URL`, and `validate_runtime_security()`.
19. `core/main.py` — Wired `settings.validate_runtime_security()` in lifespan and `settings.CORS_ORIGINS` in `CORSMiddleware`.
20. `core/services/outbox_service.py` — Implemented `SMTPTransportAdapter` using `smtplib` and wired into `process_outbox`.
21. `core/services/request_service.py` — Added `VALID_STATUS_TRANSITIONS`, phone length validation, and nested savepoints (`db.begin_nested()`) for customer creation and request creation.
22. `core/services/telegram_idempotency.py` — Replaced `hash()` with `hashlib.sha256` and added fail-closed error raising in non-demo mode.
23. `tests/conftest.py` — Added default test keys (`ADMIN_API_KEY`, `INTERNAL_API_KEY`, `TEST_WEBHOOK_SECRET`).
24. `tests/e2e/test_full_system_demo_e2e.py` — Partial updates.
25. `tests/integration/test_database_migration.py` — Updated migration test.
26. `tests/integration/test_real_postgresql_v2.py` — Added `assert_safe_test_database` guard and `TEST_DATABASE_URL` requirement.
27. `tests/integration/test_requests_api.py` — Added `X-Admin-Token` header to `GET` and `PATCH` test calls.
28. `tests/integration/test_telegram_connected_mode_contract.py` — Adjusted secret assertions.
29. `tests/unit/test_deduplication.py` — Updated test phone numbers.
30. `tools/seed_demo_data.py` — Minor update.

---

## 3. Untracked Remediation Files Created by Previous Agent

- `core/security.py` — FastAPI authentication dependencies (`require_admin_auth`, `require_internal_or_admin_auth`).
- `tests/safety.py` — Test database URL safety guard (`is_safe_test_database_url`, `assert_safe_test_database`).
- `tests/unit/test_security_and_safety.py` — Unit tests for endpoint auth, database safety guard, and datasource secrets.
- `tests/unit/test_business_core_concurrency_and_lifecycle.py` — Unit tests for phone normalization, state machine transitions, and SQLite concurrency.
- `tests/integration/test_smtp_delivery.py` — Local TCP SMTP server integration tests for `SMTPTransportAdapter`.
- `docs/audit/BASELINE_FINDINGS.md` — Initial forensic audit report.
- `docs/audit/REMEDIATION_MATRIX.md` — Initial remediation matrix (all rows still marked `READY_FOR_IMPL`).

*(Note: The working directory also contains historical academic coursework folders `LAB2..LAB6`, `LR1`, `.docx`/`.pdf` lab reports, and diagnostic scripts from earlier October sessions; these user files are preserved intact and excluded from Git commits.)*

---

## 4. Deficiencies and Incomplete Work Identified in Previous Agent's Changes

1. **Trailing Whitespace (`git diff --check`):** 11 trailing whitespace errors across `init_db.sql`, `notifications.py`, `requests.py`, `telegram.py`, `config.py`, `request_service.py`, `DEPLOYMENT.md`.
2. **Security Module (`core/security.py` & `core/api/v1/endpoints/telegram.py`):**
   - Token comparisons use `==` instead of constant-time `secrets.compare_digest()`.
   - `POST /api/v1/telegram/webhook-mock` is NOT guarded by `settings.is_safe_demo_or_dev` (still exposed in connected/production mode!).
   - `POST /api/v1/telegram/webhook` does NOT verify `X-Telegram-Bot-Api-Secret-Token` against `settings.TELEGRAM_SECRET_TOKEN` in connected/production mode.
   - `POST /api/v1/telegram/inbox/complete` has NO authentication check.
   - `core/config.py:validate_runtime_security()` does not reject insecure default database passwords (`postgres/postgres`) or Grafana `admin/admin` in production mode.
3. **Database Test Isolation (`tests/safety.py` & Integration Tests):**
   - `tests/integration/test_demo_seed_idempotency.py`, `test_legacy_lr4_postgres_v2.py`, `test_lr3_feedback_pipeline.py`, `test_lr4_notification_pipeline_forensics.py`, `test_outbox_automatic_lifecycle.py`, `test_telegram_inbox_postgres.py`, and `tests/e2e/test_full_system_demo_e2e.py` STILL hardcode `POSTGRES_URL = "postgresql+psycopg2://postgres@127.0.0.1:5433/postgres"` without calling `assert_safe_test_database()` or using a disposable database marker!
   - `tests/e2e/test_e2e_scenarios.py` was not updated for authenticated endpoints (`GET /notifications/outbox`, `POST /notifications/process`, `PATCH /requests/{id}/status`) nor for the valid state transition progression (`NEW -> IN_REVIEW -> SCHEDULED -> COMPLETED`).
4. **Schema Consistency (`schema_v2.sql` vs `init_db.sql`):**
   - `schema_v2.sql` line 97 still restricts `notification_outbox.status` to `('PENDING', 'PROCESSING', 'SENT', 'FAILED')`, omitting `'MOCK_DELIVERED'`, `'PROVIDER_ACCEPTED'`, and `'RETRY_PENDING'`.
   - `schema_v2.sql` lacks the `telegram_inbox` table definition present in `init_db.sql` and `core/models.py`.
   -Legacy customer migration `GROUP BY full_name, phone` in both SQL files fails on `UNIQUE (phone)` if the same phone number appeared with two different `full_name` strings in `real_estate_requests`. Must use `SELECT DISTINCT ON (phone) full_name, phone, created_at, created_at FROM public.real_estate_requests ORDER BY phone, created_at ASC`.
5. **RAG Service (`core/services/rag_service.py`):**
   - Was NOT modified at all by the interrupted agent!
   - Still calls Ollama with empty context when Qdrant returns 0 chunks or fails, returning `mode="grounded"`, `sources=["База знань «Рентком»"]`, and `confidence_score=0.85`.
   - System prompt still says `"Ти — професійний юрист-консультант..."`.
   - Prompt injection defense relies only on a 7-phrase substring list without structural delimiter boundaries around retrieved context.
6. **n8n Workflows (`SMART_ENTERPRISE/automation/n8n/*.json`):**
   - `unified_telegram_router.json` still has 7 hardcoded `http://127.0.0.1:8000` URLs!
   - `feedback_to_telegram.json` still defaults missing ratings to `5` (`Number(rawService || 5)`) and contains a personal Telegram chat ID (`795266863`).
   - `forms_postgres_email.json` still has hardcoded `kanaleclipse@gmail.com`.
7. **Health & Readiness (`core/api/v1/endpoints/health.py`):**
   - Only `/api/v1/health` exists (returning HTTP 200 even when database is `"unhealthy"`). Needs separate liveness (`/health/live`) and readiness (`/health/ready` returning HTTP 503 when DB is down).
8. **Backup & Restore (`tools/manage.py`):**
   - `cmd_backup` only copies `rentkom_demo.db` (SQLite) and has no `pg_dump` / `psql` PostgreSQL backup and restore implementation or verification.

---

## 5. Runtime Environment & Tooling Status

- **OS:** Windows 11 x64 (`10.0.26100`)
- **Python:** `3.13.5` in `.venv\Scripts\python.exe` (`pytest 9.1.1`)
- **PostgreSQL Binaries:** PostgreSQL `18.0` installed at `C:\Program Files\PostgreSQL\18\bin\` (`psql.exe`, `pg_dump.exe`, `pg_restore.exe`, `initdb.exe`, `pg_ctl.exe` all verified working).
  - Host service `postgresql-x64-18` is running on `127.0.0.1:5432` (password-protected user DB; untouched).
  - Ephemeral disposable PostgreSQL 18 clusters can be initialized and started on demand in `$TEMP` via `initdb.exe` + `pg_ctl.exe` (verified on port `5439`).
- **Docker:** Docker CLI `29.7.2` installed, but `com.docker.service` / `dockerDesktopLinuxEngine` is `Stopped` and cannot be started from a non-elevated shell. All live Docker runtime checks are `BLOCKED_BY_ENVIRONMENT`.
- **Ollama:** Running on `127.0.0.1:11434` with models `qwen3:4b-instruct`, `mxbai-embed-large:latest`, `qwen3:4b-lab6`, `qwen3:4b`.
- **Qdrant:** Native binary available at `LAB6\runtime\qdrant\qdrant.exe` (currently stopped on port `6333`; can be started or tested in-process/locally).
- **n8n & Grafana:** Currently stopped on ports `5678` and `3000`.
