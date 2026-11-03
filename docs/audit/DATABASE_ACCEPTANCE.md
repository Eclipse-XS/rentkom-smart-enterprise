# RENTKOM SMART ENTERPRISE — DATABASE & BUSINESS CORE ACCEPTANCE

**Audit Date:** 2026-11-03
**Branch:** `remediation/engineering-acceptance`
**PostgreSQL Engine Verified:** PostgreSQL 18 (`initdb` / `pg_ctl` / `psql` / `pg_dump` on isolated port `5439`, database `rentkom_test`)
**Verification Status:** `VERIFIED_FIXED`

---

## 1. Schema Bootstrap & Idempotent Migration Verification

Both SQL scripts in `SMART_ENTERPRISE/database/` were synchronized and verified directly against an isolated PostgreSQL 18 cluster with `-v ON_ERROR_STOP=1`:

1. **Fresh Bootstrap (`SMART_ENTERPRISE/database/init_db.sql`)**:
   - Creates all 8 v2 tables (`customers`, `service_requests`, `request_status_history`, `customer_feedbacks`, `notification_outbox`, `notification_attempts`, `audit_events`, `telegram_inbox`).
   - Enforces `CHECK` constraints on `service_requests.status` (`NEW`, `IN_REVIEW`, `SCHEDULED`, `COMPLETED`, `CANCELLED`), `customer_feedbacks` ratings (`1..5`), and `notification_outbox.status` (`PENDING`, `PROCESSING`, `RETRY_PENDING`, `SENT`, `MOCK_DELIVERED`, `PROVIDER_ACCEPTED`, `FAILED`, `FAILED_PERMANENT`).
   - Creates analytical views (`v_requests_analytics`, `v_feedback_analytics`, `v_outbox_telemetry`) and the backward-compatible legacy view `real_estate_requests` with its `INSTEAD OF INSERT` trigger `trg_instead_of_insert_real_estate_requests` (`insert_real_estate_request_legacy()`).
2. **Legacy-to-v2 Migration (`SMART_ENTERPRISE/database/schema_v2.sql`)**:
   - Fixed the customer deduplication query from `GROUP BY full_name, phone` (which failed with `UniqueViolation` on `customers_phone_key` when the same phone number appeared with different names) to `SELECT DISTINCT ON (phone) ... ORDER BY phone, created_at ASC`.
   - Migrates all legacy rows from `real_estate_requests` / `real_estate_requests_legacy_lr4` into `customers` and `service_requests` with `ON CONFLICT (source_row_id) DO NOTHING`.
   - Re-executed twice consecutively against PostgreSQL 18 with `-v ON_ERROR_STOP=1` with **zero errors** and **zero duplicate records**.

---

## 2. Business Core Concurrency & Transactional Atomicity (`core/services/request_service.py`)

1. **E.164 Phone Normalization (`normalize_phone`)**:
   - Normalizes `0689998877`, `380689998877`, `80689998877`, and `+38 (068) 999-88-77` into canonical `+380689998877`.
   - Rejects invalid lengths (`< 9` or `> 15` digits) with `ValueError` (`HTTP 400`).
2. **Concurrency-Safe Customer Deduplication**:
   - Uses `with db.begin_nested():` (`SAVEPOINT`) around `Customer` insertion and catches `IntegrityError` to re-query the winning concurrent row.
   - Verified under 2-thread and 10-thread simultaneous submissions (`test_concurrent_customer_deduplication[2]` and `[10]`): all 10 concurrent requests succeed (`0` errors) and resolve to **exactly 1 `Customer` row**.
3. **Idempotent `source_row_id` Replay & Conflict Detection**:
   - Identical replay of an existing `source_row_id` returns the existing `ServiceRequest` without creating duplicate requests or duplicate outbox rows.
   - Conflicting replay of an existing `source_row_id` with a mutated phone, `request_purpose`, or `property_type` raises `ValueError("Idempotency conflict...")`, mapped to `HTTP 409 Conflict` in `core/api/v1/endpoints/requests.py`.
4. **Full Transaction Rollback on Failure**:
   - `create_service_request` wraps final persistence in `try ... except Exception: db.rollback(); raise`, ensuring that any pre-commit failure leaves zero orphaned `Customer`, `ServiceRequest`, or `NotificationOutbox` rows (`test_transaction_rollback_on_failure_leaves_zero_partial_records`).
5. **Formal Request Lifecycle State Machine**:
   - Enforces `VALID_STATUS_TRANSITIONS`:
     - `NEW` $\to$ `{"IN_REVIEW", "CANCELLED"}`
     - `IN_REVIEW` $\to$ `{"SCHEDULED", "CANCELLED"}`
     - `SCHEDULED` $\to$ `{"COMPLETED", "CANCELLED"}`
     - `COMPLETED` $\to$ $\emptyset$ (terminal)
     - `CANCELLED` $\to$ $\emptyset$ (terminal)
   - Illegal transitions raise `ValueError` (`HTTP 400`) without mutating database state.

---

## 3. PostgreSQL Backup & Restore Verification (`tools/backup_restore.py`)

- Implemented `create_postgres_backup()` (executing `pg_dump --no-owner --no-privileges --clean --if-exists`) and `verify_postgres_restore()` (executing `psql -v ON_ERROR_STOP=1` into a disposable target database guarded by `assert_safe_test_database()`).
- Wired into `python tools/manage.py backup` and `python tools/manage.py restore`.
- Verified in `tests/integration/test_backup_restore.py`:
  - `test_restore_rejects_non_test_database_url` — `PASSED` (refuses `lab4_real_estate`, `rentkom`, `postgres`).
  - `test_postgres_backup_and_restore_into_disposable_database` — `PASSED` (creates live PostgreSQL 18 backup of `rentkom_test`, creates disposable `rentkom_restore_test`, restores via `psql`, and verifies row counts across `customers`, `service_requests`, `customer_feedbacks`, and `notification_outbox`).
