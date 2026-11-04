import pytest
from tools.evaluate_rag_suite import (
    verify_factual_claim,
    evaluate_retrieval,
    evaluate_citation_entailment_proxy,
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
    assert retrieval_single["source_hit"] is False  # wasn't in actual_sources_incomplete
