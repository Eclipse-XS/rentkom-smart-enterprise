# RENTKOM SMART ENTERPRISE — TEST EXECUTION EVIDENCE

**Execution Date:** 2026-11-04
**Branch:** `fix/rentkom-complete-remediation`
**Python Runtime:** Python 3.13.5 (`pytest-9.1.1`)
**PostgreSQL Test Environments:**
  - Ephemeral PostgreSQL 18.x on host (`127.0.0.1:5439/rentkom_test`)
  - Containerized PostgreSQL 16.x in Docker (`rentkom-postgres:5432/lab4_real_estate`)
  - GitHub Actions CI PostgreSQL 16 service container (`rentkom_test`)
**Vector & LLM Runtimes (Local Stack):**
  - Qdrant container (`127.0.0.1:6333`, 32 indexed points)
  - Ollama host daemon (`127.0.0.1:11434`, `mxbai-embed-large:latest` + `qwen3:4b-instruct`)
**Automation Runtime:**
  - n8n container (`127.0.0.1:5678`, workflow `Rentkom Unified Telegram Router` active)

---

## 1. Local Full Suite Execution vs. GitHub Actions CI Execution

We strictly distinguish between the **Local Integrated Stack** (where all optional supporting containers run) and the **GitHub Actions Multi-Stage CI Pipeline** (which runs isolated in cloud runners without GPU or multi-service daemon overhead):

### 1.1 Local Full Suite Execution (All Daemons Running)

| Suite / Scope | Command | Collected | Passed | Skipped | Failed | Duration |
|---|---|---:|---:|---:|---:|---:|
| **Unit, Security & Safety Suite** | `pytest tests/unit -v --tb=short` | `60` | **`60`** | `0` | `0` | `1.79s` |
| **SMTP Transport & Telegram Connected Contract** | `pytest tests/integration/test_smtp_delivery.py tests/integration/test_telegram_connected_mode_contract.py -v --tb=short` | `20` | **`20`** | `0` | `0` | `4.36s` |
| **Live n8n Unified Router Suite** | `pytest tests/integration/test_n8n_unified_router_live.py -v --tb=short` | `4` | **`4`** | `0` | `0` | `61.04s` |
| **Full Pytest Suite (Unit + Integration + E2E + Live n8n)** | `pytest tests/ -v --tb=short` | `127` | **`127`** | **`0`** | `0` | `220.12s` |
| **PostgreSQL SQL Bootstrap & Idempotent Migration** | `psql -v ON_ERROR_STOP=1 -f init_db.sql` & `2x schema_v2.sql` | `3 runs` | **`3`** | `0` | `0` | `1.8s` |
| **30-Question Live RAG Evaluation Benchmark** | `python tools/evaluate_rag_suite.py` | `30 queries` | **`30`** | `0` | `0` | `485.2s` |
| **Docker Compose Profile Validation** | `docker compose --profile <p> config --quiet` | `4 profiles` | **`4`** | `0` | `0` | `1.9s` |

### 1.2 GitHub Actions CI Pipeline Execution

| CI Stage | Command / Focus | Passed | Skipped | Failed | Duration |
|---|---|---:|---:|---:|---:|
| **Stage 1: Syntax, Security & Workflow Contracts** | Compile check + n8n JSON schema + Docker Compose profiles | **`4`** | `0` | `0` | `19s` |
| **Stage 2: Unit, Security & SMTP Transport** | `pytest tests/unit` (54) + SMTP & Telegram contracts (19 pass, 1 skip) | **`73`** | **`1`** | `0` | `20s` |
| **Stage 3: PostgreSQL Integration & Backup/Restore** | SQL bootstrap + Idempotent migrations + Integration/E2E tests | **`59`** | **`8`** | `0` | `43s` |

#### Forensic Explanation of Intentional CI Skips
The 9 total skips in GitHub Actions are intentional, designed graceful degradations when external daemon ports are absent in standard CI runners:
1. **1 Skip in Stage 2 (`test_telegram_connected_mode_contract.py::test_n8n_execution_output_contains_zero_plaintext_tokens`)**:
   - *Reason:* Checks historical execution records inside a local SQLite database (`runtime/n8n/database.sqlite`). When running in clean cloud CI, this historical artifact is absent and the test cleanly skips.
