import json
import os
from unittest.mock import AsyncMock, patch
import pytest

from core.schemas import RAGQueryResponse, SourceCitation
from tools.evaluate_rag_suite import (
    IncompatibleCacheError,
    compute_dataset_hash,
    evaluate_citation_entailment_proxy,
    evaluate_dataset,
    evaluate_retrieval,
    validate_cache,
    verify_factual_claim,
)


def test_claim_verifier_rejects_negations_and_contradictions():
    """
    Regression Test 1:
    Verifies that affirmative claims are strictly rejected when the answer contains
    contradictory negations (e.g. 'не надає', 'не встановлює', 'без перевірки'),
    even if the individual proposition words are present.
    """
    claim_spec = {
        "statement": "підбір нерухомості для купівлі та оренди",
        "source_fragment": "підбір об'єктів для купівлі або оренди",
        "required_anchors": ["купівл", "оренд"],
        "forbidden_negations": ["не надає", "не займається орендою", "не здійснює"],
    }

    # Case A: Explicit forbidden negation phrase
    contradictory_answer_a = "Наше агентство не надає послуг оренди або купівлі житла."
    res_a = verify_factual_claim(contradictory_answer_a, claim_spec)
    assert res_a["verified"] is False
    assert "forbidden_negation" in res_a["reason"]

    # Case B: Contextual negation pattern (e.g. 'не <verb> <anchor>')
    contradictory_answer_b = "Агентство не здійснює купівлю або оренду комерційних площ."
    res_b = verify_factual_claim(contradictory_answer_b, claim_spec)
    assert res_b["verified"] is False

    # Case C: Valid affirmative answer passes
    valid_answer = "Агентство «Рентком» здійснює професійний підбір об'єктів для купівлі та оренди."
    res_c = verify_factual_claim(valid_answer, claim_spec)
    assert res_c["verified"] is True
    assert res_c["reason"] == "all_anchors_and_context_verified"


def test_claim_verifier_rejects_accidental_word_matches():
    """
    Regression Test 2:
    Verifies that incidental mentions of isolated words (even length > 3) do NOT satisfy a factual claim.
    ALL required anchors of the proposition must co-occur.
    """
    claim_spec = {
        "statement": "визначено 6 етапів перевірки",
        "source_fragment": "встановлює 6 послідовних етапів",
        "required_anchors": ["6", "етап"],
        "forbidden_negations": ["не 6", "немає 6", "не встановлює"],
    }

    # Accidental match of "6" without "етап" (e.g. 6 місяців)
    accidental_answer_1 = "Договір оренди укладається строком на 6 місяців з можливістю пролонгації."
    res_1 = verify_factual_claim(accidental_answer_1, claim_spec)
    assert res_1["verified"] is False
    assert "missing_required_anchors" in res_1["reason"]
    assert "етап" in res_1["missing_anchors"]

    # Accidental match of "етап" without "6" (e.g. перший етап)
    accidental_answer_2 = "На першому етапі клієнт надає паспортні дані та ідентифікаційний код."
    res_2 = verify_factual_claim(accidental_answer_2, claim_spec)
    assert res_2["verified"] is False
    assert "6" in res_2["missing_anchors"]

    # Valid co-occurrence of both anchors satisfies the claim
    valid_answer = "У регламенті перевірки нерухомості визначено 6 послідовних етапів."
    res_valid = verify_factual_claim(valid_answer, claim_spec)
    assert res_valid["verified"] is True


def test_claim_verifier_measures_partial_correctness_accurately():
    """
    Regression Test 3:
    Verifies that multi-claim questions accurately report partial correctness rather than
    false binary (all-or-nothing or false 100%) scores.
    """
    claims = [
        {
            "statement": "визначено 6 етапів перевірки",
            "source_fragment": "встановлює 6 послідовних етапів",
            "required_anchors": ["6", "етап"],
            "forbidden_negations": ["не 6"],
        },
        {
            "statement": "етапи мають фіксовану тривалість до 30 днів",
            "source_fragment": "тривалість перевірки становить 30 днів",
            "required_anchors": ["триваліст", "30"],
            "forbidden_negations": [],
        },
    ]

    answer = "У регламенті агентства встановлено 6 етапів перевірки документів перед правочином."

    verifications = [verify_factual_claim(answer, c) for c in claims]
    verified_count = sum(1 for v in verifications if v["verified"])

    assert verified_count == 1
    assert len(claims) == 2
    fraction = verified_count / len(claims)
    assert fraction == 0.5  # 50% partial coverage, not 100%


