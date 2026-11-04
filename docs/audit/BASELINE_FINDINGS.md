# RENTKOM SMART ENTERPRISE — BASELINE FORENSIC AUDIT FINDINGS

**Audit Date:** 2026-11-03
**Auditor:** Principal Software Engineer, Software Architect, Security & SRE Engineer
**Repository:** `https://github.com/Eclipse-XS/rentkom-smart-enterprise`
**Target Revision:** `feature/portfolio-overhaul` / `origin/main` baseline
**Methodology:** Zero-assumption, code-level forensic verification across Python core, SQL migrations, n8n workflows, Docker orchestration, testing suites, and CI pipelines.

---

## 1. Executive Summary & Inventory

The initial independent audit estimated approximately 45/100 engineering readiness, pointing to systemic gaps across authentication, fake SMTP delivery, Docker container networking, ungrounded RAG hallucinations, CI test coverage discrepancies, and dangerous database testing fixtures.

Our independent forensic audit confirms these major architectural and operational deficits. More critically, we identified a **P0 data-safety vulnerability** where integration tests perform destructive `DROP TABLE ... CASCADE` operations against hardcoded PostgreSQL targets without verifying database isolation.

### Baseline Component Status Matrix

| Subsystem | Component | Implementation Status | Test Coverage | Verification Status | Defect Level |
|---|---|---|---|---|---|
| **Security & Auth** | FastAPI Route Protection | Incomplete (administrative endpoints public) | Minimal (only telegram guard tested) | `CONFIRMED` | **P1** |
| **Secrets Management** | Datasources & Workflows | Deficient (plaintext password in datasources.yml) | None | `CONFIRMED` | **P0** |
| **Test Safety** | PostgreSQL Integration Fixtures | Destructive (`DROP TABLE` against 127.0.0.1:5433) | Unsafe | `CONFIRMED` | **P0** |
| **Business Core** | Customer Deduplication | Vulnerable to concurrency races (find-then-insert) | Unit (single-threaded only) | `CONFIRMED` | **P1** |
| **Business Core** | Request State Machine | Missing transition validation | None | `CONFIRMED` | **P1** |
| **Database** | SQL Migrations & Legacy Views | Broken SQL aggregate in schema_v2, incomplete init_db | Partial SQLite mock | `CONFIRMED` | **P1** |
| **Integrations** | Transactional Outbox (SMTP) | FAKE success (hardcoded success without SMTP send) | Mock only | `CONFIRMED` | **P0** |
| **Integrations** | Telegram Idempotency | Non-deterministic `hash()`, unsafe silent memory fallback | In-process only | `CONFIRMED` | **P1** |
| **Integrations** | n8n Container Networking | Hardcoded `127.0.0.1:8000` inside container exports | Localhost only | `CONFIRMED` | **P1** |
| **AI / RAG** | Retrieval Grounding & Abstention | Hallucinates "grounded" on zero-retrieval; fixed 0.85 conf | Positive tests only | `CONFIRMED` | **P1** |
| **AI / RAG** | Legal Boundary | System prompt claims "професійний юрист" | None | `CONFIRMED` | **P2** |
| **Observability** | Grafana Dashboards | Missing plugins, unassigned datasources in panels | Static JSON inspection | `CONFIRMED` | **P1** |
| **Operations** | Database Backup & Restore | Missing automated pg_dump / restore verification | None | `CONFIRMED` | **P1** |
| **CI / Quality** | GitHub Actions Workflow | Only 17 unit tests run; integration/E2E excluded | 17/75 tests | `CONFIRMED` | **P1** |

---

## 2. Detailed Findings Register

