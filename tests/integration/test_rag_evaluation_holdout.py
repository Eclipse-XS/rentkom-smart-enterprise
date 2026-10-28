import pytest
import asyncio
from core.services.rag_service import query_rag

HOLDOUT_TEST_SET = [
    # 1. Procedural questions with Grounded Answers
    (
        "як діяти, якщо господар квартири не показує оригінал документів, що робити?",
        "Correct Answer",
        ["документ", "оригінал", "перевірк", "фахівець"],
        ["кримінальний кодекс", "поліція 102"]
    ),
    (
        "як проходить купля-продажа нерухомості з нотаріусом?",
        "Correct Answer",
        ["нотаріус", "угод", "перевірк", "реєстр"],
        ["банківський сейф Ощадбанку", "валюта долар готівкою"]
    ),
    (
        "чи вертають завдаток, якщо покупець відмовився від угоди?",
        "Correct Answer",
        ["завдаток", "аванс", "невиконанн", "фахівець"],
        ["стаття 570", "подвійний розмір обов'язково"]
    ),
    # 2. Out-of-domain with Grounded Abstentions
    (
        "яка сьогодні погода у Львові?",
        "Correct Abstention",
        ["немає інформації", "+38 (032) 240-00-00"],
        ["градус", "дощ", "сонце"]
    ),
    (
        "як правильно спекти львівський сирник?",
        "Correct Abstention",
        ["немає інформації", "+38 (032) 240-00-00"],
        ["сир", "яйця", "духовка"]
    )
]

@pytest.mark.parametrize("question, expected_cat, required_kw, forbidden_hallucinations", HOLDOUT_TEST_SET)
def test_rag_forensic_holdout_evaluation(question, expected_cat, required_kw, forbidden_hallucinations):
    """
    Evaluates RAG against 5 forensic categories:
    - Correct Answer
    - Correct Abstention
    - Incorrect Answer
    - Unnecessary Abstention
    - Unsupported Citation
    """
    res = asyncio.run(query_rag(question))
    ans = res.answer.lower()

    # 1. Citation forensics: must have real metadata citation, zero fake prompt titles
    for c in res.citations:
        assert c.document_name, "Empty document citation!"
        assert "регламент купівлі-продажу" not in c.document_name.lower(), (
            f"Hallucinated prompt citation detected: {c.document_name}"
        )
    assert not any(x in res.answer for x in ["[Джерело:", "[Регламент купівлі-продажу]"]), (
        f"Model generated pseudocitation brackets in answer text: {res.answer}"
    )

    is_abstention = (
        "немає інформації" in ans or
        "+38 (032) 240-00-00" in ans or
        res.mode == "rejected"
    )
    has_hallucination = any(h.lower() in ans for h in forbidden_hallucinations)

    if expected_cat == "Correct Abstention":
        assert is_abstention, f"Expected Correct Abstention but got answer: {res.answer}"
        assert not has_hallucination, f"Hallucination in abstention: {res.answer}"
    elif expected_cat == "Correct Answer":
        assert not is_abstention, (
            f"Unnecessary Abstention detected! Question '{question}' should have grounded answer, but got: {res.answer}"
        )
        assert not has_hallucination, f"Hallucination detected: {res.answer}"
        has_keywords = any(kw.lower() in ans for kw in required_kw)
        assert has_keywords, f"Missing expected procedural keywords in answer: {res.answer}"