def test_citation_entailment_proxy_rejects_incorrect_or_unrelated_citations():
    """
    Regression Test 4:
    Verifies that citations with similarity below grounding threshold (0.55) or pointing
    to source files outside expected ground-truth documents fail the entailment proxy check.
    """
    expected_sources = {"Керівництво_з_перевірки_документів.pdf", "Памятка_для_покупця.pdf"}

    # Case A: Low similarity score (< 0.55)
    citations_low_score = [
        {"document_name": "Керівництво_з_перевірки_документів.pdf", "score": 0.42}
    ]
    res_a = evaluate_citation_entailment_proxy(citations_low_score, expected_sources)
    assert res_a["supported"] is False
    assert "below_threshold" in res_a["reason"]

    # Case B: Document mismatch (citation from unrelated document)
    citations_wrong_doc = [
        {"document_name": "Шаблон_договору_оренди.docx", "score": 0.78}
    ]
    res_b = evaluate_citation_entailment_proxy(citations_wrong_doc, expected_sources)
    assert res_b["supported"] is False
    assert "citation_document_mismatch" in res_b["reason"]

    # Case C: Valid citation matching expected source and threshold >= 0.55
    citations_valid = [
        {"document_name": "Керівництво_з_перевірки_документів.pdf", "score": 0.7682}
    ]
    res_c = evaluate_citation_entailment_proxy(citations_valid, expected_sources)
    assert res_c["supported"] is True
    assert res_c["reason"] == "all_citations_meet_score_and_source_alignment"


def test_multi_document_retrieval_flags_missing_primary_document():
    """
    Regression Test 5:
    Verifies that for multi-document synthesis questions, retrieval is NOT marked as
    a complete hit if any required document is missing from top-K.
    """
    expected_sources = {
        "Керівництво_з_перевірки_документів.pdf",
        "Порядок_підписання_договору.docx",
        "Памятка_для_покупця.pdf",
    }

    # Case A: Missing 1 of 3 required documents (e.g. only 2 retrieved in top-3)
    actual_sources_incomplete = {
        "Порядок_підписання_договору.docx",
        "Памятка_для_покупця.pdf",
    }
    retrieval_multi = evaluate_retrieval("multi_document", expected_sources, actual_sources_incomplete)
    assert retrieval_multi["full_coverage"] is False
    assert retrieval_multi["source_hit"] is False
    assert retrieval_multi["missing_sources"] == ["Керівництво_з_перевірки_документів.pdf"]
    assert retrieval_multi["coverage_ratio"] == 0.6667

    # Case B: All 3 documents retrieved -> full coverage hit
    actual_sources_complete = expected_sources
    retrieval_complete = evaluate_retrieval("multi_document", expected_sources, actual_sources_complete)
    assert retrieval_complete["full_coverage"] is True
    assert retrieval_complete["source_hit"] is True
    assert retrieval_complete["missing_sources"] == []
    assert retrieval_complete["coverage_ratio"] == 1.0

    # Case C: For single_document question, retrieving any expected source is a source_hit
    single_expected = {"Керівництво_з_перевірки_документів.pdf"}
    retrieval_single = evaluate_retrieval("single_document", single_expected, actual_sources_incomplete)
    assert retrieval_single["source_hit"] is False


@pytest.fixture
def sample_dataset_items():
    return [
        {
            "id": "T01",
            "category": "single_document",
            "question": "Які етапи перевірки регламентовано?",
            "expected_mode": "grounded",
            "expected_sources": ["Керівництво_з_перевірки_документів.pdf"],
            "expected_keywords": ["етап", "6"],
            "expected_factual_claims": [
                {
                    "statement": "визначено 6 етапів",
                    "source_fragment": "встановлює 6 послідовних етапів",
                    "required_anchors": ["6", "етап"],
                    "forbidden_negations": ["не 6"],
                }
            ],
            "reference_evidence": "встановлює 6 послідовних етапів",
        }
    ]


