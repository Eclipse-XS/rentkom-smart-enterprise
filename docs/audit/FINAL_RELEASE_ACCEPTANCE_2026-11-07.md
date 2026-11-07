# Фінальний release acceptance — 2026-11-07

## 1. Кореневі причини

| Зона | Дефект | Доказ | Ризик | Виправлення |
|---|---|---|---|---|
| RAG retrieval | Чотири chunks, relative floor на lexical-adjusted score та дублікати одного документа витісняли вторинні джерела | `core/services/rag_service.py`; initial live multi-doc coverage 0.40 | Неповні відповіді | K=20 candidates; document-level cosine window; round-robin до 6 chunks, максимум 2/document |
| RAG sessions | Спільний internal token і client `session_id`; hash приховував ID, але не встановлював власника | `core/api/v1/endpoints/rag.py` | Cross-user history access | Server-derived owner з Telegram user/chat/type; private mismatch і channel відхиляються; typing прив'язано до chat |
| Public n8n | Tunnel target був `127.0.0.1:5678` | `tools/manage_tunnel.py` | Публічний editor/admin API | Loopback allowlist ingress `:8765`; лише exact Telegram webhook; secret обов'язковий |
| Grafana/LR5 | Dashboard datasource залежав від unpublished LAB5 webhook | manifest, datasource і dashboard JSON | Непрацюючі CSAT panels | Monitoring bridge класифіковано як required internal datasource і опубліковано |

## 2. Зміни

- `core/services/rag_service.py`: diversity-aware retrieval без нового model/reranker.
- `core/api/v1/endpoints/rag.py`, `core/schemas.py`: Telegram ownership, group/private isolation, typing authorization, 24-hour lazy expiry та bounded cleanup збережено.
- `SMART_ENTERPRISE/automation/n8n/unified_telegram_router.json`: передача identity, отриманої з authoritative inbox guard.
- `tools/restricted_ingress.py`, `tools/manage_tunnel.py`: route allowlist, secret validation, safe supervisor lifecycle для двох tunnel providers.
- `config/automation_manifest.yaml`: фактична залежність Grafana від LAB5 bridge та правильний property path.
- Tests: ownership, forged identity, group isolation, legacy tunnel rejection, external route filtering.
- `evaluation/rag_eval_results.json`: свіжа live evaluation, без cache.

## 3. RAG до/після

Однакові dataset v2.0 (30 питань), collection `rag_real_estate_active` (32 points), `mxbai-embed-large`, `qwen3:4b-instruct`, threshold 0.55, live inference. До — перший контрольний запуск поточного checkout; після — фінальний запуск.

| Метрика | До | Після |
|---|---:|---:|
| Recall@K | 0.95 | 1.00 |
| Multi-document full coverage | 0.40 | 1.00 |
| Factual-claim heuristic proxy | 0.60 | 0.55 |
| Citation-support proxy | 0.8421 | 1.00 |
| Abstention accuracy | 1.00 | 1.00 |
| Unsupported-answer rate | 0.10 | 0.00 |
| p50 / p95 | 8.181 / 15.982 s | 8.456 / 14.027 s |

Retrieval acceptance пройдено. Factual-claim proxy регресував на 0.05 і залишається P2 limitation локального генератора; metric є anchor heuristic, не NLI або human factual review. Пер-question результати збережені в evaluation artifact.

## 4. Security verification

- Live FastAPI: два Telegram users з однаковим client session створили дві owner-bound rows; forged private user/chat mismatch — HTTP 400. Test rows видалені за точними keys.
- External localhost.run tunnel: `/`, `/rest/workflows`, `/signin`, unknown webhook — 404; approved webhook без secret — 403.
- Tunnel state: target `http://127.0.0.1:8765`; local n8n `:5678` лишився доступним локально.
- Telegram `getWebhookInfo`: URL збігається, pending updates 0, delivery error відсутній. Реальний inbound message користувача в цьому acceptance не надсилався.

## 5. System acceptance

- Controlled restart без видалення volumes: PostgreSQL, FastAPI, n8n, Qdrant, Grafana healthy; Ollama API 200.
- Persisted baseline збережено; Qdrant лишився 32 points. Smoke workflows додали очікувані synthetic records, тому business counts зросли на один request/outbox/inbox.
- Reconciliation: Unified Telegram, feedback, Forms/Gmail, LAB5 monitoring published; conflicting legacy Telegram/RAG workflows inactive.
- Google OAuth credentials були наявні; reconciliation повідомив Sheets/Gmail operational. Це connector/runtime evidence, не нова ручна real Google submission.
- LAB5 customer-satisfaction і property-popularity internal webhooks — HTTP 200.
- Grafana health — 200; dashboard/datasource static references узгоджено. Authenticated Grafana API inspection не виконано: persisted admin password невідомий.

## 6. Tests і CI

- Full local pytest: 244 PASS, 0 FAIL, 23 SKIPPED.
- Focused post-change security/contracts: 34 PASS.
- Compose profile validation: 4 PASS.
- System audit: EXTERNAL_READY.
- GitHub Actions: перевіряється після push; до green CI release не є остаточно прийнятим.

## 7. Залишкові ризики

- P1: відсутній автоматизований доказ реального inbound Telegram message після фінального tunnel rotation.
- P2: factual-claim heuristic 0.55; retrieval правильний, generation не завжди відтворює всі reference anchors.
- P2: 23 integration/e2e tests skipped через умовні external prerequisites; це не PASS.
- P2: Grafana authenticated API smoke не виконано через невідомий persisted admin password; health та backing endpoints перевірено.
- P2: n8n попереджає, що PostgreSQL 16 має compatibility support; поточний stack healthy.

## 8. Git

Branch: `remediation/architectural-restoration`. Final commit SHA, remote HEAD і CI URL фіксуються після commit/push, оскільки commit не може коректно містити власний SHA.

## 9. Оцінка зрілості

| Категорія | Бал |
|---|---:|
| Architecture and component coherence | 13/15 |
| PostgreSQL and business correctness | 14/15 |
| Integrations and orchestration | 12/15 |
| Security and authorization | 14/15 |
| RAG quality and reliability | 11/15 |
| Testing and reproducibility | 8/10 |
| Operations and observability | 8/10 |
| Telegram UX | 4/5 |
| **TOTAL** | **84/100** |

Academic readiness: так. Portfolio readiness: так, із зафіксованими limitations. Controlled demo readiness: так. Production readiness: ні — немає повної real-channel acceptance та factual generation quality недостатня для неконтрольованих юридично чутливих відповідей.
