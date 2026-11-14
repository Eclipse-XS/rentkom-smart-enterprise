# Rentkom deployment guide

This guide describes how to run the repository locally and what extra configuration is needed for connected services. The default setup is a **portfolio demo**, not a public production deployment.

## Requirements

- Docker Engine / Docker Desktop with Docker Compose v2
- Git and a shell with permission to run Docker
- Python 3.13 and project requirements only if using Python maintenance tools from the host

Run the commands below from the root of the cloned repository.

## Local demo: API, database, Grafana, Qdrant

```bash
cp .env.example .env
docker compose --profile demo up -d --build
docker compose exec business-api python tools/seed_demo_data.py
```

| Service | Local URL |
| --- | --- |
| FastAPI Swagger | http://127.0.0.1:8000/docs |
| API readiness | http://127.0.0.1:8000/api/v1/health/ready |
| Grafana | http://127.0.0.1:3000 |
| Qdrant | http://127.0.0.1:6333/dashboard |

To inspect running services:

```bash
docker compose --profile demo ps
docker compose --profile demo logs --tail=100 business-api
```

The `demo` profile uses mock notification behavior. It does not start n8n or Ollama; consequently, do not describe it as a live Telegram or fully initialized RAG demo. The demo credentials in `.env.example` are local defaults, not secure deployment settings.

## Compose profiles and environment modes

The four Compose profiles control which services start:

| Profile | Additional components | Usage |
| --- | --- | --- |
| `demo` | FastAPI, PostgreSQL, Grafana, Qdrant | Local API and monitoring walkthrough |
| `connected` | Adds n8n | Workflow orchestration with configured credentials |
| `full-ai` | Adds n8n and Ollama | Model serving for RAG, after separate model/index preparation |
| `dedicated-worker` | Separate outbox worker | Set `RUN_OUTBOX_WORKER_IN_LIFESPAN=false` when running this profile |

`ENVIRONMENT` is a **separate application setting**; a Compose profile alone does not set the security mode. To use connected behavior, set `ENVIRONMENT=connected` in `.env` and replace the example API keys with strong random values.

For Telegram, configure `TELEGRAM_BOT_TOKEN`, `TELEGRAM_SECRET_TOKEN`, `TELEGRAM_ADMIN_CHAT_ID`, and the appropriate n8n webhook configuration. For SMTP, configure the `SMTP_*` variables and select the intended `NOTIFICATION_PROVIDER`.

The n8n credential bootstrap script is [`scripts/bootstrap_n8n_credentials.py`](scripts/bootstrap_n8n_credentials.py). It expects `INTERNAL_API_KEY`, `TEST_WEBHOOK_SECRET`, and `TELEGRAM_SECRET_TOKEN`. It imports header credentials into the running n8n container and does not replace provider-side webhook setup. Configure credentials before expecting end-to-end messages to reach a real Telegram account.

Do not publish the `.env` file, expose default credentials, or bind local data services to untrusted interfaces.

## RAG preparation and limits

The knowledge base consists of six demonstration PDF/DOCX documents in `SMART_ENTERPRISE/ai/knowledge_base/source/`. The retrieval and generation components use Qdrant and Ollama. Model availability and an indexed collection are prerequisites for meaningful RAG responses.

A `full-ai` Compose startup alone does **not** prove that models are downloaded or that the Qdrant collection is indexed. The repository includes `scripts/ensure_ollama_models.py` and `tools/reindex_knowledge_base.py` for these steps; run them from an environment that can reach Ollama/Qdrant and read the source documents. The current API Dockerfile does not bundle the document source directory, so running the reindexer **inside the default API image is not supported without an additional mount or image change**.

Recorded model quality and latency measurements are in [`evaluation/rag_eval_results.json`](evaluation/rag_eval_results.json) and [`docs/audit/RAG_EVALUATION.md`](docs/audit/RAG_EVALUATION.md).

## Tests and health checks

```bash
python -m pytest tests/unit tests/security -v --tb=short
docker compose --profile demo config --quiet
```

The broader integration suite requires the dependencies appropriate to each test. Destructive database tests must use an explicitly isolated test database and the safeguards in `tests/safety.py`. Do not execute integration database tests against real agency data.

FastAPI exposes `/api/v1/health/live`, `/api/v1/health/ready`, and `/api/v1/health`. Liveness and database readiness do not mean that all external integrations or local AI services are ready.

## Shutdown and recovery

### Telegram fallback-ID upgrade

Real Telegram `update_id` values and existing text-only fallback IDs are unchanged.
Requests without `update_id` but with `message_id` now use a delimited hash in a
negative ID namespace, preventing concatenation collisions such as `(1, 23)` and
`(12, 3)`. No database rows are moved or deleted.

Pause ingress and drain/stop all old API and workflow workers before deploying
this version; mixed versions can claim different IDs for the same new message.
Keep the persisted inbox available throughout the upgrade. A matching completed
legacy row returns its cached response without executing another delivery.
Legacy rows in the same chat with changed text, failed delivery, or an expired
processing lease return `Legacy Identity Review` and suppress automatic replay.
An operator must reconcile those rows with notification/provider records before
retrying; do not clear the inbox to resolve the review. Old rows do not store
message IDs or synthetic-ID provenance, so ambiguous matches are deliberately
held for review rather than assumed safe to send.

The migration guard fails closed if its database lookup fails, including in demo
mode. Memory-only operation cannot preserve deduplication across restarts. The
inbox/outbox/provider boundary still requires reconciliation after a crash that
occurs after sending but before completion is persisted; this change does not
promise exactly-once external delivery. Validate the upgrade against an isolated
PostgreSQL copy before a connected production deployment.

```bash
docker compose --profile demo --profile connected --profile full-ai --profile dedicated-worker down
```

Do not add `--volumes` unless you intend to delete local persisted data. Database backup and restore utilities are available through `tools/manage.py` and `tools/backup_restore.py`; test restores only against isolated disposable targets.

The historical acceptance evidence and remaining limitations are under [`docs/audit/`](docs/audit/), especially [`REMAINING_RISKS.md`](docs/audit/REMAINING_RISKS.md).