@pytest.mark.anyio
async def test_default_evaluation_invokes_live_query_rag(tmp_path, sample_dataset_items):
    """
    Regression Test 6:
    Proves that default evaluation (reuse_existing_responses=False) always invokes live query_rag,
    even if an existing output file is present on disk.
    """
    ds_path = tmp_path / "dataset.json"
    ds_path.write_text(json.dumps(sample_dataset_items, ensure_ascii=False), encoding="utf-8")
    res_path = tmp_path / "results.json"
    res_path.write_text(json.dumps({"results": []}), encoding="utf-8")

    mock_resp = RAGQueryResponse(
        answer="Встановлено 6 послідовних етапів перевірки документів.",
        sources=["Керівництво_з_перевірки_документів.pdf"],
        citations=[SourceCitation(document_name="Керівництво_з_перевірки_документів.pdf", score=0.82)],
        confidence_score=0.82,
        latency_seconds=0.12,
        mode="grounded",
    )

    with patch("tools.evaluate_rag_suite.query_rag", new_callable=AsyncMock) as mock_query:
        mock_query.return_value = mock_resp
        summary = await evaluate_dataset(
            dataset_path=str(ds_path),
            results_path=str(res_path),
        )

        assert mock_query.call_count == 1
        assert summary["run_mode"] == "live_inference"
        assert summary["is_cached_run"] is False
        assert summary["evaluation_metadata"]["run_mode"] == "live_inference"
        assert summary["results"][0]["is_cached"] is False
        assert summary["results"][0]["run_mode"] == "live_inference"


@pytest.mark.anyio
async def test_500_char_answer_evaluated_in_full(tmp_path, sample_dataset_items):
    """
    Regression Test 7:
    Proves that a 500+ character answer is evaluated in full, and claims positioned
    beyond character 220 are NOT truncated or lost.
    """
    # Construct a 500+ character answer where the factual proposition is located at index 260
    prefix = "Загальна інформація про діяльність агентства нерухомості Рентком у Львові. " * 4  # ~300 chars
    factual_anchor = " Встановлено 6 послідовних етапів регламентної перевірки документів. "
    suffix = "Звертайтесь до чергового рієлтора для отримання повного юридичного супроводу правочину. " * 3
    full_500_answer = prefix + factual_anchor + suffix
    assert len(full_500_answer) >= 500

    claim = sample_dataset_items[0]["expected_factual_claims"][0]

    # If evaluated against the 220-character preview, the claim would fail
    preview_220 = full_500_answer[:220]
    preview_check = verify_factual_claim(preview_220, claim)
    assert preview_check["verified"] is False
    assert "6" in preview_check["missing_anchors"]

    # Evaluated against the full answer, the claim succeeds
    full_check = verify_factual_claim(full_500_answer, claim)
    assert full_check["verified"] is True
    assert full_check["matched_anchors"] == ["6", "етап"]

    # In evaluate_dataset, full_answer is preserved and evaluated
    ds_path = tmp_path / "dataset.json"
    ds_path.write_text(json.dumps(sample_dataset_items, ensure_ascii=False), encoding="utf-8")
    res_path = tmp_path / "results.json"

    mock_resp = RAGQueryResponse(
        answer=full_500_answer,
        sources=["Керівництво_з_перевірки_документів.pdf"],
        citations=[SourceCitation(document_name="Керівництво_з_перевірки_документів.pdf", score=0.88)],
        confidence_score=0.88,
        latency_seconds=0.15,
        mode="grounded",
    )

    with patch("tools.evaluate_rag_suite.query_rag", new_callable=AsyncMock) as mock_query:
        mock_query.return_value = mock_resp
        summary = await evaluate_dataset(
            dataset_path=str(ds_path),
            results_path=str(res_path),
        )

        res_item = summary["results"][0]
        assert len(res_item["full_answer"]) >= 500
        assert len(res_item["answer_preview"]) <= 220
        assert res_item["claim_hit"] is True
        assert len(res_item["verified_claims"]) == 1


@pytest.mark.anyio
async def test_missing_citation_objects_cannot_produce_successful_citation_support(tmp_path, sample_dataset_items):
    """
    Regression Test 8:
    Proves that missing citation objects CANNOT produce a successful citation-support result,
    even when source_hit is True and confidence_score is high (e.g. 0.95).
    """
    expected_sources = {"Керівництво_з_перевірки_документів.pdf"}

    # Unit level: empty citations strictly fail
    unit_res = evaluate_citation_entailment_proxy([], expected_sources)
    assert unit_res["supported"] is False
    assert unit_res["reason"] == "no_citations_returned"

    # Dataset evaluation level: no fallback to similarity heuristic
    ds_path = tmp_path / "dataset.json"
    ds_path.write_text(json.dumps(sample_dataset_items, ensure_ascii=False), encoding="utf-8")
    res_path = tmp_path / "results.json"

    # High confidence and matching sources, but NO citation objects
    mock_resp = RAGQueryResponse(
        answer="Встановлено 6 послідовних етапів перевірки.",
        sources=["Керівництво_з_перевірки_документів.pdf"],
        citations=[],  # Empty citations
        confidence_score=0.95,
        latency_seconds=0.1,
        mode="grounded",
    )

    with patch("tools.evaluate_rag_suite.query_rag", new_callable=AsyncMock) as mock_query:
        mock_query.return_value = mock_resp
        summary = await evaluate_dataset(
            dataset_path=str(ds_path),
            results_path=str(res_path),
        )

        assert summary["citation_support_rate"] == 0.0
        assert summary["citation_entailment_proxy"] == 0.0
        res_item = summary["results"][0]
        assert res_item["citation_entailment_proxy"] is False
        assert "citation_entailment_failure" in str(res_item["failure_reason"])


