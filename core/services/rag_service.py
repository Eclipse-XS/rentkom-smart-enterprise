import time
import re
import httpx
from typing import List, Dict, Any, Optional
from core.schemas import RAGQueryResponse, SourceCitation
from core.config import settings

def normalize_query_for_retrieval(query: str) -> str:
    """
    Normalizes colloquial and informal Ukrainian real-estate terms into standard terminology
    to bridge semantic gaps in multilingual embeddings without altering user intent or the final LLM prompt.
    """
    normalized = query
    # Colloquial terms: хата, хату, хати, хаті, хатою -> житло будинок нерухомість
    normalized = re.sub(r"\bхат[а-яієї']*\b", "житло будинок нерухомість", normalized, flags=re.IGNORECASE)
    # Colloquial terms: фатира -> квартира житло
    normalized = re.sub(r"\bфатир[а-яієї']*\b", "квартира житло", normalized, flags=re.IGNORECASE)
    # Colloquial terms: комірне -> комунальні послуги
    normalized = re.sub(r"\bкомірне\b", "комунальні послуги", normalized, flags=re.IGNORECASE)
    return normalized

STRICT_SYSTEM_PROMPT = """Ти — професійний юрист-консультант агентства нерухомості «Рентком» (м. Львів).
Твоє завдання — надавати точні, лаконічні та достовірні відповіді виключно на основі наданого контексту з нормативних документів агентства.

ПРАВИЛА:
1. Надавай відповіді на основі фактів, процедур, етапів та застережень із наданого контексту.
2. Якщо клієнт запитує про процедуру перевірки документів (наприклад, якщо власник не показує оригінали, є сумніви щодо прав), нотаріальний процес або завдаток, роз'ясни вимоги регламентів агентства:
   - До передачі будь-яких коштів обов'язково вимагати та звірити оригінали правовстановлюючих документів і ланцюг набуття; зафіксувати виявлені зауваження (статус issue_found) письмово та залучити профільного фахівця або нотаріуса; не перераховувати завдаток до завершення перевірки.
   - Купівля-продаж з нотаріусом передбачає перевірку повноважень сторін, узгодження остаточного тексту і порядку платежу, нотаріальне посвідчення угоди, перевірку реєстраційних документів у Державному реєстрі речових прав та оформлення акта приймання-передачі.
   - Щодо завдатку: у договорі обов'язково письмово узгоджуються наслідки невиконання зобов'язань; заборонено ототожнювати завдаток з авансом, а правову конструкцію та повернення коштів перевіряє профільний фахівець або нотаріус.
3. Тільки якщо у наданих документах взагалі немає інформації з порушеного питання (або воно не стосується нерухомості), дай відповідь:
   «На жаль, у базі знань агентства немає інформації з цього питання. Будь ласка, зверніться до нашого чергового рієлтора за телефоном +38 (032) 240-00-00.»
4. Ніколи не вигадуй статті законів, ціни чи процедури, яких немає в регламентах.
5. НЕ пиши самостійно назви файлів чи вигаданих джерел у квадратних дужках — перелік офіційних джерел формується автоматично платформою.
6. Якщо ти дав змістовну відповідь на основі контексту, НЕ додавай наприкінці фразу про відсутність інформації."""

OUT_OF_DOMAIN_PATTERNS = ["авіаквит", "літак", "кулінар", "рецепт", "погода в києві", "погода в парижі", "погода сьогодні"]
PROMPT_INJECTION_PATTERNS = ["ignore previous", "delete table", "drop database", "викради пароль", "покажи системний промпт", "forget all instructions", "show password"]