2. **4 Skips in Stage 3 (`test_n8n_unified_router_live.py`)**:
   - *Reason:* All 4 tests in this module require a live n8n webhook daemon on `http://localhost:5678`. Standard CI runners run PostgreSQL 16 via a service container, but do not start the full n8n Node.js daemon. Each test verifies port 5678 and skips via `pytest.skip("Live n8n daemon not listening on port 5678")`.
3. **3 Skips in Stage 3 (`test_rag_evaluation_holdout.py::test_live_rag_grounded_answer[...]`)**:
   - *Reason:* Validates real-time neural LLM inference against Qdrant (port 6333) and Ollama (port 11434). Standard GitHub Actions runners lack GPUs and local LLM model weights; the tests gracefully skip when ports 6333/11434 are closed.
4. **1 Skip in Stage 3 (`test_telegram_connected_mode_contract.py`)**:
   - *Reason:* Same historical SQLite artifact check as in Stage 2.

---

## 2. 30-Question Live RAG Evaluation Results (`evaluation/rag_eval_results.json`)

Executed via `tools/evaluate_rag_suite.py` against live Qdrant (32 indexed points from agency knowledge base) and live Ollama (`mxbai-embed-large:latest` and `qwen3:4b-instruct`, fresh `live_inference` run):

```json
{
  "total_questions": 30,
  "run_mode": "live_inference",
  "is_cached_run": false,
  "retrieval_single_doc_recall": 0.8667,
  "retrieval_multi_doc_full_coverage": 0.2000,
  "retrieval_multi_doc_partial_recall": 0.8000,
  "overall_source_coverage_ratio": 0.7500,
  "recall_at_k": 0.8500,
  "factual_claim_accuracy": 0.8000,
  "heuristic_claim_match_proxy": 0.8000,
  "answer_keyword_coverage": 0.9500,
  "citation_support_rate": 0.7368,
  "citation_entailment_proxy": 0.7368,
  "abstention_accuracy": 1.0000,
  "unnecessary_abstention_rate": 0.0500,
  "unsupported_answer_rate": 0.1667,
  "p50_latency_seconds": 24.329,
  "p95_latency_seconds": 34.489
}
```

### Analysis of Factual Grounding & Limitations
- **Retrieval Hit Rate (Recall@K = 85.0%)**: 17 of 20 in-domain questions retrieved their authoritative primary documents.
- **Unsupported Answer Rate (16.67%, 5 / 30)**: Questions `Q11, Q13, Q14, Q15, Q23` lacked complete qualifying citation coverage across required documents (primarily due to the top-K=3 retrieval budget on multi-document queries).
- **Citation Support Rate (73.68%, 14 / 19)**: 14 of 19 grounded responses carried qualifying citations with cosine similarity $\ge 0.55$ covering all expected ground-truth documents.
- **Abstention Accuracy (100.0%, 10 / 10)**: 5 out-of-domain questions and 5 prompt injection attacks were cleanly rejected.
- **Methodological Disclaimer**: Passing 5 static prompt injection queries does not constitute complete defense against novel multi-turn, token-smuggling, or zero-day jailbreak variants.

---

## 3. External Integration Testing Boundaries

We explicitly state that:
1. **SMTP Delivery**: Verified using real socket-level TCP connections against an in-process local SMTP server (`test_smtp_delivery.py`), covering STARTTLS handshakes, authentication, recipient errors (`550`, `451`), and network timeouts (`504`). It was **not** tested against an external enterprise SMTP server over the public internet to prevent unsolicited test emails.
2. **Telegram Bot Dispatch**: Verified using contract tests against mock dispatchers, local database lease recovery, and simulated upstream responses (`200`, `429`, `503`, timeouts). It was **not** connected to `api.telegram.org` with a live commercial production bot token to prevent live messaging side-effects.

---

## 4. Test Suite Detailed Breakdown (60 Unit + 56 Integration + 11 E2E)