@pytest.mark.anyio
async def test_old_or_incompatible_cache_is_rejected(tmp_path, sample_dataset_items):
    """
    Regression Test 9:
    Proves that old or incompatible cache files are strictly rejected:
    1. Missing evaluation metadata
    2. Mismatched dataset hash
    3. Mismatched model
    4. Missing full_answer field (preview only)
    5. Missing citations field
    """
    ds_path = tmp_path / "dataset.json"
    ds_path.write_text(json.dumps(sample_dataset_items, ensure_ascii=False), encoding="utf-8")
    expected_hash = compute_dataset_hash(sample_dataset_items)

    # 1. Missing evaluation metadata entirely
    legacy_cache = {"total_questions": 1, "results": [{"id": "T01", "answer_preview": "test"}]}
    is_valid, reason = validate_cache(legacy_cache, sample_dataset_items)
    assert is_valid is False
    assert reason == "missing_evaluation_metadata"

    res_path_legacy = tmp_path / "legacy.json"
    res_path_legacy.write_text(json.dumps(legacy_cache), encoding="utf-8")
    with pytest.raises(IncompatibleCacheError, match="missing_evaluation_metadata"):
        await evaluate_dataset(
            dataset_path=str(ds_path),
            results_path=str(res_path_legacy),
            reuse_existing_responses=True,
        )

    # 2. Mismatched dataset hash
    mismatched_hash_cache = {
        "evaluation_metadata": {
            "dataset_hash": "different_hash_1234",
            "model": "qwen3:4b-instruct",
            "embed_model": "mxbai-embed-large:latest",
            "collection_name": "rag_real_estate_active",
        },
        "results": [{"id": "T01", "question": sample_dataset_items[0]["question"], "full_answer": "ans", "citations": []}],
    }
    is_valid, reason = validate_cache(mismatched_hash_cache, sample_dataset_items)
    assert is_valid is False
    assert "dataset_hash_mismatch" in reason

    # 3. Mismatched model
    mismatched_model_cache = {
        "evaluation_metadata": {
            "dataset_hash": expected_hash,
            "model": "mistral-7b-outdated",
            "embed_model": "mxbai-embed-large:latest",
            "collection_name": "rag_real_estate_active",
        },
        "results": [{"id": "T01", "question": sample_dataset_items[0]["question"], "full_answer": "ans", "citations": []}],
    }
    is_valid, reason = validate_cache(mismatched_model_cache, sample_dataset_items)
    assert is_valid is False
    assert "model_mismatch" in reason

    # 4. Truncated answer only (missing full_answer)
    truncated_only_cache = {
        "evaluation_metadata": {
            "dataset_hash": expected_hash,
            "model": "qwen3:4b-instruct",
            "embed_model": "mxbai-embed-large:latest",
            "collection_name": "rag_real_estate_active",
        },
        "results": [
            {
                "id": "T01",
                "question": sample_dataset_items[0]["question"],
                "actual_mode": "grounded",
                "answer_preview": "Truncated preview only",
                "citations": [],
            }
        ],
    }
    is_valid, reason = validate_cache(truncated_only_cache, sample_dataset_items)
    assert is_valid is False
    assert "missing_full_answer" in reason

    # 5. Missing citations field
    missing_citations_cache = {
        "evaluation_metadata": {
            "dataset_hash": expected_hash,
            "model": "qwen3:4b-instruct",
            "embed_model": "mxbai-embed-large:latest",
            "collection_name": "rag_real_estate_active",
        },
        "results": [
            {
                "id": "T01",
                "question": sample_dataset_items[0]["question"],
                "actual_mode": "grounded",
                "full_answer": "Complete valid answer text",
            }
        ],
    }
    is_valid, reason = validate_cache(missing_citations_cache, sample_dataset_items)
    assert is_valid is False
    assert "missing_citations_field" in reason