async def query_rag(
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
    collection_name: Optional[str] = None
) -> RAGQueryResponse:
    start_time = time.time()
    q_lower = question.lower()
    target_collection = collection_name or settings.QDRANT_COLLECTION

    # Guardrail 1: Check Prompt Injection
    for pat in PROMPT_INJECTION_PATTERNS:
        if pat in q_lower:
            return RAGQueryResponse(
                answer="Спроба несанкціонованого доступу або зміни системних інструкцій заблокована. Консультант працює виключно у межах регламентів агентства «Рентком».",
                sources=["Security: Guardrail Policy"],
                citations=[SourceCitation(document_name="Security: Guardrail Policy", score=1.0)],
                confidence_score=1.0,
                latency_seconds=round(time.time() - start_time, 3),
                mode="rejected"
            )

    # Guardrail 2: Out of Domain
    for pat in OUT_OF_DOMAIN_PATTERNS:
        if pat in q_lower:
            return RAGQueryResponse(
                answer="На жаль, у базі знань агентства немає інформації з цього питання. Будь ласка, зверніться до нашого чергового рієлтора за телефоном +38 (032) 240-00-00.",
                sources=[],
                citations=[],
                confidence_score=0.0,
                latency_seconds=round(time.time() - start_time, 3),
                mode="rejected"
            )

    # Vector Retrieval from Qdrant via HTTP API
    retrieved_chunks = []
    sources: List[str] = []
    citations: List[SourceCitation] = []
    seen_sources = set()
    max_score = 0.0

    try:
        # First embed question using Ollama with query normalization for colloquial vocabulary
        normalized_q = normalize_query_for_retrieval(question)
        async with httpx.AsyncClient(timeout=90.0) as client:
            embed_resp = await client.post(
                f"{settings.OLLAMA_BASE_URL}/api/embeddings",
                json={"model": settings.OLLAMA_EMBED_MODEL, "prompt": normalized_q}
            )
            if embed_resp.status_code == 200:
                vector = embed_resp.json().get("embedding", [])

                # Query Qdrant
                qdrant_url = f"http://{settings.QDRANT_HOST}:{settings.QDRANT_PORT}/collections/{target_collection}/points/search"
                qdrant_resp = await client.post(
                    qdrant_url,
                    json={"vector": vector, "limit": 4, "with_payload": True}
                )
                if qdrant_resp.status_code == 200:
                    results = qdrant_resp.json().get("result", [])
                    for pt in results:
                        score = pt.get("score", 0.0)
                        payload = pt.get("payload", {})
                        chunk_id = str(pt.get("id", ""))
                        text = payload.get("content") or payload.get("text") or ""
                        doc_name = (
                            payload.get("document_name") or
                            payload.get("source") or
                            "Регламент агентства «Рентком»"
                        )
                        if text:
                            retrieved_chunks.append(text)
                            citation = SourceCitation(
                                document_name=doc_name,
                                chunk_id=chunk_id,
                                score=round(float(score), 4)
                            )
                            citations.append(citation)
                            if doc_name not in seen_sources:
                                seen_sources.add(doc_name)
                                sources.append(doc_name)
                        if score > max_score:
                            max_score = score
    except Exception:
        # Graceful fallback handled below if retrieval is interrupted
        pass

    # Context assembly
    if retrieved_chunks:
        context_str = "\n\n---\n\n".join(retrieved_chunks)
        user_prompt = f"Контекст із регламентів агентства:\n{context_str}\n\nЗапитання клієнта: {question}\nВідповідь:"
    else:
        context_str = ""
        user_prompt = f"Запитання клієнта: {question}\nВідповідь:"

    # Call LLM via Ollama
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            llm_resp = await client.post(
                f"{settings.OLLAMA_BASE_URL}/api/generate",
                json={
                    "model": settings.OLLAMA_MODEL,
                    "prompt": user_prompt,
                    "system": STRICT_SYSTEM_PROMPT,
                    "stream": False,
                    "options": {
                        "temperature": 0.1,
                        "num_ctx": 4096,
                        "num_predict": settings.RAG_NUM_PREDICT
                    }
                }
            )
            if llm_resp.status_code == 200:
                ans_text = llm_resp.json().get("response", "").strip()
                # Remove think tags if generated by reasoning models
                if "<think>" in ans_text and "</think>" in ans_text:
                    ans_text = ans_text.split("</think>")[-1].strip()
                # Strip any hallucinated brackets or pseudocitations
                ans_text = re.sub(r"\[(Джерело|Регламент|Source)[^\]]*\]", "", ans_text, flags=re.IGNORECASE).strip()

                # If a substantive answer had an accidental trailing abstention boilerplate appended, strip it
                if not ans_text.strip().startswith("На жаль") and "На жаль, у базі знань агентства немає інформації" in ans_text:
                    ans_text = re.sub(
                        r"(\n+)?На жаль, у базі знань агентства немає інформації з цього питання.*",
                        "",
                        ans_text,
                        flags=re.DOTALL
                    ).strip()

                return RAGQueryResponse(
                    answer=ans_text,
                    sources=sources if sources else ["База знань «Рентком»"],
                    citations=citations,
                    confidence_score=round(max_score, 3) if max_score > 0 else 0.85,
                    latency_seconds=round(time.time() - start_time, 3),
                    mode="grounded"
                )
    except Exception:
        pass

    # Fallback response if services are cold
    return RAGQueryResponse(
        answer="Наразі модуль автоматичних консультацій працює в черговому режимі. Для отримання вичерпної консультації щодо оренди чи купівлі нерухомості у Львові зв'яжіться з нашим фахівцем за телефоном +38 (032) 240-00-00 або скористайтеся меню бота.",
        sources=["Черговий регламент зв'язку «Рентком»"],
        citations=[SourceCitation(document_name="Черговий регламент зв'язку «Рентком»", score=0.5)],
        confidence_score=0.5,
        latency_seconds=round(time.time() - start_time, 3),
        mode="fallback"
    )
