# RENTKOM SMART ENTERPRISE — TEST EXECUTION EVIDENCE

**Execution Date:** 2026-11-03
**Branch:** `remediation/engineering-acceptance`
**Python Runtime:** Python 3.13.5 (`pytest-9.1.1`)
**PostgreSQL Test Instance:** PostgreSQL 18.x on isolated disposable cluster (`postgresql+psycopg2://postgres@127.0.0.1:5439/rentkom_test`)
**Vector & LLM Runtimes:** Qdrant (`127.0.0.1:6333`, 32 indexed points) + Ollama (`127.0.0.1:11434`, `mxbai-embed-large:latest` + `qwen3:4b-instruct-2507-q4_K_M`)

---

## 1. Summary of Executed Test Suites

| Suite / Scope | Command | Collected | Passed | Skipped | Failed | Duration |
|---|---|---:|---:|---:|---:|---:|
| **Unit, Security & Workflow Contracts** | `pytest tests/unit -v --tb=short` | `45` | **`45`** | `0` | `0` | `1.30s` |
| **SMTP Transport & Telegram Connected Contract** | `pytest tests/integration/test_smtp_delivery.py tests/integration/test_telegram_connected_mode_contract.py -v --tb=short` | `20` | **`20`** | `0` | `0` | `2.14s` |
| **Full Suite (Unit + PG18 Integration + Backup/Restore + Live Qdrant/Ollama E2E)** | `pytest tests/ -v --tb=short` | `112` | **`108`** | `4` *(live n8n port 5678)* | `0` | `164.67s` |
| **PostgreSQL SQL Bootstrap & Idempotent Migration** | `psql -v ON_ERROR_STOP=1 -f init_db.sql` & `2x schema_v2.sql` | `3 runs` | **`3`** | `0` | `0` | `1.8s` |
| **30-Question Live RAG Evaluation Benchmark** | `python tools/evaluate_rag_suite.py` | `30 queries` | **`30`** | `0` | `0` | `428.5s` |
| **Docker Compose Profile Validation** | `docker compose --profile <p> config --quiet` | `4 profiles` | **`4`** | `0` | `0` | `1.9s` |

> **Why 4 tests skipped in `pytest tests/`:** The 4 tests in `tests/integration/test_n8n_unified_router_live.py` require a live n8n server listening on `http://localhost:5678/webhook/rentkom-unified-router-test` and cleanly `pytest.skip(...)` when port `5678` is not open. All 8 n8n workflow JSON contracts and the local workflow execution harness are verified in `tests/unit/test_n8n_workflows_contract.py` (`4/4 PASSED`).

---

## 2. Detailed Test Breakdown by Module

### 2.1 Unit & Contract Tests (`tests/unit/` — 45/45 PASSED)
- **`test_security_and_safety.py` (10/10 PASSED)**:
  - `test_safety_guard_rejects_application_and_default_databases`
  - `test_safety_guard_rejects_unmarked_database`
  - `test_safety_guard_accepts_test_database`
  - `test_unauthorized_endpoints_rejected_401_missing_credentials`
  - `test_invalid_credentials_rejected_403`
  - `test_authorized_endpoints_succeed_with_valid_credentials`
  - `test_missing_configured_server_key_returns_500_no_bypass`
  - `test_environment_modes_demo_connected_production`
  - `test_public_customer_ingestion_remains_open`
  - `test_zero_plaintext_secrets_in_provisioning`
- **`test_business_core_concurrency_and_lifecycle.py` (8/8 PASSED)**:
  - `test_normalize_phone_canonical_formats`
  - `test_normalize_phone_invalid_lengths`
  - `test_request_state_machine_valid_lifecycle`
  - `test_request_state_machine_rejects_illegal_transitions`
  - `test_repeated_source_row_id_idempotency_and_conflict`
  - `test_transaction_rollback_on_failure_leaves_zero_partial_records`
  - `test_concurrent_customer_deduplication[2]`
  - `test_concurrent_customer_deduplication[10]`
- **`test_n8n_workflows_contract.py` (4/4 PASSED)**:
  - `test_all_eight_n8n_workflows_valid_json_and_zero_localhost_urls`
  - `test_single_authoritative_active_telegram_router`
  - `test_feedback_workflow_does_not_default_missing_ratings_to_five`
  - `test_end_to_end_local_workflow_contract_harness`