@pytest.mark.anyio
async def test_explicit_valid_cache_reuse_works_correctly(tmp_path, sample_dataset_items):
    """
    Regression Test 10:
    Proves that when explicit valid cache reuse is requested, compatible responses
    are loaded without invoking live query_rag, and full answers/citations are evaluated.
    """
    ds_path = tmp_path / "dataset.json"
    ds_path.write_text(json.dumps(sample_dataset_items, ensure_ascii=False), encoding="utf-8")
    expected_hash = compute_dataset_hash(sample_dataset_items)

    valid_cache = {
        "total_questions": 1,
        "run_mode": "cached_replay",
        "is_cached_run": True,
        "evaluation_metadata": {
            "run_mode": "cached_replay",
            "is_cached_run": True,
            "dataset_hash": expected_hash,
            "model": "qwen3:4b-instruct",
            "embed_model": "mxbai-embed-large:latest",
            "collection_name": "rag_real_estate_active",
        },
        "results": [
            {
                "id": "T01",
                "category": "single_document",
                "question": sample_dataset_items[0]["question"],
                "actual_mode": "grounded",
                "confidence_score": 0.84,
                "latency_seconds": 0.22,
                "actual_sources": ["Керівництво_з_перевірки_документів.pdf"],
                "full_answer": "У регламенті встановлено 6 послідовних етапів перевірки.",
                "answer_preview": "У регламенті встановлено 6 послідовних етапів перевірки.",
                "citations": [
                    {
                        "document_name": "Керівництво_з_перевірки_документів.pdf",
                        "chunk_id": "c1",
                        "score": 0.84,
                    }
                ],
            }
        ],
    }
    res_path = tmp_path / "valid_cache.json"
    res_path.write_text(json.dumps(valid_cache, ensure_ascii=False), encoding="utf-8")

    with patch("tools.evaluate_rag_suite.query_rag", new_callable=AsyncMock) as mock_query:
        summary = await evaluate_dataset(
            dataset_path=str(ds_path),
            results_path=str(res_path),
            reuse_existing_responses=True,
        )

        assert mock_query.call_count == 0  # Reused from cache, no live inference
        assert summary["run_mode"] == "cached_replay"
        assert summary["is_cached_run"] is True
        assert summary["results"][0]["claim_hit"] is True
        assert summary["results"][0]["citation_entailment_proxy"] is True


@pytest.mark.anyio
async def test_fresh_and_cached_runs_are_labeled_separately(tmp_path, sample_dataset_items):
    """
    Regression Test 11:
    Proves that fresh and cached runs are labeled distinctly in both top-level metadata
    and per-item result entries.
    """
    ds_path = tmp_path / "dataset.json"
    ds_path.write_text(json.dumps(sample_dataset_items, ensure_ascii=False), encoding="utf-8")
    expected_hash = compute_dataset_hash(sample_dataset_items)

    mock_resp = RAGQueryResponse(
        answer="Встановлено 6 послідовних етапів перевірки.",
        sources=["Керівництво_з_перевірки_документів.pdf"],
        citations=[SourceCitation(document_name="Керівництво_з_перевірки_документів.pdf", score=0.85)],
        confidence_score=0.85,
        latency_seconds=0.1,
        mode="grounded",
    )

    res_fresh_path = tmp_path / "fresh_results.json"
    with patch("tools.evaluate_rag_suite.query_rag", new_callable=AsyncMock) as mock_query:
        mock_query.return_value = mock_resp
        fresh_summary = await evaluate_dataset(
            dataset_path=str(ds_path),
            results_path=str(res_fresh_path),
            reuse_existing_responses=False,
        )

    # Validate fresh labels
    assert fresh_summary["run_mode"] == "live_inference"
    assert fresh_summary["is_cached_run"] is False
    assert fresh_summary["evaluation_metadata"]["run_mode"] == "live_inference"
    assert fresh_summary["evaluation_metadata"]["is_cached_run"] is False
    assert fresh_summary["results"][0]["is_cached"] is False
    assert fresh_summary["results"][0]["run_mode"] == "live_inference"

    # Now replay the saved fresh results via explicit cache reuse
    res_cached_path = tmp_path / "cached_replay.json"
    with patch("tools.evaluate_rag_suite.query_rag", new_callable=AsyncMock) as mock_query:
        cached_summary = await evaluate_dataset(
            dataset_path=str(ds_path),
            results_path=str(res_fresh_path),
            reuse_existing_responses=True,
        )

    # Validate cached replay labels
    assert cached_summary["run_mode"] == "cached_replay"
    assert cached_summary["is_cached_run"] is True
    assert cached_summary["evaluation_metadata"]["run_mode"] == "cached_replay"
    assert cached_summary["evaluation_metadata"]["is_cached_run"] is True
    assert cached_summary["results"][0]["is_cached"] is True
    assert cached_summary["results"][0]["run_mode"] == "cached_replay"
