import asyncio
import json
import os
import statistics
import sys
import time
from typing import Dict, Any, List

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from core.services.rag_service import query_rag

DATASET_PATH = "evaluation/rag_eval_dataset.json"
RESULTS_PATH = "evaluation/rag_eval_results.json"


async def evaluate_dataset() -> Dict[str, Any]:
    with open(DATASET_PATH, "r", encoding="utf-8") as f:
        dataset: List[Dict[str, Any]] = json.load(f)

    results = []
    latencies = []
    grounded_latencies = []

    retrieval_hits = 0
    retrieval_total = 0
    keyword_hits = 0
    unnecessary_abstentions = 0
    correct_abstentions = 0
    abstention_total = 0
    unsupported_answers = 0
    citation_supported = 0
    grounded_total = 0

    for item in dataset:
        qid = item["id"]
        category = item["category"]
        question = item["question"]
        expected_mode = item["expected_mode"]
        expected_sources = set(item.get("expected_sources", []))
        expected_keywords = [kw.lower() for kw in item.get("expected_keywords", [])]

        t0 = time.time()
        res = await query_rag(question)
        elapsed = round(time.time() - t0, 3)
        latencies.append(elapsed)

        actual_sources = set(res.sources or [])
        source_hit = bool(expected_sources & actual_sources) if expected_sources else (len(actual_sources) == 0)
        ans_lower = (res.answer or "").lower()
        matched_keywords = [kw for kw in expected_keywords if kw in ans_lower]
        keyword_hit = len(matched_keywords) > 0 if expected_keywords else True

        if expected_mode == "grounded":
            retrieval_total += 1
            if source_hit:
                retrieval_hits += 1
            if res.mode == "grounded":
                grounded_total += 1
                grounded_latencies.append(elapsed)
                if keyword_hit:
                    keyword_hits += 1
                if len(res.citations) > 0 and source_hit and all(c.score >= 0.55 for c in res.citations):
                    citation_supported += 1
                else:
                    unsupported_answers += 1
            else:
                unnecessary_abstentions += 1
        elif expected_mode == "rejected":
            abstention_total += 1
            if res.mode == "rejected" and keyword_hit:
                correct_abstentions += 1
            elif res.mode == "grounded":
                unsupported_answers += 1

        results.append({
            "id": qid,
            "category": category,
            "question": question,
            "expected_mode": expected_mode,
            "actual_mode": res.mode,
            "confidence_score": res.confidence_score,
            "latency_seconds": elapsed,
            "expected_sources": sorted(list(expected_sources)),
            "actual_sources": sorted(list(actual_sources)),
            "source_hit": source_hit,
            "expected_keywords": expected_keywords,
            "matched_keywords": matched_keywords,
            "keyword_hit": keyword_hit,
            "answer_preview": res.answer[:220],
        })
        print(
            f"[{qid}] ({category}) mode={res.mode} score={res.confidence_score} "
            f"latency={elapsed}s src_hit={source_hit} kw_hit={keyword_hit}"
        )

    sorted_lat = sorted(latencies)
    p50 = round(statistics.median(sorted_lat), 3) if sorted_lat else 0.0
    p95_idx = min(len(sorted_lat) - 1, int(len(sorted_lat) * 0.95)) if sorted_lat else 0
    p95 = round(sorted_lat[p95_idx], 3) if sorted_lat else 0.0

    summary = {
        "total_questions": len(dataset),
        "recall_at_k": round(retrieval_hits / retrieval_total, 4) if retrieval_total else 0.0,
        "answer_keyword_coverage": round(keyword_hits / retrieval_total, 4) if retrieval_total else 0.0,
        "unnecessary_abstention_rate": round(unnecessary_abstentions / retrieval_total, 4) if retrieval_total else 0.0,
        "abstention_accuracy": round(correct_abstentions / abstention_total, 4) if abstention_total else 0.0,
        "unsupported_answer_rate": round(unsupported_answers / len(dataset), 4) if dataset else 0.0,
        "citation_support_rate": round(citation_supported / grounded_total, 4) if grounded_total else 0.0,
        "p50_latency_seconds": p50,
        "p95_latency_seconds": p95,
        "results": results,
    }

    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("\n=== RAG EVALUATION SUMMARY ===")
    for k, v in summary.items():
        if k != "results":
            print(f"  {k}: {v}")
    return summary


if __name__ == "__main__":
    asyncio.run(evaluate_dataset())