### TEST-P0-01: Destructive PostgreSQL Drops Against Unverified Connection
- **Severity:** P0 (Critical Data Safety)
- **Status:** `CONFIRMED`
- **File & Line:** `tests/integration/test_real_postgresql_v2.py:16-44`
- **Actual Code Behavior:**
  ```python
  POSTGRES_TEST_URL = "postgresql+psycopg2://postgres@127.0.0.1:5433/postgres"

  @pytest.fixture(scope="function")
  def clean_pg_schema(pg_engine):
      with pg_engine.connect() as conn:
          for drop_stmt in [
              "DROP VIEW IF EXISTS real_estate_requests CASCADE;",
              "DROP TABLE IF EXISTS real_estate_requests CASCADE;",
              "DROP TABLE IF EXISTS notification_attempts CASCADE;", ...
  ```
  The fixture unconditionally executes destructive `DROP TABLE ... CASCADE` on the hardcoded database URL without verifying database identity, without inspecting `TEST_DATABASE_URL`, and without checking whether port 5433 or the target database is an application or development database.
- **Expected Behavior:** Destructive operations must only ever execute against a dedicated, isolated test database whose name explicitly indicates disposable test scope (e.g., `*_test`). The test suite must refuse execution against default or application databases, and must fail fast if `TEST_DATABASE_URL` is absent or unverified.
- **Reproduction Procedure:** Run pytest with an active postgres database on port 5433. All target tables and views will be deleted.
- **Business Impact:** Accidental destruction of developer or production database tables during local test invocation.
- **Proposed Fix:** Introduce a strict database safety validator in `tests/conftest.py` that verifies the database name and safety token; mandate `TEST_DATABASE_URL`; abort if connected to `lab4_real_estate` or non-test databases.
- **Required Tests:** Negative test verifying that execution against an unverified database URL is immediately refused.

---

### SEC-P0-02: Plaintext PostgreSQL Password in Grafana Provisioning
- **Severity:** P0 (Credential Exposure)
- **Status:** `CONFIRMED`
- **File & Line:** `SMART_ENTERPRISE/monitoring/provisioning/datasources.yml:16-17`
- **Actual Code Behavior:**
  ```yaml
    secureJsonData:
      password: "VadymGone123"
  ```
  The plaintext credential `"VadymGone123"` and username `"lab4_user"` are stored directly in the committed provisioning file.
- **Expected Behavior:** Secrets must never be stored in plain text in version-controlled configuration files. Environment variable interpolation (`${POSTGRES_PASSWORD}`) must be used, matching the pattern in `provisioning/datasources/datasources.yml`.
- **Reproduction Procedure:** Inspect `SMART_ENTERPRISE/monitoring/provisioning/datasources.yml`.
- **Business Impact:** Public exposure of internal database credentials; credential reuse risk across environments.
- **Proposed Fix:** Sanitize `datasources.yml` to use environment variable references, align with docker-compose volume mounts, and document mandatory credential rotation.
- **Required Tests:** Repository secret audit test checking that zero plaintext passwords exist in `SMART_ENTERPRISE/`.

---

### INT-P0-01: Falsified SMTP Notification Success
- **Severity:** P0 (Integration Integrity)
- **Status:** `CONFIRMED`
- **File & Line:** `core/services/outbox_service.py:103-111`
- **Actual Code Behavior:**
  ```python
  elif settings.NOTIFICATION_PROVIDER == "smtp":
      if not settings.SMTP_HOST:
          is_success = True
          http_code = 250
      else:
          # In production SMTP send would occur here
          is_success = True
          http_code = 250
  ```
  When `NOTIFICATION_PROVIDER == "smtp"`, the outbox processor marks notifications as `PROVIDER_ACCEPTED` with HTTP 250 without initiating an SMTP socket connection, regardless of whether `SMTP_HOST` is configured, valid, or unreachable.
