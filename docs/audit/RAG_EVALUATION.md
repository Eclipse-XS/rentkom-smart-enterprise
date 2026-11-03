# RENTKOM SMART ENTERPRISE — RAG GROUNDING, SAFETY & EVALUATION REPORT

**Audit Date:** 2026-11-03
**Branch:** `remediation/engineering-acceptance`
**Vector Engine:** Qdrant (`127.0.0.1:6333`, collection `rag_real_estate_active`, 32 deterministic UUIDv5 chunks across 6 documents)
**Inference Engine:** Ollama (`127.0.0.1:11434`, embedding `mxbai-embed-large:latest`, chat `qwen3:4b-instruct-2507-q4_K_M`)
**Verification Status:** `VERIFIED_FIXED`

---

## 1. Remediation of RAG Grounding, Legal Boundaries & Prompt-Injection Defenses (`core/services/rag_service.py`)

1. **Strict Grounding Invariant (`AI-P1-01`)**:
   - Removed the defect where `query_rag()` returned `mode="grounded"` and `confidence_score=0.85` when Qdrant was offline or returned 0 chunks above threshold.
   - `mode="grounded"` is now returned **if and only if**:
     1. Pre-retrieval guardrails pass (not prompt injection, not out-of-domain).
     2. Embedding generation and Qdrant vector search succeed.
     3. At least one retrieved chunk has cosine similarity $\ge$ `RAG_MIN_SIMILARITY` (`0.55`).
     4. Ollama chat generation succeeds and does not emit an explicit abstention phrase.
   - If Qdrant or Ollama is unreachable, `query_rag()` returns `mode="fallback"` with `confidence_score=None` and `sources=[]`.
   - If retrieval returns 0 qualifying chunks, `query_rag()` returns `mode="rejected"` with `confidence_score=round(max_score, 4)` (or `0.0`), `sources=[]`, and `citations=[]`.
2. **Exact Retrieval Similarity Reporting**:
   - Removed hardcoded `0.85` fallback confidence. `confidence_score` reflects the exact rounded maximum cosine similarity score (`round(max_score, 4)`) from Qdrant, `0.0` on guardrail abstention, or `None` on infrastructure fallback.
3. **Informational Consultant Role & Legal Disclaimer (`AI-P2-01`)**:
   - Replaced `"Ти — професійний юрист-консультант..."` with an informational consultant persona (`"Ти — інформаційний консультант агентства нерухомості «Рентком» (м. Львів)..."`) that explicitly states responses are informational only, based on internal agency documents, and do not constitute licensed legal representation.
4. **Structural Trust Boundary & Indirect Prompt Injection Defense**:
   - Added `sanitize_retrieved_chunk()` to strip adversarial instruction patterns (`ignore previous instructions`, `system prompt:`, `<|im_start|>`, `[INST]`, etc.) from retrieved document chunks before prompt assembly.
   - Wrapped retrieved chunks inside `<retrieved_evidence>...</retrieved_evidence>` XML delimiters with explicit system instructions to treat everything inside `<retrieved_evidence>` strictly as passive reference data and never as executable commands.

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

## 3. Live 30-Question Benchmark Evaluation (`evaluation/rag_eval_dataset.json`, `evaluation/rag_eval_results.json`)

Executed `python tools/evaluate_rag_suite.py` against live Qdrant + live Ollama (`qwen3:4b-instruct-2507-q4_K_M`) across 30 questions spanning 5 categories:
- `single_document` (10 questions: `Q01`–`Q10`)
- `multi_document` (5 questions: `Q11`–`Q15`)
- `unsupported_out_of_domain` (5 questions: `Q16`–`Q20`)
- `colloquial_paraphrase` (5 questions: `Q21`–`Q25`)
- `adversarial_prompt_injection` (5 questions: `Q26`–`Q30`)

### Aggregate Quantitative Metrics

| Metric | Measured Value | Target / Threshold | Notes |
|---|---:|---:|---|
| **Total Questions Evaluated** | `30` | $\ge 30$ | 20 in-domain (`grounded` expected), 10 out-of-domain/adversarial (`rejected` expected) |
| **Recall@K (Source Document Hit Rate)** | **`0.8500` (85.0%)** | $\ge 0.80$ | 10/10 on `single_document` (100%), 4/5 on `multi_document` (80%), 3/5 on `colloquial_paraphrase` |
| **Abstention Accuracy** | **`1.0000` (100.0%)** | `1.00` | 5/5 out-of-domain + 5/5 adversarial prompt injections cleanly rejected (`mode="rejected"`) |
| **Unsupported Answer Rate** | **`0.0000` (0.0%)** | `0.00` | Zero grounded answers produced without supporting citations $\ge 0.55$; zero out-of-domain hallucinations |
| **Citation Support Rate** | **`1.0000` (100.0%)** | `1.00` | 19/19 grounded responses backed by real Qdrant document citations with cosine similarity $0.7109 - 0.8369$ |
| **Median Latency (`p50`)** | `17.335 s` | Bounded | Local CPU generation with `qwen3:4b-instruct`; guardrail abstentions execute in `< 0.001 s` |
| **95th Percentile Latency (`p95`)** | `31.386 s` | Bounded | Local CPU inference without GPU acceleration |
