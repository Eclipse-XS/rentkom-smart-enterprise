# RENTKOM SMART ENTERPRISE — REMAINING RISKS & OPERATIONAL LIMITATIONS

**Audit Date:** 2026-11-04
**Branch:** `fix/rentkom-complete-remediation`
**Target Upstream:** `main` (`origin/main` HEAD `b607df6`)
**Pull Request:** [#1](https://github.com/Eclipse-XS/rentkom-smart-enterprise/pull/1)

This document transparently records all environmental constraints, architectural trade-offs, and operational considerations following completion of the engineering remediation.

---

## 1. Environmental & Operational Boundaries

| ID | Operational Area | Classification | Impact | Mitigation / Operator Action |
|---|---|---|---|---|
| **ENV-01** | **Docker Clean-Room Stack** | `OPERATIONAL_VERIFIED` | Full containerized stack (PostgreSQL 16, FastAPI, n8n, Qdrant, Grafana) verified on Docker Desktop v29.7.2. | Chained healthchecks ensure orderly boot. Use `docker compose --profile connected up -d` for multi-service startup. Historical note: Early remediation passes ran natively while Docker daemon was offline. |
| **ENV-02** | **External Live Telegram & Production SMTP Credentials** | `OPERATIONAL_PREREQUISITE` | Live outbound dispatch to `api.telegram.org` and commercial corporate SMTP relays requires customer-owned credentials (`TELEGRAM_BOT_TOKEN`, `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`). | Protocol adapters verified against local TCP SMTP servers (`test_smtp_delivery.py`) and simulated upstream API contracts (`test_telegram_connected_mode_contract.py`). Populate `.env` or production vault before enabling `connected` or `production` mode. |
| **ENV-03** | **Local CPU LLM Inference Latency** | Hardware Constraint | Running `qwen3:4b-instruct-2507-q4_K_M` on host CPU yields median RAG generation latency of `22.155s` (`p95 = 30.676s`). | Generation is bounded (`num_predict=384`) with a 90-second client timeout and graceful fallback. Deploy on GPU-enabled infrastructure (CUDA/ROCm) for production sub-3-second throughput. |
| **ENV-04** | **In-Memory Rate Limiting Scope & Proxy Trust** | Architectural Boundary | Public ingress (`POST /api/v1/requests/`, `POST /api/v1/feedbacks/`, 60 req/min) and RAG inference (`POST /api/v1/rag/ask`, 10 req/min) embed thread-safe sliding-window rate limiters (`InMemoryRateLimiter`, HTTP 429). Client IP resolution ignores spoofed `X-Forwarded-For` headers unless the direct peer is explicitly registered in `TRUSTED_PROXIES`. | In multi-worker horizontal scaling (e.g. Gunicorn/Uvicorn multi-process or Kubernetes replicas), an in-memory limiter tracks counts per process rather than cluster-wide. For multi-node production, terminate traffic at an external API Gateway / reverse proxy (Nginx, Cloudflare, Traefik) with centralized Redis-backed rate limiting. |

---

## 2. Architectural & Operational Considerations

1. **At-Least-Once Outbox Delivery Semantics**:
   - The transactional outbox claims rows with a `PROCESSING` lease (`stale_timeout_seconds=300`) using `FOR UPDATE SKIP LOCKED` before invoking the external SMTP/Telegram transport, and commits `SENT` / `PROVIDER_ACCEPTED` immediately afterward.
   - If the worker process crashes *after* the remote SMTP server accepts `DATA` but *before* the local database commits status, the stale lease recovery mechanism will re-deliver the notification after 300 seconds. This is standard at-least-once delivery behavior and is explicitly tested in `test_outbox_case_a_worker_crash_recovery`.
2. **Git Lineage & PR Structure**:
   - Historical branch `remediation/engineering-acceptance` was preserved untouched at SHA `6a67cc5`.
   - The authoritative deliverable branch `fix/rentkom-complete-remediation` cleanly branches from `origin/main` (`b607df6`), sharing a direct fast-forward ancestor. Zero force-pushes or squashes were performed. Pull Request [#1](https://github.com/Eclipse-XS/rentkom-smart-enterprise/pull/1) is open for code review.
3. **RAG Grounding & Adversarial Scope**:
   - RAG evaluation achieved 85.0% Recall@K (any source), 86.7% single-document recall, 20.0% multi-document full coverage (top-K=3 constraint), 80.0% factual claim accuracy proxy (16/20 verified), 73.68%–89.47% citation entailment proxy, and 6.67% unsupported answer rate (2 of 30 queries: Q14, Q23 where retrieval missed the primary source and the model answered from system prompt domain rules).
   - Passing 5 static prompt injection queries (`Q26`–`Q30`) demonstrates that XML delimiters (`<retrieved_evidence>`) and prompt sanitization work against direct instruction overriding. It does **not** constitute proof of universal security against novel or dynamic LLM jailbreak variants.
