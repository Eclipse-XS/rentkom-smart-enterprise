# RENTKOM SMART ENTERPRISE — INTEGRATION & WORKFLOW ACCEPTANCE

**Audit Date:** 2026-11-04
**Branch:** `fix/rentkom-complete-remediation`
**Target Upstream:** `main` (`origin/main` HEAD `b607df6`)
**Verification Status:** `VERIFIED_WITH_DOCUMENTED_BOUNDARIES`

---

## 1. Real SMTP Notification Delivery (`core/services/outbox_service.py`)

The previous hardcoded fake success in `outbox_service.py` was replaced by `SMTPTransportAdapter` using Python's `smtplib.SMTP` and `email.mime.text.MIMEText`.

### Transport & Outbox Behavior
- **Missing `SMTP_HOST` when `NOTIFICATION_PROVIDER="smtp"`**: Fails closed (`is_success=False`, code `503`), increments `retry_count`, and schedules exponential backoff (`RETRY_PENDING`).
- **Real TCP Delivery**: Connects to `SMTP_HOST:SMTP_PORT` with configurable `SMTP_TIMEOUT_SECONDS`, optional `STARTTLS` (`SMTP_USE_TLS`), and optional `login(SMTP_USER, SMTP_PASSWORD)`, then dispatches UTF-8 MIME email via `sendmail`. Transitions outbox status to `SENT` / `PROVIDER_ACCEPTED` (code `250`) only upon SMTP server acknowledgment.
- **Structured Failure Classification**:
  - `SMTPAuthenticationError` $\to$ code `535` (logged without exposing `SMTP_PASSWORD` in logs or database `last_error`).
  - `SMTPRecipientsRefused` $\to$ code `550` / `451` (extracted from recipient tuple).
  - `socket.timeout` / `TimeoutError` / timed-out `SMTPServerDisconnected` $\to$ code `504`.
  - Unreachable host / connection refused $\to$ code `502`.
- **Retry & Dead-Letter Lifecycle**:
  - Retries up to `max_retries` (default `5`) with exponential backoff $10\text{s} \times 2^{\text{retry\_count}-1}$.
  - Transitions to `FAILED` (`delivery_state="FAILED_PERMANENT"`) when `retry_count >= max_retries`.
  - Reclaims crashed workers stuck in `PROCESSING` past `stale_timeout_seconds` using `SELECT ... FOR UPDATE SKIP LOCKED` and atomic CAS update.

### Verification Tests (`tests/integration/test_smtp_delivery.py` — 6/6 PASSED)
1. `test_smtp_missing_configuration_fails_closed` — `PASSED`
2. `test_smtp_unreachable_host_connection_refused_retries` — `PASSED`
3. `test_smtp_timeout_records_error_and_schedules_retry` — `PASSED`
4. `test_smtp_authentication_error_535_does_not_leak_password` — `PASSED`
5. `test_smtp_recipient_refused_550_and_temporary_451_failure` — `PASSED`
6. `test_real_smtp_delivery_and_idempotent_reprocessing` — `PASSED`

---

## 2. Telegram Connected Mode & PostgreSQL Idempotency (`core/api/v1/endpoints/telegram.py`, `core/services/telegram_idempotency.py`)

1. **Authoritative PostgreSQL Inbox (`public.telegram_inbox`)**:
   - Uses atomic `INSERT ... ON CONFLICT (bot_id, update_id) DO UPDATE ... WHERE status = 'FAILED' OR (status = 'PROCESSING' AND locked_at < NOW() - INTERVAL '30 seconds') RETURNING id, status`.
   - Lease expiration checks use the PostgreSQL server clock (`locked_at > NOW() - INTERVAL '30 seconds'`) to eliminate timezone/clock skew between Python workers and PostgreSQL.
   - Persists completed responses in `response_payload` (`JSONB`) and survives server restarts (`test_telegram_inbox_persists_across_restart` — `PASSED`).
   - Reclaims abandoned leases after 30 seconds (`test_telegram_inbox_lease_recovery_after_worker_crash` — `PASSED`).
2. **Connected vs. Demo Mode Boundaries**:
   - `/api/v1/telegram/webhook-mock` and `/api/v1/telegram/mock-dispatcher` return `HTTP 403 Forbidden` when `ENVIRONMENT` is `connected` or `production`.
   - `/api/v1/telegram/send-message` rejects `is_test=True` (`HTTP 403`), requires `X-Internal-Token` (`401`/`403`), requires `TELEGRAM_BOT_TOKEN` (`422`), and maps upstream Telegram API HTTP `429`, `5xx`, and network timeouts to `HTTP 502 Bad Gateway` with diagnostic details (`test_real_telegram_send_handles_429_5xx_and_timeout` — `PASSED`).