- **Expected Behavior:** A real, robust `SMTPTransportAdapter` must connect to the configured SMTP server using TLS and authentication, construct a MIME email, dispatch the message, and only record success upon SMTP server acknowledgment. If `SMTP_HOST` is missing or fails, it must record an error, increment `retry_count`, and schedule exponential backoff.
- **Reproduction Procedure:** Configure `NOTIFICATION_PROVIDER=smtp` with an empty or non-existent `SMTP_HOST`. Call `process_outbox()`. The outbox returns `success_count=1`, `status="PROVIDER_ACCEPTED"`.
- **Business Impact:** Silent failure of administrative and customer communications; false positive delivery logs.
- **Proposed Fix:** Implement a real, testable `SMTPTransportAdapter` using Python `smtplib`/`email.message` with connection timeout, TLS, authentication, and structured error reporting.
- **Required Tests:** Local SMTP test against an isolated test server (e.g. `aiosmtpd` / Mailpit) proving genuine network delivery, plus failure tests for unreachable hosts.

---

### SEC-P1-01: Unprotected Sensitive Administrative and Ingestion Endpoints
- **Severity:** P1 (Authorization Boundary)
- **Status:** `CONFIRMED`
- **File & Line:** `core/api/v1/endpoints/requests.py:16-43`, `notifications.py:11-29`, `feedbacks.py:16-23`
- **Actual Code Behavior:**
  - `GET /api/v1/requests` has zero authentication: anyone can dump all customer names, phone numbers, and consultation requests.
  - `PATCH /api/v1/requests/{id}/status` allows unauthenticated callers to change request status and spoof the `changed_by` identity.
  - `GET /api/v1/notifications/outbox` leaks notification payloads, recipient email addresses, and phone numbers.
  - `POST /api/v1/notifications/process` allows unauthenticated triggers of batch background jobs.
  - `GET /api/v1/feedbacks` exposes customer reviews and identifiers without restriction.
- **Expected Behavior:** Clear separation between:
  1. **Public Customer Ingress:** `POST /api/v1/requests`, `POST /api/v1/feedbacks` (rate-limited, open to customers).
  2. **Administrative Operations:** `GET /api/v1/requests`, `PATCH /api/v1/requests/{id}/status`, `GET /api/v1/notifications/outbox`, `GET /api/v1/feedbacks` (protected by `ADMIN_API_KEY` / Bearer token).
  3. **Internal Service Operations:** `POST /api/v1/notifications/process` (protected by `INTERNAL_API_KEY`).
  Consistent HTTP 401/403 responses must be returned for unauthenticated/unauthorized calls.
- **Reproduction Procedure:** Execute `curl -X GET http://127.0.0.1:8000/api/v1/requests` without any authentication headers; HTTP 200 is returned with customer PII.
- **Business Impact:** Critical violation of data privacy standards (GDPR/Ukrainian Data Protection Law); potential denial of service and data tampering.
- **Proposed Fix:** Implement reusable security dependencies in `core/security.py` and apply to administrative endpoints.
- **Required Tests:** Negative security test suite asserting HTTP 401 for requests lacking authorization headers, and HTTP 403 for invalid credentials.

---

### DATA-P1-01: Concurrency Race in Customer Deduplication
- **Severity:** P1 (Concurrency Safety)
- **Status:** `CONFIRMED`
- **File & Line:** `core/services/request_service.py:27-38`
- **Actual Code Behavior:**
  ```python
  customer = db.query(Customer).filter(Customer.phone == norm_phone).first()
  if not customer:
      customer = Customer(
          full_name=req.full_name.strip(),
          phone=norm_phone, ...
      )
      db.add(customer)
      db.flush()
  ```
  The read-then-write customer lookup is not atomic. If two concurrent HTTP requests arrive with the same new phone number, both queries return `None` and both execute `db.add(customer)`, triggering a PostgreSQL `UniqueViolation` (`IntegrityError`) and an unhandled HTTP 500 error on the second request.
- **Expected Behavior:** Concurrency-safe customer resolution utilizing PostgreSQL atomic upsert (`ON CONFLICT (phone) DO UPDATE`) or transactional savepoint retry, ensuring exactly one customer row is created without HTTP 500 exceptions.
- **Reproduction Procedure:** Dispatch concurrent asynchronous POST requests to `/api/v1/requests` with identical new phone numbers. One request fails with HTTP 500.
- **Business Impact:** Lost leads and degraded customer experience during concurrent usage spikes.
- **Proposed Fix:** Implement atomic upsert with conflict handling on `customers.phone` in `request_service.py`.
- **Required Tests:** Concurrency integration test using thread pools or async tasks asserting zero HTTP 500 errors and exactly 1 created customer record.