### 4.1 Unit Tests (`tests/unit/` — 60 PASSED)
- `test_security_and_safety.py` (14 PASSED): Database safety guards, 401 missing credentials, 403 invalid credentials, fail-closed 500 when keys unset, public endpoint accessibility, zero plaintext secrets in provisioning, **public endpoint rate limiting (HTTP 429)**, **trusted proxy X-Forwarded-For spoofing prevention**, **RAG endpoint rate limiting**, and **dynamically configurable agency contact details**.
- `test_rag_claim_evaluation.py` (11 PASSED): Regression suite for factual claim verification and cache safety: negation rejection, accidental word match rejection, partial correctness measurement, citation entailment rejection of invalid/unrelated citations, multi-document retrieval missing primary source detection, **default live execution invocation**, **500+ character full answer evaluation**, **missing citation objects failure enforcement**, **rejection of old/incompatible caches**, **explicit valid cache reuse**, and **distinct labeling of fresh vs cached runs**.
- `test_business_core_concurrency_and_lifecycle.py` (8 PASSED): Phone normalization, state machine lifecycle transitions, repeated source row ID conflict, transaction rollback on failure, and concurrent customer deduplication (2 and 10 threads).
- `test_n8n_workflows_contract.py` (4 PASSED): All 8 workflow exports valid JSON without localhost URLs, single active router, feedback rating preservation, local workflow execution harness.
- `test_rag_grounding_and_safety.py` (6 PASSED): Legal disclaimer and untrusted evidence rules, prompt injection sanitization, offline Qdrant fallback, low-similarity abstention, exact score reporting, 30-question dataset validation.
- `test_outbox_logic.py` (9 PASSED): Atomicity, mock provider, crash recovery, dead-letter queue, malformed recipient, idempotency, backoff, no client address, channel notification.
- `test_telegram_idempotency.py` (2 PASSED)
- `test_telegram_router_logic.py` (1 PASSED)
- `test_deduplication.py` (1 PASSED)
- `test_validation.py` (4 PASSED)

### 4.2 Integration Tests (`tests/integration/` — 56 PASSED)
- `test_n8n_unified_router_live.py` (4 PASSED): Live webhook ping, rental request routing, feedback routing, outbox cron trigger.
- `test_smtp_delivery.py` (6 PASSED): Real TCP socket delivery, missing host 503, connection refused 502, timeout 504, auth failure 535 without password leak, recipient errors.
- `test_telegram_connected_mode_contract.py` (14 PASSED): Auth boundaries, fail-closed, duplicate updates, test-to-real block, upstream 429/503/timeout, zero secret leakage.
- `test_backup_restore.py` (2 PASSED): Safety guard rejection, real `pg_dump` and `psql` restore with cross-version `transaction_timeout` filtering.
- `test_real_postgresql_v2.py` (3 PASSED): Migration lifecycle, `FOR UPDATE SKIP LOCKED` worker concurrency, crash recovery.
- `test_legacy_lr4_postgres_v2.py` (4 PASSED): Legacy view trigger, conflict idempotency, deduplication.
- `test_lr4_notification_pipeline_forensics.py` (5 PASSED): Single delivery, duplicate detection, provider recovery, concurrent identical row ID, crash recovery.
- `test_demo_seed_idempotency.py` (3 PASSED)
- `test_lr3_feedback_pipeline.py` (1 PASSED)
- `test_outbox_automatic_lifecycle.py` (2 PASSED)
- `test_outbox_concurrency.py` (1 PASSED)
- `test_telegram_inbox_postgres.py` (2 PASSED)
- `test_database_migration.py` (1 PASSED)
- `test_feedbacks_api.py` (1 PASSED)
- `test_requests_api.py` (2 PASSED)
- `test_rag_evaluation_holdout.py` (5 PASSED)

### 4.3 End-to-End Tests (`tests/e2e/` — 11 PASSED)
- `test_cleanroom_business_scenarios.py` (5 PASSED): Scenarios A, B, C, D, E.
- `test_e2e_scenarios.py` (1 PASSED): Full business pipeline.
- `test_full_system_demo_e2e.py` (2 PASSED): Scenario 1 (FastAPI $\to$ PostgreSQL $\to$ Outbox $\to$ Grafana) and Scenario 2 (Unified Router $\to$ Qdrant $\to$ Ollama).
- `test_rag_guardrails.py` (3 PASSED): Prompt injection, out-of-domain rejection, offline graceful fallback.
