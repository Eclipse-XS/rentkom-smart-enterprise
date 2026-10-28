import time
import asyncio
import pytest
from fastapi.testclient import TestClient
from core.main import app
from core.services.telegram_idempotency import telegram_idempotency

@pytest.fixture(autouse=True)
def clear_idempotency_cache():
    asyncio.run(telegram_idempotency.clear_cache())
    yield
    asyncio.run(telegram_idempotency.clear_cache())

def test_telegram_sequential_duplicate_idempotency():
    client = TestClient(app)
    uid = int(time.time() * 10000) % 90000000 + 10000000
    payload = {
        "update_id": uid,
        "message": {"text": "/start", "chat": {"id": 1234567}}
    }

    # 1. First delivery
    resp1 = client.post("/api/v1/telegram/webhook-mock", json=payload)
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["route"] == "Start Menu"
    assert data1["is_duplicate"] is False

    # 2. Duplicate delivery with the SAME update_id
    resp2 = client.post("/api/v1/telegram/webhook-mock", json=payload)
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["route"] == "Start Menu"
    assert data2["is_duplicate"] is True
    assert data2["response_text"] == data1["response_text"]

@pytest.mark.anyio
async def test_telegram_concurrent_parallel_delivery_deduplication():
    # Simulate 5 parallel workers receiving the exact same webhook event concurrently
    payload = {
        "update_id": 999002,
        "message": {"text": "🏢 Процес купівлі/продажу", "chat": {"id": 7654321}}
    }
    event_key = telegram_idempotency.get_event_key(payload)

    execution_count = 0

    async def worker_simulate():
        nonlocal execution_count
        is_new, cached = await telegram_idempotency.acquire_or_get_cached(event_key)
        if is_new:
            # Simulate work
            await asyncio.sleep(0.05)
            execution_count += 1
            from core.services.telegram_idempotency import CachedResponse
            import time
            res = CachedResponse(
                route="Купівля/Продаж",
                response_text="Процес купівлі/продажу: 1...",
                processed_at=time.time(),
                is_duplicate=False
            )
            await telegram_idempotency.finish_processing(event_key, res)
            return res
        else:
            return cached

    # Launch 5 concurrent workers
    tasks = [worker_simulate() for _ in range(5)]
    results = await asyncio.gather(*tasks)

    # Exactly 1 worker must have executed the work
    assert execution_count == 1
    assert len(results) == 5

    # Exactly 1 result is original, 4 are marked as duplicate
    duplicates = [r.is_duplicate for r in results]
    assert duplicates.count(False) == 1
    assert duplicates.count(True) == 4
    for r in results:
        assert r.route == "Купівля/Продаж"
