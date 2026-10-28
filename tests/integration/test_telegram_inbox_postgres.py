import pytest
import time
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from core.main import app
from core.database import get_db, Base
from core.services.telegram_idempotency import telegram_idempotency

POSTGRES_URL = "postgresql+psycopg2://postgres@127.0.0.1:5433/postgres"

@pytest.fixture(scope="module")
def pg_session():
    engine = create_engine(POSTGRES_URL)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    session = Session()
    yield session
    session.close()
    app.dependency_overrides.clear()
    engine.dispose()

def test_telegram_inbox_persists_across_restart(pg_session):
    """
    GATE 2: Proves that telegram_inbox in PostgreSQL retains completed state across server restarts.
    """
    client = TestClient(app)
    update_id = int(time.time() * 1000 % 10000000)
    payload = {
        "update_id": update_id,
        "message": {"text": "/start", "chat": {"id": 112233}}
    }

    # 1. First execution
    resp1 = client.post("/api/v1/telegram/webhook", json=payload)
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["route"] == "Start Menu"
    assert data1["is_duplicate"] is False

    # Check DB record
    row = pg_session.execute(text("""
        SELECT status, response_payload, retry_count
        FROM public.telegram_inbox
        WHERE update_id = :uid;
    """), {"uid": update_id}).fetchone()
    assert row is not None
    assert row[0] == "COMPLETED"
    assert row[1]["route"] == "Start Menu"

    # 2. Simulate server crash / restart by wiping in-memory cache
    telegram_idempotency._in_memory_cache.clear()
    telegram_idempotency._in_progress.clear()

    # 3. Duplicate delivery after restart
    resp2 = client.post("/api/v1/telegram/webhook", json=payload)
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["is_duplicate"] is True
    assert data2["route"] == "Start Menu"
    assert data2["response_text"] == data1["response_text"]

def test_telegram_inbox_lease_recovery_after_worker_crash(pg_session):
    """
    GATE 2: Proves that an update stuck in PROCESSING recovers after lease timeout (30s).
    """
    client = TestClient(app)
    crashed_update_id = int(time.time() * 1000 % 10000000) + 1

    # Insert an abandoned lease (crashed 45s ago)
    pg_session.execute(text("""
        INSERT INTO public.telegram_inbox (
            bot_id, update_id, chat_id, message_text, status, locked_at, retry_count, created_at
        ) VALUES (
            'rentkom_bot', :uid, 999888, '🏢 Процес купівлі/продажу', 'PROCESSING', NOW() - INTERVAL '45 seconds', 0, NOW() - INTERVAL '45 seconds'
        );
    """), {"uid": crashed_update_id})
    pg_session.commit()

    # Worker arrives for retried webhook delivery: should reclaim lease and finish
    telegram_idempotency._in_memory_cache.clear()
    payload = {
        "update_id": crashed_update_id,
        "message": {"text": "🏢 Процес купівлі/продажу", "chat": {"id": 999888}}
    }
    resp = client.post("/api/v1/telegram/webhook", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["route"] == "Купівля/Продаж"

    # Verify DB status transitioned to COMPLETED and retry_count incremented
    row = pg_session.execute(text("""
        SELECT status, retry_count, completed_at
        FROM public.telegram_inbox
        WHERE update_id = :uid;
    """), {"uid": crashed_update_id}).fetchone()
    assert row[0] == "COMPLETED"
    assert row[1] >= 1
    assert row[2] is not None
