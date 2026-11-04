# RENTKOM SMART ENTERPRISE — RAG GROUNDING, SAFETY & EVALUATION REPORT

**Audit Date:** 2026-11-04
**Branch:** `fix/rentkom-complete-remediation`
**Vector Engine:** Qdrant (`127.0.0.1:6333`, collection `rag_real_estate_active`, 32 deterministic UUIDv5 chunks across 6 documents)
**Inference Engine:** Ollama (`127.0.0.1:11434`, embedding `mxbai-embed-large:latest`, chat `qwen3:4b-instruct`)
**Execution Mode:** `live_inference` (fresh live run against active instances, no cache fallback)
**Verification Status:** `VERIFIED_WITH_DOCUMENTED_LIMITATIONS`

---

## 1. Remediation of RAG Grounding, Legal Boundaries & Prompt-Injection Defenses (`core/services/rag_service.py`)

1. **Strict Grounding Invariant (`AI-P1-01`)**:
   - Replaced legacy behavior where `query_rag()` returned hardcoded `mode="grounded"` and `confidence_score=0.85` even when Qdrant was offline or returned 0 qualifying chunks.
   - `mode="grounded"` is now returned **if and only if**:
     1. Pre-retrieval guardrails pass (not prompt injection, not out-of-domain).
     2. Embedding generation and Qdrant vector search succeed.
     3. At least one retrieved chunk has cosine similarity $\ge$ `RAG_MIN_SIMILARITY` (`0.55`).
     4. Ollama chat generation succeeds and does not emit an explicit abstention phrase.
   - If Qdrant or Ollama is unreachable, `query_rag()` returns `mode="fallback"` with `confidence_score=0.0` and `sources=[]`.
   - If retrieval returns 0 qualifying chunks above threshold, `query_rag()` returns `mode="rejected"` with `confidence_score=round(max_score, 4)` (or `0.0`), `sources=[]`, and `citations=[]`.
2. **Exact Retrieval Similarity Reporting**:
   - Removed hardcoded confidence values. `confidence_score` reflects the exact rounded maximum cosine similarity score (`round(max_score, 4)`) from Qdrant, `0.0` on guardrail abstention, or `0.0` on infrastructure fallback.
3. **Informational Consultant Role & Legal Disclaimer (`AI-P2-01`)**:
   - Replaced lawyer persona with an informational consultant persona (`"Ти — автоматизований інформаційний консультант агентства нерухомості «Рентком» (м. Львів)..."`) that explicitly states responses are informational only, based on internal agency documents, and do not constitute licensed legal representation.
4. **Structural Trust Boundary & Indirect Prompt Injection Defense**:
   - Added `sanitize_retrieved_chunk()` to strip adversarial instruction patterns (`ignore previous instructions`, `system prompt:`, `<|im_start|>`, `[INST]`, etc.) from retrieved document chunks before prompt assembly.
   - Wrapped retrieved chunks inside `<retrieved_evidence>...</retrieved_evidence>` XML delimiters with explicit system instructions to treat everything inside `<retrieved_evidence>` strictly as passive reference data and never as executable commands.
5. **Configurable Agency Contact Details**:
   - Extracted hardcoded phone numbers (`+38 (032) 240-00-00`) and emails into `settings.AGENCY_CONTACT_PHONE` and `settings.AGENCY_CONTACT_EMAIL` with dynamic interpolation in `get_abstention_response()`, `get_fallback_response()`, and system prompts.

---

## 2. Deterministic Knowledge Base Indexing (`tools/reindex_knowledge_base.py`)

Executed `python tools/reindex_knowledge_base.py` against local Qdrant (`127.0.0.1:6333`) and Ollama (`mxbai-embed-large:latest`):

| Document ID | Source File | Extracted Chars | Indexed Chunks |
|---|---|---:|---:|
| `doc-01-verification-guide` | `Керівництво_з_перевірки_документів.pdf` | 1,670 | 5 |
| `doc-02-services-description` | `Опис_послуг_агентства.docx` | 706 | 2 |
| `doc-03-buyer-memo` | `Памятка_для_покупця.pdf` | 1,999 | 7 |
| `doc-04-contract-signing-order` | `Порядок_підписання_договору.docx` | 864 | 3 |
| `doc-05-privacy-policy` | `Правила_конфіденційності.pdf` | 2,149 | 7 |
| `doc-06-lease-contract-template` | `Шаблон_договору_оренди.docx` | 2,302 | 8 |
| **Total** | **6 Official Agency Documents** | **9,690** | **32 deterministic points** |

