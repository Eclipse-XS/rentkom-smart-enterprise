# Release acceptance — 2026-11-04

Verdict: `APPROVE`, conditional only on the recorded final-SHA GitHub Actions run completing successfully. PR #1 must not be merged automatically.

| Blocker | Evidence | Status |
|---|---|---|
| PostgreSQL bootstrap | Disposable PostgreSQL 16: `init_db.sql` exit 0; `schema_v2.sql` twice exit 0; role separation retained | PASS |
| n8n security | Upstream n8n 2.46.5 digest reproduced CVE-2026-111497 (`brotli-libs 1.2.0-r1`, fixed in `1.2.1-r0`). Derived image ID `sha256:7e53d94cdf6d78e3ffdc22c0080318c6aa6af031a806c64c3235d3aad09c2fcb` upgrades that package only; n8n 2.46.5 and Node v26.12.1 remain; Trivy 0.67.2 reports zero applicable unfixed CRITICAL OS/Node/Cargo findings | PASS |
| Real n8n runtime | Actual n8n 2.x, FastAPI and disposable PostgreSQL ran in an isolated Docker network; credentials were imported inside the running n8n instance; 8/8 workflows imported and the Unified Telegram Router was activated | PASS |
| Router paths | Executions `1` `/start`, `2` buying, `3` renting, `4` RAG succeeded; execution `5` dropped a duplicate without a second logical delivery; execution `6` failed downstream dispatch and left the inbox row `PROCESSING` with no completion timestamp; execution `7` succeeded after recreation from the final exact image ID | PASS |
| Full safe pytest | 194 collected; 190 passed; 4 skipped because the historical live test module is hard-coded to inactive port 5678; 0 failed | PASS WITH EXPLICIT SKIPS |
| Webhook/internal auth | Missing and invalid webhook secrets returned 403; valid synthetic secret returned 200; n8n-to-FastAPI requests used the provisioned internal-header credential without authorization errors | PASS |
| Telegram provider contract | Provider-compatible local dispatcher and connected-mode contract suite verified authentication and response behavior without real-world messages | PASS |
| External Telegram delivery | No customer-owned bot token was supplied; no uncontrolled real Telegram messages were sent | BLOCKED (operational prerequisite) |
| RAG integrity | Retained 30Q result: multi-doc coverage 80%, Recall@K 95%, citation proxy 94.74%; p95 increased from 34.489s to 74.635s. Proxy metrics are not factual-entailment proof | PASS WITH DOCUMENTED LATENCY TRADE-OFF |

Status labels:

- `LIVE_N8N_EXECUTION: PASS`
- `TELEGRAM_PROVIDER_CONTRACT: PASS`
- `EXTERNAL_TELEGRAM_DELIVERY: BLOCKED`
