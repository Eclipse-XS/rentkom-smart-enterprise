# PostgreSQL schema

This directory contains the database bootstrap, legacy migration, and verification SQL used by Rentkom's FastAPI business core.

## Files

| File | Purpose |
| --- | --- |
| `init_db.sql` | Creates the current relational schema, indexes, and compatibility/analytics views |
| `schema_v2.sql` | Migration and compatibility logic for earlier real-estate request data |
| `schema.sql` | Historical schema definition retained for reference |
| `verify.sql` | SQL checks for the relational data |

## Current bootstrap tables

`init_db.sql` creates the following nine tables:

1. `customers`
2. `service_requests`
3. `request_status_history`
4. `customer_feedbacks`
5. `notification_outbox`
6. `notification_attempts`
7. `audit_events`
8. `telegram_inbox`
9. `inbound_lead_submissions`

The bootstrap also defines `real_estate_requests` as a compatibility view for earlier consumers. Do not describe it as the primary current table. The core application uses normalized records and tracks notification state separately.

## Runtime configuration

The Docker Compose demo uses `postgres:16-alpine` and the `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` configuration values. The application connects with a separate `rentkom_app` role initialized by `scripts/init-db.sh`. Local development may use a different PostgreSQL version, but the container image is the deployment reference.

Use isolated databases for destructive integration tests. The test safety checks reject known application database names and require an explicit disposable test database. See [deployment instructions](../../DEPLOYMENT.md) and [tests/safety.py](../../tests/safety.py).