---

## 3. n8n Workflow Inventory & Contract Verification (`SMART_ENTERPRISE/automation/n8n/`)

All 8 workflow exports were inspected, remediated, and validated by `tests/unit/test_n8n_workflows_contract.py` (4/4 PASSED):

| Workflow File | Active | Role | Remediation & Contract Status |
|---|---|---|---|
| `unified_telegram_router.json` | **`true`** | Authoritative Telegram ingress (Webhook + Test Webhook), Inbox Guard, Menu/RAG routing, Connected (`/send-message`) vs. Test (`/mock-dispatcher`) dispatch, Inbox Complete | Replaced all 7 `127.0.0.1:8000` URLs with `$env.BUSINESS_API_URL \|\| 'http://business-api:8000'`; added `If Test Mode?` branch routing live traffic to `/api/v1/telegram/send-message`; configured offline webhook trigger (`VERIFIED_FIXED`) |
| `feedback_to_telegram.json` | `true` | Customer feedback ingestion (`POST /api/v1/feedbacks/`) & low-rating Telegram alert | Replaced `127.0.0.1:8000` with `$env.BUSINESS_API_URL`; fixed rating fallback bug (`Number(rawService) \|\| 5` $\to$ `null` when missing so invalid payloads are rejected with `422` instead of silently recorded as 5-star); replaced hardcoded `chatId: "684809016"` with `$env.TELEGRAM_ADMIN_CHAT_ID` (`VERIFIED_FIXED`) |
| `google_forms_to_postgres.json` | `true` | Consultation lead intake (`POST /api/v1/requests/`) | Uses `$env.BUSINESS_API_URL`; valid JSON (`VERIFIED_FIXED`) |
| `outbox_processor_workflow.json` | `true` | Scheduled worker trigger (`POST /api/v1/notifications/process`) | Uses `$env.BUSINESS_API_URL` and `$env.INTERNAL_API_KEY`; valid JSON (`VERIFIED_FIXED`) |
| `ai_agent_rag.json` | `false` | Legacy/standalone RAG workflow | Deactivated (`active: false`) to prevent Telegram polling conflict; replaced `127.0.0.1:5680` with `$env.BUSINESS_API_URL` (`VERIFIED_FIXED`) |
| `telegram_bot.json` | `false` | Legacy LR2 menu bot | Deactivated (`active: false`) to prevent Telegram polling conflict (`VERIFIED_FIXED`) |
| `data_sync_pipeline.json` | `false` | Auxiliary data sync workflow | Valid JSON, zero localhost URLs (`VERIFIED_FIXED`) |
| `notification_dispatcher.json` | `false` | Auxiliary notification workflow | Valid JSON, zero localhost URLs (`VERIFIED_FIXED`) |

---

## 4. Live n8n Container Verification (`tests/integration/test_n8n_unified_router_live.py`)

Executed against the live Docker Desktop n8n container (`http://localhost:5678`):
- `test_live_n8n_webhook_ping` — `PASSED`
- `test_live_n8n_rental_request_routing` — `PASSED` (synthetic rental lead ingested via n8n webhook and persisted to PostgreSQL)
- `test_live_n8n_feedback_routing` — `PASSED` (synthetic feedback routed to FastAPI)
- `test_live_n8n_outbox_cron_trigger` — `PASSED` (outbox dispatch triggered via n8n cron node)

---

## 5. Testing Boundaries vs. Production Live Relay Disclaimer

We explicitly document the verification boundaries:
- **SMTP Protocol Verification:** Tests in `test_smtp_delivery.py` verified real socket-level TCP transmissions (MIME assembly, STARTTLS negotiation, authentication handshakes, recipient error codes) against an in-process TCP SMTP server. They were **not** transmitted across public internet relays to avoid unbudgeted third-party email traffic.
- **Telegram Dispatch Verification:** Tests in `test_telegram_connected_mode_contract.py` verified outbound payloads, header authentication, and upstream error mapping (`429`, `503`, timeouts) against simulated HTTP endpoints. Live delivery via `https://api.telegram.org` requires a real user-supplied `TELEGRAM_BOT_TOKEN` in the production vault.
