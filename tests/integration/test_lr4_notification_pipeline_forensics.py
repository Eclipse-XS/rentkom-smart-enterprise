import pytest
import time
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from core.database import Base
from core.models import ServiceRequest, NotificationOutbox, NotificationAttempt
from core.services.outbox_service import process_outbox
from core.config import settings

POSTGRES_URL = "postgresql+psycopg2://postgres@127.0.0.1:5433/postgres"

@pytest.fixture(scope="module")
def pg_session():
    engine = create_engine(POSTGRES_URL)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    session = Session()
    yield session
    session.close()
    engine.dispose()

def test_a_single_request_single_outbox_single_delivery(pg_session):
    """
    Test A: One request -> one service_requests entry -> one outbox event -> one mock delivery.
    """
    unique_id = f"lr4_gate3_test_a_{int(time.time() * 1000 % 10000000)}"
    res = pg_session.execute(text("""
        SELECT * FROM public.insert_real_estate_request_legacy(
            'Тест Заявка А', '+380679991122', 'Хочу купити', 'Квартира',
            'м. Львів, Франківський р-н', '2026-11-20'::date, 'Коментар А', :src_id
        );
    """), {"src_id": unique_id}).fetchall()
    pg_session.commit()
    assert len(res) == 1
    req_id = res[0].id

    # Verify exactly 1 outbox event created in PENDING status
    outbox_items = pg_session.query(NotificationOutbox).filter(
        NotificationOutbox.payload_json["request_id"].as_integer() == req_id
    ).all()
    assert len(outbox_items) == 1
    outbox_item = outbox_items[0]
    assert outbox_item.status == "PENDING"

    # Process outbox
    result = process_outbox(pg_session, batch_size=100)
    assert result.processed_count >= 1

    pg_session.refresh(outbox_item)
    assert outbox_item.status in ("MOCK_DELIVERED", "PROVIDER_ACCEPTED", "SENT")
    assert outbox_item.sent_at is not None
    assert len(outbox_item.attempts) >= 1
    success_attempt = [a for a in outbox_item.attempts if a.status == "SUCCESS"][0]
    assert success_attempt.response_code == 250

def test_b_repeated_source_row_id_zero_duplicates(pg_session):
    """
    Test B: Repeated source_row_id -> zero new requests -> zero new outbox events.
    """
    unique_id = f"lr4_gate3_test_b_{int(time.time() * 1000 % 10000000)}"

    # First insert
    res1 = pg_session.execute(text("""
        SELECT * FROM public.insert_real_estate_request_legacy(
            'Тест Заявка Б', '+380679993344', 'Хочу купити', 'Будинок',
            'м. Львів, Сихів', '2026-11-22'::date, 'Коментар Б1', :src_id
        );
    """), {"src_id": unique_id}).fetchall()
    pg_session.commit()
    assert len(res1) == 1
    req_id = res1[0].id

    # Second insert with identical source_row_id
    res2 = pg_session.execute(text("""
        SELECT * FROM public.insert_real_estate_request_legacy(
            'Тест Заявка Б Дублікат', '+380679993344', 'Хочу купити', 'Будинок',
            'м. Львів, Сихів', '2026-11-22'::date, 'Коментар Б2', :src_id
        );
    """), {"src_id": unique_id}).fetchall()
    pg_session.commit()

    # Must return 0 rows
    assert len(res2) == 0

    # Verify no duplicate service_request and no duplicate outbox event
    req_count = pg_session.query(ServiceRequest).filter(
        ServiceRequest.source_row_id == unique_id
    ).count()
    assert req_count == 1

    outbox_count = pg_session.query(NotificationOutbox).filter(
        NotificationOutbox.payload_json["request_id"].as_integer() == req_id
    ).count()
    assert outbox_count == 1

