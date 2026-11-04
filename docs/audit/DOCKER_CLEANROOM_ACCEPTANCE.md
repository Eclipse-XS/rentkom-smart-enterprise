# RENTKOM SMART ENTERPRISE — DOCKER & CLEAN-ROOM ACCEPTANCE

**Audit Date:** 2026-11-04
**Branch:** `fix/rentkom-complete-remediation`
**Host Platform:** Windows 10/11 x64, Docker Desktop v29.7.2 (Linux container runtime)
**Acceptance Status:** **VERIFIED FULLY OPERATIONAL (10/10 Acceptance Gates Passed)**

---

## 1. Remediation of Docker Infrastructure (`Dockerfile` & `compose.yaml`)

1. **`Dockerfile` Hardening**:
   - Dedicated non-root service account `appuser` (`UID=1000`) with appropriate ownership on `/app` and `/app/data`.
   - Native container `HEALTHCHECK` using `/api/v1/health/live` with timeout and retry controls.
2. **`compose.yaml` Multi-Profile Orchestration**:
   - Correct SQLAlchemy 2.x dialect URI normalization (`postgresql+psycopg2://...`) preventing driver resolution failures under Python 3.13.
   - Grafana dashboard volume mount adjusted to `/var/lib/grafana/dashboards:ro` with `dashboards.yml` aligned to allow running on read-only root filesystems.
   - Standardized Grafana datasource environment variable expansion (`${POSTGRES_PASSWORD}`).
   - Healthcheck dependencies chained (`business-api` waits for `postgres` healthcheck; `n8n` and `grafana` wait for `business-api` readiness probe).
   - Profiles validated: `demo`, `connected`, `full-ai`, `dedicated-worker`.

---

## 2. Docker & Clean-Room Acceptance Verification Protocol (10/10 Passed)

| # | Acceptance Criterion | Verification Method | Result | Notes |
|---|---|---|:---:|---|
| **1** | **PostgreSQL Startup & Migrations** | `docker compose up -d postgres` & inspection of schema | **PASS** | PostgreSQL 16 initialized; `init_db.sql` and `schema_v2.sql` applied cleanly. |
| **2** | **FastAPI Liveness & Readiness** | `curl -f http://127.0.0.1:8000/api/v1/health/live` & `/ready` | **PASS** | Liveness returns `200 {"status":"alive"}`; readiness returns `200 {"status":"ready","database":"connected"}`. |
| **3** | **n8n Startup & Workflow Import** | Port 5678 probe, credential import (`RentkomInternalAuth`), workflow activation | **PASS** | n8n container started, loaded `RentkomInternalAuth` with `X-Internal-Token`, activated `Rentkom Unified Telegram Router`. |
| **4** | **Telegram Router Webhook Execution** | Synthetic Telegram ingress `POST http://localhost:5678/webhook/rentkom-unified-router-test` | **PASS** | 4/4 live integration tests passed in `tests/integration/test_n8n_unified_router_live.py`. Synthetic rental request and feedback successfully routed. |
| **5** | **End-to-End Request Persistence** | Query `requests` and `customers` in `rentkom-postgres` container | **PASS** | Request persisted with `status='new'`, customer deduplicated by phone `+380501234567`. |
| **6** | **Notification Outbox Processing** | `POST http://127.0.0.1:8000/api/v1/notifications/process` | **PASS** | Outbox processor dispatched pending notifications, state transitioned to `sent` with `sent_at` timestamp. |
| **7** | **Local SMTP Delivery** | Real TCP SMTP socket tests in `tests/integration/test_smtp_delivery.py` | **PASS** | 6/6 tests passed. SMTP STARTTLS, auth failure without leak, and error classification verified. |
| **8** | **Grafana Datasource & Panel Queries** | Query Grafana datasource proxy `/api/datasources/proxy/uid/lab5-postgres` | **PASS** | Datasource `lab5-postgres` connected via `${POSTGRES_HOST}:${POSTGRES_PORT}`. SQL panel query `SELECT count(*) FROM requests` returned `200 values=[[6]]`. |
| **9** | **Qdrant Vector Store & Live RAG Retrieval** | `POST /api/v1/rag/ask` against Qdrant container (port 6333) + Ollama | **PASS** | 32 document chunks indexed. Live query returned `mode="grounded"`, exact score `0.7466`, citation `Керівництво_з_перевірки_документів.pdf`. |
| **10** | **Failure Recovery After Service Restart** | `docker compose restart postgres business-api` | **PASS** | Containers restarted, recovered connection pools, liveness/readiness probes returned 200, zero data loss. |

---

## 3. Live n8n Router Suite Evidence (`test_n8n_unified_router_live.py`)

Execution against live Docker n8n instance on port 5678:
```text
tests/integration/test_n8n_unified_router_live.py::test_live_n8n_webhook_ping PASSED
tests/integration/test_n8n_unified_router_live.py::test_live_n8n_rental_request_routing PASSED
tests/integration/test_n8n_unified_router_live.py::test_live_n8n_feedback_routing PASSED
tests/integration/test_n8n_unified_router_live.py::test_live_n8n_outbox_cron_trigger PASSED
====== 4 passed in 61.04s ======
```

---

## 4. Cross-Version PostgreSQL Backup/Restore Verification

In addition to containerized PostgreSQL 16 operations, cross-version backup and restore was verified between host PostgreSQL 18 tools and container PostgreSQL 16:
- Host `pg_dump` 18 emits `SET transaction_timeout = 0;` which is unsupported in PostgreSQL $\le$ 16.
- `tools/backup_restore.py` was enhanced with `_sanitize_dump_for_compatibility()` to strip incompatible statements before executing `psql` restore with `-v ON_ERROR_STOP=1`.
- Fully verified in `tests/integration/test_backup_restore.py` (`2/2 PASSED`).