Each point carries deterministic `uuid5(NAMESPACE_DNS, f"rentkom:{doc_id}:{idx}")` IDs and rich metadata (`document_id`, `document_name`, `chunk_index`, `source_format`, `content_hash`, `embedding_model`, `ingestion_version`).

---

## 3. Evaluation Cache Hardening & Defect Remediation (`tools/evaluate_rag_suite.py`)

1. **Default Live Execution**:
   - `reuse_existing_responses` is set to `False` by default in both `evaluate_dataset()` and the CLI runner. Every run executes fresh live inference against Qdrant and Ollama unless `--reuse-cache` is explicitly passed.
2. **Preservation of Full Model Responses & Citations**:
   - Full responses are stored in `full_answer` (and `answer`), while `answer_preview` is preserved strictly for display (first 220 characters).
   - Factual claim evaluation is strictly executed against complete, un-truncated answer text.
   - Returned `SourceCitation` objects are serialized and preserved in each item of `results`.
3. **Strict Citation-Support Requirement**:
   - Missing citation objects strictly result in `supported: False` (`"no_citations_returned"`).
   - Removed legacy fallback heuristic that substituted `source_hit and confidence_score >= 0.55` when citations were absent. Missing citation objects cannot produce a successful citation-support result.
4. **Cache Compatibility & Integrity Validation**:
   - Explicit cache reuse requires passing `validate_cache()`, which verifies:
     - Existence of top-level `evaluation_metadata`
     - Matching `dataset_hash` (deterministic sha256 fingerprint of questions and claims)
     - Matching model (`qwen3:4b-instruct`), embedding model (`mxbai-embed-large:latest`), and collection (`rag_real_estate_active`)
     - Presence of `full_answer` and actual `citations` list in all cached items.
   - Incompatible or legacy caches are strictly rejected with `IncompatibleCacheError`.
5. **Distinct Run Mode Labeling**:
   - Fresh runs are labeled `run_mode: "live_inference"`, `is_cached_run: False`.
   - Replayed runs are labeled `run_mode: "cached_replay"`, `is_cached_run: True`.
   - Per-item results carry `is_cached` and `run_mode` attributes.

---

## 4. Live 30-Question Benchmark Evaluation (`evaluation/rag_eval_results.json`)

Fresh live benchmark execution against active Qdrant + Ollama (`qwen3:4b-instruct`, `timestamp: 2026-11-04T12:39:33Z`):

### Measured Benchmark Metrics Summary

| Metric | Measured Value | Standard Target | Actual Engineering Finding |
|---|---:|---:|---|
| **Total Questions Evaluated** | `30` | $\ge 30$ | 20 in-domain, 5 out-of-domain, 5 adversarial injections |
| **Run Mode** | **`live_inference`** | Live | Fresh live execution against active local Qdrant and Ollama |
| **Retrieval Recall@K (Any Source)** | **`0.8500` (85.0%)** | $\ge 0.80$ | 17/20 in-domain queries retrieved at least one expected source in top-K |
| **Single-Document Retrieval Recall** | **`0.8667` (86.7%)** | $\ge 0.80$ | 13/15 single-document / paraphrase queries retrieved expected source |
| **Multi-Document Full Coverage** | **`0.2000` (20.0%)** | $\ge 0.50$ | 1/5 multi-doc queries retrieved ALL required sources (top-K=3 constraint) |
| **Overall Source Coverage Ratio** | **`0.7500` (75.0%)** | $\ge 0.70$ | Average fraction of required documents present in top-3 chunks |
| **Answer Keyword Coverage** | **`0.9500` (95.0%)** | $\ge 0.80$ | 19/20 in-domain queries contained expected domain keyword stems |
| **Factual Claim Accuracy (Heuristic Proxy)** | **`0.8000` (80.0%)** | $\ge 0.75$ | **16/20 in-domain queries** verified against reference claim anchors (Q05, Q08, Q15, Q22 failed) |
| **Citation Entailment Proxy** | **`0.7368` (73.68%)** | $\ge 0.75$ | 14/19 grounded queries have qualifying citations matching expected sources ($\ge 0.55$) |
| **Abstention Accuracy** | **`1.0000` (100.0%)** | `1.00` | 10/10 out-of-domain and adversarial prompts safely rejected |
| **Unnecessary Abstention Rate** | **`0.0500` (5.0%)** | $\le 0.10$ | 1/20 in-domain queries (Q22) abstained due to low similarity |
| **Unsupported Answer Rate** | **`0.1667` (16.67%)** | $\le 0.20$ | 5/30 total queries (Q11, Q13, Q14, Q15, Q23) lacked full qualifying citation coverage |
| **Median Latency (`p50`)** | `24.329 s` | Bounded | Local CPU generation (`qwen3:4b-instruct`); guardrail checks `< 0.001 s` |
| **95th Percentile Latency (`p95`)** | `34.489 s` | Bounded | Local CPU inference without GPU acceleration |

