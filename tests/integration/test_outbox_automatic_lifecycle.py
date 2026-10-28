import pytest
import time
from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from core.main import app
from core.database import Base, get_db
from core.models import ServiceRequest, NotificationOutbox, NotificationAttempt
from core.config import settings

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

def test_outbox_automatic_delivery_lifecycle(pg_session):
    """
    Proves GATE 6: Automated Outbox Worker Lifecycle.
    1. Service request submitted creates client + admin outbox items in PENDING.
    2. Background worker processes items and transitions to SENT.
    3. Verifies non-duplication and attempt logging.
    """
    from core.services.outbox_service import process_outbox

    client = TestClient(app)

    unique_suffix = int(time.time() * 1000 % 10000000)
    req_payload = {
        "full_name": f"Автоматичний Тест {unique_suffix}",
        "phone": f"+38067{unique_suffix % 9000000 + 1000000}",
        "email": f"auto_{unique_suffix}@rentkom.lviv.ua",
        "request_purpose": "Хочу купити",
        "property_type": "Квартира",
        "property_location": "м. Львів, Галицький р-н",
        "consultation_date": "2026-11-25",
        "additional_comments": "Тест автоматичного воркера Outbox",
        "source_channel": "REST_API",
        "source_row_id": f"auto_outbox_test_{unique_suffix}"
    }

    # 1. Submit service request
    resp = client.post("/api/v1/requests/", json=req_payload)
    assert resp.status_code == 201, f"Failed to create request: {resp.text}"
    created_id = resp.json()["id"]

    # 2. Check outbox events are created (client confirmation + admin alert)
    outbox_items = pg_session.query(NotificationOutbox).filter(
        NotificationOutbox.payload_json["request_id"].as_integer() == created_id
    ).all()
    assert len(outbox_items) == 2, f"Expected 2 outbox events (client + admin), found {len(outbox_items)}"

    recipients = [item.recipient for item in outbox_items]
    assert req_payload["email"] in recipients
    assert settings.ADMIN_EMAIL in recipients

    for item in outbox_items:
        assert item.status in ("PENDING", "PROCESSING", "MOCK_DELIVERED", "PROVIDER_ACCEPTED", "SENT")

    # 3. Trigger worker process step
    res = process_outbox(pg_session, batch_size=20)
    assert res.processed_count >= 2

    # 4. Verify transition to MOCK_DELIVERED / PROVIDER_ACCEPTED for both events
    for item in outbox_items:
        pg_session.refresh(item)
        assert item.status in ("MOCK_DELIVERED", "PROVIDER_ACCEPTED", "SENT")
        assert item.sent_at is not None
        assert len(item.attempts) >= 1
        last_attempt = item.attempts[-1]
        assert last_attempt.status == "SUCCESS"
        assert last_attempt.response_code == 250

    # 5. Idempotency: re-running process_outbox should NOT re-deliver already delivered items
    repeat_res = process_outbox(pg_session, batch_size=20)
    for item in outbox_items:
        pg_session.refresh(item)
        assert item.status in ("MOCK_DELIVERED", "PROVIDER_ACCEPTED", "SENT")
        assert len(item.attempts) == 1

def test_outbox_retry_backoff_on_failure(pg_session):
    """
    Proves GATE 6: Outbox Retry and Backoff on Failure.
    If delivery fails, status remains PENDING with incremented retry_count and delayed next_retry_at.
    Once max_retries is reached, status transitions to FAILED.
    """
    from core.services.outbox_service import process_outbox

    # Create synthetic outbox item with forced failure flag and 1 retry remaining
    item = NotificationOutbox(
        channel="EMAIL",
        recipient="admin@rentkom.lviv.ua",
        subject="Тест збою відправки з retry",
        status="PENDING",
        payload_json={"force_fail": True, "test_case": "retry_backoff"},
        retry_count=0,
        max_retries=2,
        next_retry_at=datetime.now(timezone.utc) - timedelta(seconds=10)
    )
    pg_session.add(item)
    pg_session.commit()
    pg_session.refresh(item)

    # First attempt: failure 1 -> status remains PENDING, retry_count = 1, backoff scheduled
    res1 = process_outbox(pg_session, batch_size=20)
    pg_session.refresh(item)
    assert item.status == "PENDING"
    assert item.retry_count == 1
    next_retry = item.next_retry_at.replace(tzinfo=timezone.utc) if item.next_retry_at.tzinfo is None else item.next_retry_at
    assert next_retry > datetime.now(timezone.utc) - timedelta(seconds=1)
    assert len(item.attempts) == 1
    assert item.attempts[-1].status == "ERROR"
    assert item.attempts[-1].response_code == 503

    # Fast-forward retry timer to simulate passage of backoff period
    item.next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    pg_session.commit()

    # Second attempt: failure 2 -> reaches max_retries (2) -> status becomes FAILED
    res2 = process_outbox(pg_session, batch_size=20)
    pg_session.refresh(item)
    assert item.status == "FAILED"
    assert item.retry_count == 2
    assert len(item.attempts) == 2
    assert item.attempts[-1].status == "ERROR"