- **`test_rag_grounding_and_safety.py` (6/6 PASSED)**:
  - `test_system_prompt_has_legal_disclaimer_and_untrusted_evidence_rules`
  - `test_indirect_prompt_injection_in_retrieved_chunk_is_sanitized`
  - `test_rag_never_returns_grounded_when_qdrant_offline[asyncio]`
  - `test_rag_abstains_when_retrieval_returns_empty_or_low_similarity[asyncio]`
  - `test_rag_grounded_with_real_retrieved_chunks_and_exact_score[asyncio]`
  - `test_rag_evaluation_dataset_has_30_questions_across_5_categories`
- **`test_outbox_logic.py` (9/9 PASSED)**:
  - Atomicity, mock processing, crash recovery, dead-letter after `max_retries`, malformed recipient, idempotency, exponential backoff, optional client notification, Telegram channel outbox.
- **`test_telegram_idempotency.py` (2/2 PASSED)**, **`test_telegram_router_logic.py` (1/1 PASSED)**, **`test_deduplication.py` (1/1 PASSED)**, **`test_validation.py` (4/4 PASSED)**.

### 2.2 Integration Tests (`tests/integration/` — 52 PASSED, 4 SKIPPED)
- **`test_smtp_delivery.py` (6/6 PASSED)**: Real local TCP SMTP server delivery, 503 missing host, 502 connection refused, 504 timeout, 535 auth failure without password leak, 550/451 recipient errors, idempotent reprocessing.
- **`test_telegram_connected_mode_contract.py` (14/14 PASSED)**: Demo vs. connected auth boundaries, fail-closed on missing server keys, duplicate update drop, test-to-real Telegram block, 429/503/timeout/200 upstream handling, zero secret leakage in responses and workflow exports.
- **`test_backup_restore.py` (2/2 PASSED)**: Rejects non-test DBs; executes real `pg_dump` and `psql` restore into disposable PostgreSQL 18 database `rentkom_restore_test` and verifies all table counts.
- **`test_real_postgresql_v2.py` (3/3 PASSED)**: Full migration lifecycle, `FOR UPDATE SKIP LOCKED` concurrent workers, crash recovery on PostgreSQL 18.
- **`test_legacy_lr4_postgres_v2.py` (4/4 PASSED)**: Legacy `real_estate_requests` view `INSTEAD OF INSERT ... RETURNING id`, `ON CONFLICT` idempotency, phone deduplication, outbox + Grafana views.
- **`test_lr4_notification_pipeline_forensics.py` (5/5 PASSED)**: Single delivery, repeated `source_row_id` zero duplicates, provider recovery, concurrent identical `source_row_id`, at-least-once crash boundary.
- **`test_demo_seed_idempotency.py` (3/3 PASSED)**, **`test_lr3_feedback_pipeline.py` (1/1 PASSED)**, **`test_outbox_automatic_lifecycle.py` (2/2 PASSED)**, **`test_outbox_concurrency.py` (1/1 PASSED)**, **`test_telegram_inbox_postgres.py` (2/2 PASSED)**, **`test_database_migration.py` (1/1 PASSED)**, **`test_feedbacks_api.py` (1/1 PASSED)**, **`test_requests_api.py` (2/2 PASSED)**, **`test_rag_evaluation_holdout.py` (5/5 PASSED)**.

### 2.3 End-to-End Tests (`tests/e2e/` — 11/11 PASSED)
- **`test_cleanroom_business_scenarios.py` (5/5 PASSED)**: Scenarios A, B, C, D, E.
- **`test_e2e_scenarios.py` (1/1 PASSED)**: Full business pipeline.
- **`test_full_system_demo_e2e.py` (2/2 PASSED)**: Scenario 1 (FastAPI $\to$ PostgreSQL 18 $\to$ Outbox $\to$ Grafana SQL) and Scenario 2 (Unified Router $\to$ Qdrant $\to$ Ollama live RAG).
- **`test_rag_guardrails.py` (3/3 PASSED)**: Prompt injection, out-of-domain, and offline fallback guardrails.