---

## 5. Detailed Investigation of Non-Perfect Cases

Forensic inspection of `evaluation/rag_eval_results.json` reveals the exact failure modes:

1. **`Q22` (Colloquial Paraphrase) — Low-Similarity Safe Abstention (`actual_mode=rejected`)**:
   - *Question:* "Винаймаю фатиру, як прописується комірне і стан меблів у договорі оренди?"
   - *Expected Mode:* `grounded` from `Шаблон_договору_оренди.docx`.
   - *Actual Mode:* `rejected` (confidence score 0.0, empty retrieval).
   - *Issue:* Colloquial term query rewriting did not produce a chunk matching above the strict `0.55` threshold. The system chose safety over hallucination and returned the standard abstention message.
2. **`Q14` & `Q23` (Multi-Doc / Colloquial) — Retrieval Misses (`source_hit=False`)**:
   - `Q14`: Expected `Правила_конфіденційності.pdf` and `Керівництво_з_перевірки_документів.pdf`. Neither appeared in top-K. The model generated an answer based on prompt context without qualifying citation support.
   - `Q23`: Question about seller not showing original documents. `Керівництво_з_перевірки_документів.pdf` was not in top-K. The model responded accurately using system prompt guidance, but without primary source citation.
3. **Multi-Document Incomplete Citation Coverage (`Q11`, `Q13`, `Q15`)**:
   - In 3-chunk retrieval, multi-document synthesis queries requiring 2-3 distinct source files frequently retrieve only 1 or 2 of them. While partial citations exist with score $\ge 0.55$, they do not cover all required documents, resulting in strict citation entailment failure.
4. **Factual Claim Misses (`claim_hit=False`: Q05, Q08, Q15, Q22)**:
   - Across the 20 grounded questions, exactly **16 were verified (80.0%)** against reference claim anchors, while 4 failed:
     - `Q05`: Order of signing answer covered pre-signing checks but omitted specific reference claims regarding power of attorney verification and acceptance act formalities.
     - `Q08`: Privacy answer gave general policy summary but omitted specific restricted access clauses.
     - `Q15`: Purchase vs. lease comparison omitted specific notarization and state registry statutory claims.
     - `Q22`: Abstained due to low similarity, hence 0 factual claims verified.
   - Resulting empirical **factual claim accuracy proxy is 0.8000 (80.0%)**, faithfully reported without artificial metric inflation.

---

## 6. Critical Methodological Review & Technical Limitations

1. **Cosine Similarity vs. Factual Grounding**:
   - A cosine similarity score $\ge 0.55$ proves topical relevance, not claim-level factual entailment. High similarity chunks may discuss real estate generally without answering the specific legal question.
2. **Document Filename vs. Claim Grounding**:
   - Retrieving the correct file does not guarantee the model will extract the correct clause. Evaluation must verify claim propositions directly against authoritative excerpts.
3. **Keyword Matching vs. Semantic Correctness**:
   - Keyword presence can be fooled by negation ("не можна передавати" vs "можна передавати") or missed by synonymy.
4. **Adversarial Security Boundaries**:
   - Passing 5 static prompt injection queries (`Q26`–`Q30`) demonstrates that XML delimiters (`<retrieved_evidence>`) and prompt sanitization work against direct instruction overriding.
   - **It does NOT constitute proof of universal security.** Advanced jailbreaks (multi-turn persuasion, base64 encoding, foreign-language smuggling, or adversarial token sequences) remain an inherent risk of LLM systems and require external firewall and gateway protections in production.
