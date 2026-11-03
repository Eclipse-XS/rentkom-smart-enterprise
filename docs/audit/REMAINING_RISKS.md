# RENTKOM SMART ENTERPRISE — REMAINING RISKS & OPERATIONAL LIMITATIONS

**Audit Date:** 2026-11-03
**Branch:** `remediation/engineering-acceptance`

This document transparently records all remaining environmental constraints, architectural trade-offs, and operational considerations following completion of the engineering remediation.

---

## 1. Environmental & Host Limitations

| ID | Risk / Limitation | Classification | Impact | Mitigation / Operator Action |
|---|---|---|---|---|
| **ENV-01** | **Docker Desktop Linux Engine Stopped on Host** | `BLOCKED_BY_ENVIRONMENT` | Live container startup (`docker compose up`) could not be executed during this session because `//./pipe/dockerDesktopLinuxEngine` was unavailable on the Windows host. | All 4 Docker Compose profiles (`demo`, `connected`, `full-ai`, `dedicated-worker`) were validated via `docker compose config --quiet`, and all underlying services (PostgreSQL 18, FastAPI, Qdrant, Ollama) were verified natively. Start Docker Desktop before running `docker compose up`. |
| **ENV-02** | **External Live Telegram & Production SMTP Relay Credentials** | `BLOCKED_BY_ENVIRONMENT` | Live outbound dispatch to `api.telegram.org` and external production SMTP relays requires user-owned credentials (`TELEGRAM_BOT_TOKEN`, `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`) not stored in the repository. | Protocol adapters were verified against real local TCP SMTP servers (`tests/integration/test_smtp_delivery.py`) and deterministic HTTP upstream mocks (`429`, `503`, timeout, `200` in `test_telegram_connected_mode_contract.py`). Populate `.env` before starting `connected` or `production` mode. |
| **ENV-03** | **Local CPU LLM Inference Latency** | Hardware Constraint | Running `qwen3:4b-instruct-2507-q4_K_M` on CPU yields a median RAG latency of `17.34s` (`p95 = 31.39s`). | Generation is bounded (`num_predict=384`) with a 60-second client timeout and graceful fallback. Deploy with a CUDA/ROCm GPU (`full-ai` profile or native Ollama) for sub-3-second latency under concurrent load. |

---

## 2. Architectural & Operational Trade-offs

1. **At-Least-Once Outbox Delivery Semantics**:
   - The transactional outbox claims rows with a `PROCESSING` lease (`stale_timeout_seconds=300`) before invoking the external SMTP/Telegram transport and commits `PROVIDER_ACCEPTED` immediately afterward.
   - If the worker process crashes *after* the remote SMTP server accepts `DATA` but *before* the local database commits `PROVIDER_ACCEPTED`, the stale lease recovery mechanism will re-deliver the notification after 300 seconds. This is standard at-least-once delivery behavior and is explicitly tested in `test_e_crash_after_delivery_at_least_once_documentation`.
2. **Git History Divergence (`remediation/engineering-acceptance` vs. `origin/main`)**:
   - As documented in `RECOVERY_BASELINE.md`, the local branch history (`remediation/engineering-acceptance`) and `origin/main` do not share a common commit ancestor (`git merge-base` returns exit code 1) due to an earlier standalone repository initialization.
   - Per strict engineering safety rules, no destructive rebase, force-push, or automatic unrelated-history merge was performed. When integrating with `origin/main`, the repository owner should review the branch diff or open a compare PR according to their preferred release workflow.
3. **Rate Limiting Scope**:
   - Public endpoints (`POST /api/v1/requests/`, `POST /api/v1/feedbacks/`, `POST /api/v1/rag/ask`) enforce strict Pydantic input length/pattern validation, phone normalization, and pre-retrieval regex filters, but do not embed an in-process IP rate limiter because reverse-proxy rate limiting (Nginx / Cloudflare / Traefik) is standard in multi-worker deployments. Configure upstream rate limiting before exposing port `8000` directly to the public internet.