---

### DATA-P1-02: Missing Service Request State Machine Validation
- **Severity:** P1 (Business Logic Integrity)
- **Status:** `CONFIRMED`
- **File & Line:** `core/services/request_service.py:130-150`
- **Actual Code Behavior:**
  `update_request_status` permits any status transition to any other status (e.g. from `COMPLETED` or `CANCELLED` back to `NEW`). The `changed_by` field is blindly trusted from client input.
- **Expected Behavior:** Explicit state transition graph:
  - `NEW` $\to$ `IN_REVIEW`, `CANCELLED`
  - `IN_REVIEW` $\to$ `SCHEDULED`, `CANCELLED`
  - `SCHEDULED` $\to$ `COMPLETED`, `CANCELLED`
  - `COMPLETED` $\to$ (terminal state, immutable)
  - `CANCELLED` $\to$ (terminal state, immutable)
  Invalid transitions must be rejected with HTTP 400. The authenticated user identity must override `changed_by`.
- **Reproduction Procedure:** Issue `PATCH /api/v1/requests/1/status` with `{"new_status": "NEW"}` on an already `COMPLETED` request; the request succeeds.
- **Business Impact:** Corruption of business workflows, inaccurate reporting, and illegitimate state reversion.
- **Proposed Fix:** Introduce `VALID_TRANSITIONS` mapping in `request_service.py`, validate transitions, and record the authenticated actor in audit logs.
- **Required Tests:** Unit tests verifying valid forward transitions and rejecting illegal transitions.

---

### DATA-P1-03: SQL Aggregate Syntax Error & Incomplete Data Migration
- **Severity:** P1 (Data Integrity)
- **Status:** `CONFIRMED`
- **File & Line:** `SMART_ENTERPRISE/database/schema_v2.sql:149`, `SMART_ENTERPRISE/database/init_db.sql:167`
- **Actual Code Behavior:**
  - `schema_v2.sql`: `SELECT DISTINCT full_name, phone, MIN(created_at) FROM public.real_estate_requests` fails on PostgreSQL because `MIN()` is an aggregate function used without `GROUP BY full_name, phone`.
  - `init_db.sql`: The legacy migration block merely renames `real_estate_requests` to `real_estate_requests_legacy_lr4` without copying existing records into `customers` and `service_requests`.
- **Expected Behavior:** PostgreSQL migration script must use standard, validated SQL (`GROUP BY full_name, phone`), migrate all historical rows into normalized tables with referential integrity, and provide idempotent execution.
- **Reproduction Procedure:** Execute `schema_v2.sql` on a PostgreSQL database with populated `real_estate_requests`. Query fails with `ERROR: column "real_estate_requests.full_name" must appear in the GROUP BY clause`.
- **Business Impact:** Migration failures in production; total loss of historical lead records.
- **Proposed Fix:** Correct the SQL aggregation query with proper `GROUP BY` and ensure complete data preservation across both `schema_v2.sql` and `init_db.sql`.
- **Required Tests:** Integration test executing migration on populated legacy tables and verifying 100% row preservation.

---

### AI-P1-01: Falsely Claimed Grounding on Empty Retrieval
- **Severity:** P1 (AI Trustworthiness)
- **Status:** `CONFIRMED`
- **File & Line:** `core/services/rag_service.py:127-175`
- **Actual Code Behavior:**
  When vector search returns zero chunks or Qdrant is unreachable, `context_str` is empty. The service still calls Ollama with an empty context and returns `mode="grounded"`, `sources=["База знань «Рентком»"]`, and `confidence_score=0.85`.
