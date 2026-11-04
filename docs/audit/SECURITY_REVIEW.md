# RENTKOM SMART ENTERPRISE — SECURITY & DATA SAFETY REVIEW

**Audit Date:** 2026-11-03
**Branch:** `remediation/engineering-acceptance`
**Verification Status:** `VERIFIED_FIXED`

---

## 1. Endpoint Authorization Matrix

All FastAPI routes registered under `core/api/v1/` were inspected and verified against the required access policy.

| Endpoint | HTTP Method | Access Tier | Enforced Dependency / Guard | Missing Creds | Invalid Creds | Unconfigured Server Key |
|---|---|---|---|---|---|---|
| `/api/v1/requests/` | `POST` | Public Customer Ingress | Pydantic schema validation + E.164 normalization | `201 Created` | N/A | N/A |
| `/api/v1/requests/` | `GET` | Administrative | `Depends(require_admin_auth)` (`X-Admin-Token` / `Bearer`) | `401` | `403` | `500` |
| `/api/v1/requests/{id}` | `GET` | Administrative | `Depends(require_admin_auth)` (`X-Admin-Token` / `Bearer`) | `401` | `403` | `500` |
| `/api/v1/requests/{id}/status` | `PATCH` | Administrative | `Depends(require_admin_auth)` + actor audit override | `401` | `403` | `500` |
| `/api/v1/feedbacks/` | `POST` | Public Customer Ingress | Pydantic rating validation (`1..5`) | `201 Created` | N/A | N/A |
| `/api/v1/feedbacks/` | `GET` | Administrative | `Depends(require_admin_auth)` (`X-Admin-Token` / `Bearer`) | `401` | `403` | `500` |
| `/api/v1/notifications/outbox` | `GET` | Administrative | `Depends(require_admin_auth)` (`X-Admin-Token` / `Bearer`) | `401` | `403` | `500` |
| `/api/v1/notifications/process` | `POST` | Internal Worker / Admin | `Depends(require_internal_or_admin_auth)` | `401` | `403` | `500` |
| `/api/v1/telegram/webhook-mock` | `POST` | Demo / Test Mode Only | Rejects with `403` when `not settings.is_safe_demo_or_dev` | `200` (Demo) / `403` (Connected) | `403` | N/A |
| `/api/v1/telegram/webhook` | `POST` | Connected Webhook Ingress | Verifies `X-Telegram-Bot-Api-Secret-Token` or `X-Internal-Token` | `401` | `403` | `500` |
| `/api/v1/telegram/inbox/guard` | `POST` | Internal / Test Gatekeeper | `X-Internal-Token` (`is_test=false`) or `X-Test-Token` (`is_test=true` in demo) | `401` | `403` | `500` |
| `/api/v1/telegram/inbox/complete` | `POST` | Internal / Test Gatekeeper | `X-Internal-Token` (or `X-Test-Token` in demo) | `401` | `403` | `500` |
| `/api/v1/telegram/mock-dispatcher` | `POST` | Demo / Test Mode Only | Disabled in connected/prod (`403`); requires `X-Test-Token` / `X-Internal-Token` | `401` | `403` | N/A |
| `/api/v1/telegram/send-message` | `POST` | Internal Connected Service | Rejects `is_test=true` (`403`); requires `X-Internal-Token` | `401` | `403` | `500` |
| `/api/v1/rag/ask` | `POST` | Public (Bounded & Guarded) | Prompt injection + out-of-domain filters + `<retrieved_evidence>` boundary | `200` | N/A | N/A |
| `/api/v1/health`, `/live`, `/ready` | `GET` | Public Operational Probes | Zero secret exposure; `/ready` returns `503` if DB unreachable | `200` / `503` | N/A | N/A |

---

## 2. Cryptographic & Runtime Security Controls (`core/security.py`, `core/config.py`)