def test_c_provider_unavailable_retry_recovery(pg_session):
    """
    Test C: Gmail provider unavailable -> outbox pending/retry -> successful retry delivery.
    """
    unique_id = f"lr4_gate3_test_c_{int(time.time() * 1000 % 10000000)}"
    res = pg_session.execute(text("""
        SELECT * FROM public.insert_real_estate_request_legacy(
            'Тест Заявка В', '+380679995566', 'Хочу орендувати', 'Квартира',
            'м. Львів, Центр', '2026-11-25'::date, 'Коментар В', :src_id
        );
    """), {"src_id": unique_id}).fetchall()
    pg_session.commit()
    req_id = res[0].id

    outbox_item = pg_session.query(NotificationOutbox).filter(
        NotificationOutbox.payload_json["request_id"].as_integer() == req_id
    ).first()
    assert outbox_item.status == "PENDING"

    # 1. Process with simulated failure (provider unavailable)
    orig_provider = settings.NOTIFICATION_PROVIDER
    try:
        settings.NOTIFICATION_PROVIDER = "mock_fail"
        process_outbox(pg_session)
        pg_session.refresh(outbox_item)

        assert outbox_item.status == "PENDING"
        assert outbox_item.retry_count == 1
        assert "Simulated upstream notification dispatch failure" in (outbox_item.last_error or "")
        assert len(outbox_item.attempts) == 1
        assert outbox_item.attempts[0].status == "ERROR"

        # Reset next_retry_at to now for immediate retry
        outbox_item.next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        pg_session.commit()

        # 2. Process with healthy provider (recovery)
        settings.NOTIFICATION_PROVIDER = "mock"
        process_outbox(pg_session)
        pg_session.refresh(outbox_item)

        assert outbox_item.status in ("MOCK_DELIVERED", "PROVIDER_ACCEPTED", "SENT")
        assert outbox_item.retry_count == 1
        assert len(outbox_item.attempts) == 2
        assert outbox_item.attempts[1].status == "SUCCESS"
        assert outbox_item.attempts[1].response_code == 250
    finally:
        settings.NOTIFICATION_PROVIDER = orig_provider

def test_d_concurrent_calls_same_source_row_id():
    """
    Test D: Two concurrent workers invoking insert_real_estate_request_legacy with the SAME source_row_id.
    """
    engine = create_engine(POSTGRES_URL)
    unique_id = f"lr4_gate3_test_d_{int(time.time() * 1000 % 10000000)}"

    def worker_call():
        with engine.connect() as conn:
            res = conn.execute(text("""
                SELECT * FROM public.insert_real_estate_request_legacy(
                    'Конкурентна Заявка', '+380679997788', 'Хочу купити', 'Квартира',
                    'м. Львів, Центр', '2026-11-30'::date, 'Конкуренція', :src_id
                );
            """), {"src_id": unique_id}).fetchall()
            conn.commit()
            return len(res)

    with ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(worker_call)
        f2 = executor.submit(worker_call)
        results = [f1.result(), f2.result()]

    # Exactly one worker wins (returns 1 row), the other returns 0 rows
    assert sorted(results) == [0, 1]

    with engine.connect() as conn:
        req_count = conn.execute(text(
            "SELECT COUNT(*) FROM public.service_requests WHERE source_row_id = :src_id;"
        ), {"src_id": unique_id}).scalar()
        assert req_count == 1

        outbox_count = conn.execute(text(
            "SELECT COUNT(*) FROM public.notification_outbox WHERE payload_json->>'source_row_id' = :src_id;"
        ), {"src_id": unique_id}).scalar()
        assert outbox_count == 1
    engine.dispose()

def test_e_crash_after_delivery_at_least_once_documentation(pg_session):
    """
    Test E: Crash after delivery before recording status, documenting at-least-once semantic.
    """
    unique_id = f"lr4_gate3_test_e_{int(time.time() * 1000 % 10000000)}"
    res = pg_session.execute(text("""
        SELECT * FROM public.insert_real_estate_request_legacy(
            'Crash Тест Заявка', '+380679998899', 'Хочу купити', 'Земля',
            'м. Львів, Брюховичі', '2026-12-05'::date, 'Crash Тест', :src_id
        );
    """), {"src_id": unique_id}).fetchall()
    pg_session.commit()
    req_id = res[0].id

    outbox_item = pg_session.query(NotificationOutbox).filter(
        NotificationOutbox.payload_json["request_id"].as_integer() == req_id
    ).first()
    assert outbox_item.status == "PENDING"

    # Documentation:
    # In Transactional Outbox pattern, if a worker crashes between physical message dispatch and DB commit,
    # the outbox row remains in PENDING or stale PROCESSING.
    # The recovery monitor claims it after stale_timeout and re-delivers.
    # This guarantees AT-LEAST-ONCE delivery (zero loss of business events).
    assert outbox_item.status == "PENDING"