- **Expected Behavior:** A response must never be marked `mode="grounded"` without retrieved context satisfying a calibrated similarity threshold (e.g. `score >= 0.50`). On zero-retrieval or low confidence, the system must explicitly abstain (`mode="abstention"` or `mode="fallback"`) and must not hallucinate sources.
- **Reproduction Procedure:** Query `query_rag("як варити борщ?")` when out-of-domain regex is bypassed or when the database is empty. The model generates text and tags it as `grounded` with 85% confidence.
- **Business Impact:** Hallucinated real estate policies and fabricated advice presented to clients as official agency regulations.
- **Proposed Fix:** Enforce a strict grounding gate: verify retrieved chunks and minimum score threshold before claiming grounded mode; abstain otherwise.
- **Required Tests:** Test suite for empty retrieval, low-similarity retrieval, out-of-domain queries, and abstention verification.

---

### INT-P1-02: Broken Container Networking in n8n Workflows
- **Severity:** P1 (Operational Interoperability)
- **Status:** `CONFIRMED`
- **File & Line:** `SMART_ENTERPRISE/automation/n8n/unified_telegram_router.json:48, 219, 248, 277, 306, 328, 357`, `feedback_to_telegram.json:71`
- **Actual Code Behavior:**
  HTTP Request nodes in n8n workflows target `http://127.0.0.1:8000`. Inside Docker, `127.0.0.1` is the n8n container itself, causing all requests to `business-api` to fail with `ECONNREFUSED`.
- **Expected Behavior:** Workflows in Docker Compose must communicate via the internal network service name `http://business-api:8000`, with configurable fallback via environment variables for standalone execution.
- **Reproduction Procedure:** Start Docker Compose stack (`connected` profile). Invoke the Telegram webhook in n8n. Node execution fails with connection error to `127.0.0.1:8000`.
- **Business Impact:** Complete failure of automation workflows when deployed via Docker Compose.
- **Proposed Fix:** Update workflow definitions to use service discovery (`http://business-api:8000`) or environment variables (`{{ $env.BUSINESS_API_URL }}`).
- **Required Tests:** Automated schema inspection test ensuring zero hardcoded `127.0.0.1:8000` references in container-targeted workflows.

---

### CI-P1-01: Discrepancy Between Claimed Test Coverage and CI Execution
- **Severity:** P1 (DevOps & Verification)
- **Status:** `CONFIRMED`
- **File & Line:** `.github/workflows/ci.yml:35`, `README.md`
- **Actual Code Behavior:**
  README previously asserted that 75 automated tests pass, but `.github/workflows/ci.yml` executed only `pytest tests/unit/ -v` (17 tests) because the remaining 58 integration and E2E tests failed in CI due to missing service dependencies and hardcoded port assumptions (port 5433).
- **Expected Behavior:** CI must run all unit, security, and offline integration tests cleanly without developer machine dependencies. CI pipeline should include distinct jobs for linting, security scanning, unit testing, and isolated database integration testing.
- **Reproduction Procedure:** Inspect `.github/workflows/ci.yml`. Observe that only `tests/unit/` is executed.
- **Business Impact:** Hidden regressions in database, integration, and E2E layers; false representation of test verification in repository badges.
- **Proposed Fix:** Structure CI into clear stages (Lint, Security, Unit Tests, Isolated DB Integration Tests); eliminate host-specific port assumptions in test files.
- **Required Tests:** CI workflow execution and test pass verification across all isolated suites.

---

### SEC-P2-01: Wildcard CORS and Container Root User
- **Severity:** P2 (Hardening Defect)
- **Status:** `CONFIRMED`
- **File & Line:** `core/main.py:60`, `Dockerfile:1-29`
- **Actual Code Behavior:**
  `CORSMiddleware` allows `allow_origins=["*"]`. `Dockerfile` has no non-root user and executes uvicorn as `root`.
- **Expected Behavior:** CORS origins must be restricted to configured frontend domains via `settings.CORS_ORIGINS`. Container must drop privileges and run as an unprivileged `appuser`.
- **Reproduction Procedure:** Inspect `core/main.py` and `Dockerfile`.
- **Business Impact:** CSRF vulnerabilities and expanded blast radius in container breakout scenarios.
- **Proposed Fix:** Parameterize `CORS_ORIGINS` in `core/config.py`; add `RUN useradd -m appuser && USER appuser` in `Dockerfile`.
- **Required Tests:** Unit test verifying configured CORS headers; Dockerfile syntax check.