1. **Timing-Safe Token Comparison**: All secret comparisons (`ADMIN_API_KEY`, `INTERNAL_API_KEY`, `TEST_WEBHOOK_SECRET`, `TELEGRAM_SECRET_TOKEN`) use `secrets.compare_digest` via `constant_time_equals()`.
2. **Fail-Closed When Unconfigured**: `get_effective_admin_key()`, `get_effective_internal_key()`, and `get_effective_test_secret()` return `None` when the corresponding setting is empty or unset. Missing server keys return `HTTP 500 Internal Server Error` and never bypass authentication.
3. **Production Startup Validation (`settings.validate_runtime_security()`)**:
   - Invoked during FastAPI lifespan startup (`core/main.py`).
   - When `ENVIRONMENT=production`, startup aborts with `RuntimeError` if:
     - `ADMIN_API_KEY` or `INTERNAL_API_KEY` is shorter than 24 characters or matches a known demo key.
     - `ALLOW_TEST_WEBHOOK` is `True`.
     - `NOTIFICATION_PROVIDER` is `"mock"`.
     - `DATABASE_URL` contains default `postgres:postgres@` credentials.
4. **Actor Attribution on State Changes**: `PATCH /api/v1/requests/{id}/status` extracts the authenticated `AuthenticatedActor.actor_id` from `require_admin_auth` and writes it to `request_status_history.changed_by` and `audit_events.details_json`, preventing client-supplied actor spoofing.
5. **Container & CORS Hardening**:
   - `Dockerfile` creates and switches to non-root user `appuser`.
   - `core/main.py` binds `CORSMiddleware` `allow_origins` to `settings.CORS_ORIGINS` instead of `["*"]`.

---

## 3. Repository Secret & PII Remediation

1. **`SMART_ENTERPRISE/monitoring/provisioning/datasources.yml`**: Replaced plaintext PostgreSQL password with `${POSTGRES_PASSWORD:-postgres}` and `${POSTGRES_USER:-postgres}`.
2. **`SMART_ENTERPRISE/automation/n8n/feedback_to_telegram.json`**: Replaced hardcoded personal Telegram `chatId: "684809016"` with `={{ $env.TELEGRAM_ADMIN_CHAT_ID || '123456789' }}`.
3. **`tests/integration/test_legacy_lr4_postgres_v2.py`**: Replaced personal email address with synthetic `legacy_test_client@rentkom.lviv.ua`.
4. **`.env.example`**: Sanitized template provided with all required environment variables documented and zero real secrets.

---

## 4. Database Test Safety (`tests/safety.py`)

To prevent accidental data loss in developer or production PostgreSQL instances during test execution:
1. **Explicit Environment Variable**: All 8 PostgreSQL integration/E2E test modules read `os.getenv("TEST_DATABASE_URL")` and safely `pytest.skip(...)` when unset. Hardcoded fallback URLs (`127.0.0.1:5433/postgres`) have been completely removed.
2. **Forbidden Database Blocklist (`assert_safe_test_database`)**: Strictly rejects `postgres`, `template0`, `template1`, `lab4_real_estate`, `rentkom`, `rentkom_prod`, `rentkom_production`, and any database name that does not contain `test`, `disposable`, or `temp`.
3. **Ownership Safety Marker (`ensure_test_schema_marker`)**: Before any fixture drops or truncates tables, it checks whether application tables exist without the `_rentkom_test_safety_marker` table. If unmarked tables exist, execution aborts immediately with `RuntimeError("DATA SAFETY ABORT...")`.

---

## 5. Verification Evidence

Executed in `tests/unit/test_security_and_safety.py` and `tests/integration/test_telegram_connected_mode_contract.py`:
- `test_safety_guard_rejects_application_and_default_databases` — `PASSED`
- `test_safety_guard_rejects_unmarked_database` — `PASSED`
- `test_safety_guard_accepts_test_database` — `PASSED`
- `test_unauthorized_endpoints_rejected_401_missing_credentials` — `PASSED`
- `test_invalid_credentials_rejected_403` — `PASSED`
- `test_authorized_endpoints_succeed_with_valid_credentials` — `PASSED`
- `test_missing_configured_server_key_returns_500_no_bypass` — `PASSED`
- `test_environment_modes_demo_connected_production` — `PASSED`
- `test_public_customer_ingestion_remains_open` — `PASSED`
- `test_zero_plaintext_secrets_in_provisioning` — `PASSED`