---

### AI-P2-01: Uncalibrated Confidence Scores & Legal Role Misrepresentation
- **Severity:** P2 (AI Boundary Defect)
- **Status:** `CONFIRMED`
- **File & Line:** `core/services/rag_service.py:22, 172`
- **Actual Code Behavior:**
  - `confidence_score` is directly equated to raw cosine similarity (`round(max_score, 3)`) or defaults to `0.85` if no score is found.
  - System prompt states: "Ти — професійний юрист-консультант агентства нерухомості «Рентком»".
- **Expected Behavior:** Cosine similarity is a retrieval measure, not a calibrated probability of factual accuracy. The prompt must clarify that the system is an automated informational assistant and does not provide legal advice or act as a licensed attorney.
- **Reproduction Procedure:** Inspect `core/services/rag_service.py`.
- **Business Impact:** Legal liability; user confusion regarding professional status of AI answers.
- **Proposed Fix:** Reframe system prompt to emphasize informational consultation and clarify confidence metric semantics.
- **Required Tests:** Unit tests validating disclaimer presence and prompt phrasing.

---

### OPS-P1-01: Grafana Plugin Incompatibility & Missing Backup Support
- **Severity:** P1 (Operations & Reliability)
- **Status:** `CONFIRMED`
- **File & Line:** `compose.yaml:125-152`, `technical_observability.json:99-100`
- **Actual Code Behavior:**
  Grafana container in Compose does not install `marcusolsson-json-datasource`, breaking panels in `real_estate_monitoring.json`. Two panels in `technical_observability.json` have `datasource: None`. No verified PostgreSQL backup/restore script is present.
- **Expected Behavior:** Grafana must install required plugins or panels must query PostgreSQL directly; all panels must have valid datasources; a reproducible backup/restore verification utility (`tools/backup_restore.py`) must be provided.
- **Reproduction Procedure:** Start Grafana container; observe plugin load errors. Inspect panels 99-100 in `technical_observability.json`.
- **Business Impact:** Inability to view operational metrics; risk of data loss due to unverified backup procedures.
- **Proposed Fix:** Fix panel datasource assignments, configure `GF_INSTALL_PLUGINS`, and create an automated backup/restore verification script.
- **Required Tests:** Backup/restore verification test and Grafana JSON schema audit.

---

## 3. Forensic Conclusion & Gate Prioritization

The findings confirm that the repository requires substantive engineering remediation across all major layers. Work will proceed strictly according to the defined implementation order:

1. **Gate 1 (Safety):** Fix P0 destructive database tests (`TEST-P0-01`), sanitize secrets (`SEC-P0-02`), implement authentication model (`SEC-P1-01`, `SEC-P1-02`), and harden containers (`SEC-P2-01`).
2. **Gate 2 (Correctness):** Fix customer concurrency race (`DATA-P1-01`), enforce request lifecycle state machine (`DATA-P1-02`), and resolve SQL migration bugs (`DATA-P1-03`).
3. **Gate 3 (Integrations):** Replace fake SMTP success with real `SMTPTransportAdapter` (`INT-P0-01`), fix Telegram hash non-determinism (`INT-P1-01`), and correct n8n container networking (`INT-P1-02`).
4. **Gate 4 (AI Trustworthiness):** Fix ungrounded RAG hallucinations (`AI-P1-01`), calibrate confidence/sources, and enforce legal assistant boundaries (`AI-P2-01`).
5. **Gate 5 (Operations):** Fix Grafana datasource/plugin configuration, implement verified backup/restore (`OPS-P1-01`).
6. **Gate 6 (Quality Assurance):** Unify test suite, expand CI pipeline (`CI-P1-01`), and conduct final clean-room verification.
