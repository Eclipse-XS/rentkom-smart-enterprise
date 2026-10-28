from datetime import date, datetime, timedelta, timezone
from core.models import NotificationOutbox
from core.schemas import ServiceRequestCreate
from core.services.request_service import create_service_request
from core.services.outbox_service import process_outbox

def test_outbox_atomicity(db_session):
    # Create request
    req = ServiceRequestCreate(
        full_name="Ірина Ковальчук",
        phone="+380502223344",
        email="irina@test.ua",
        request_purpose="Хочу орендувати",
        property_type="Квартира",
        property_location="Сихівський р-н",
        consultation_date=date(2026, 11, 5)
    )
    res = create_service_request(db_session, req)

    # Verify outbox item was created in the SAME transaction (client + admin)
    outbox = db_session.query(NotificationOutbox).all()
    assert len(outbox) >= 1
    recipients = [o.recipient for o in outbox]
    assert "irina@test.ua" in recipients
    client_item = next(o for o in outbox if o.recipient == "irina@test.ua")
    assert client_item.status == "PENDING"
    assert client_item.payload_json["request_id"] == res.id

def test_outbox_processing_mock(db_session):
    # Setup pending outbox item
    item = NotificationOutbox(
        channel="EMAIL",
        recipient="test@rentkom.ua",
        subject="Test notification",
        payload_json={"test": 123},
        status="PENDING"
    )
    db_session.add(item)
    db_session.commit()

    result = process_outbox(db_session)
    assert result.processed_count == 1
    assert result.success_count == 1

    # Check status changed to MOCK_DELIVERED (or PROVIDER_ACCEPTED)
    db_session.refresh(item)
    assert item.status in ("MOCK_DELIVERED", "PROVIDER_ACCEPTED", "SENT")
    assert item.sent_at is not None
    assert len(item.attempts) == 1
    assert item.attempts[0].status == "SUCCESS"

def test_outbox_case_a_worker_crash_recovery(db_session):
    """Case A: Worker crash while outbox item is in PROCESSING status (recovery on next run)."""
    # Simulate crashed worker: item stuck in PROCESSING since 10 minutes ago
    crashed_time = datetime.now(timezone.utc) - timedelta(minutes=10)
    item = NotificationOutbox(
        channel="EMAIL",
        recipient="crashed_worker_recovery@rentkom.ua",
        subject="Worker crash simulation",
        payload_json={"task": "recovery"},
        status="PROCESSING",
        next_retry_at=crashed_time
    )
    db_session.add(item)
    db_session.commit()

    # Process outbox with stale timeout = 300s (5 minutes)
    result = process_outbox(db_session, stale_timeout_seconds=300)
    assert result.processed_count == 1
    assert result.success_count == 1

    db_session.refresh(item)
    assert item.status in ("MOCK_DELIVERED", "PROVIDER_ACCEPTED", "SENT")

def test_outbox_case_b_max_retries_dead_letter(db_session):
    """Case B: Outbox item retry count exceeded (status -> FAILED / Dead Letter)."""
    item = NotificationOutbox(
        channel="EMAIL",
        recipient="fail_target@rentkom.ua",
        subject="Simulated failure item",
        payload_json={"force_fail": True},
        status="PENDING",
        retry_count=4, # Already retried 4 times
        max_retries=5
    )
    db_session.add(item)
    db_session.commit()

    result = process_outbox(db_session)
    assert result.processed_count == 1
    assert result.failed_count == 1

    db_session.refresh(item)
    assert item.retry_count == 5
    assert item.status == "FAILED"
    assert item.last_error is not None
    assert "Simulated upstream notification dispatch failure" in item.last_error

def test_outbox_case_e_malformed_recipient(db_session):
    """Case E: Malformed recipient / invalid payload."""
    item = NotificationOutbox(
        channel="EMAIL",
        recipient="invalid-email-without-at",
        subject="Bad recipient email",
        payload_json={"key": "val"},
        status="PENDING"
    )
    db_session.add(item)
    db_session.commit()

    result = process_outbox(db_session)
    assert result.processed_count == 1
    assert result.failed_count == 1

    db_session.refresh(item)
    assert item.retry_count == 1
    assert "Invalid email format" in item.last_error

def test_outbox_case_f_idempotency(db_session):
    """Case F: Idempotent processing (processing same outbox twice does not duplicate)."""
    item = NotificationOutbox(
        channel="EMAIL",
        recipient="idempotent@rentkom.ua",
        subject="Idempotency test",
        payload_json={"data": 42},
        status="PENDING"
    )
    db_session.add(item)
    db_session.commit()

    # First run processes and delivers
    res1 = process_outbox(db_session)
    assert res1.processed_count == 1
    assert res1.success_count == 1

    # Second run finds no pending records
    res2 = process_outbox(db_session)
    assert res2.processed_count == 0
    assert res2.success_count == 0

    # Ensure attempts count is exactly 1
    db_session.refresh(item)
    assert len(item.attempts) == 1

def test_outbox_case_g_exponential_backoff_calculation(db_session):
    """Case G: Exponential backoff calculation accuracy (10s * 2^(retry-1))."""
    item = NotificationOutbox(
        channel="EMAIL",
        recipient="backoff_test@rentkom.ua",
        subject="Backoff test",
        payload_json={"force_fail": True},
        status="PENDING",
        retry_count=0,
        max_retries=5
    )
    db_session.add(item)
    db_session.commit()

    # Attempt 1: retry_count becomes 1 -> delay = 10s * 2^0 = 10s
    t0 = datetime.now(timezone.utc)
    res = process_outbox(db_session)
    assert res.failed_count == 1
    db_session.refresh(item)
    assert item.retry_count == 1
    expected_delay_1 = 10
    actual_next = item.next_retry_at.replace(tzinfo=timezone.utc) if item.next_retry_at.tzinfo is None else item.next_retry_at
    actual_delay_1 = (actual_next - t0).total_seconds()
    assert abs(actual_delay_1 - expected_delay_1) < 3

    # Fast forward item to now so it is eligible for attempt 2
    item.next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()

    # Attempt 2: retry_count becomes 2 -> delay = 10s * 2^1 = 20s
    t1 = datetime.now(timezone.utc)
    res2 = process_outbox(db_session)
    assert res2.failed_count == 1
    db_session.refresh(item)
    assert item.retry_count == 2
    expected_delay_2 = 20
    actual_next_2 = item.next_retry_at.replace(tzinfo=timezone.utc) if item.next_retry_at.tzinfo is None else item.next_retry_at
    actual_delay_2 = (actual_next_2 - t1).total_seconds()
    assert abs(actual_delay_2 - expected_delay_2) < 3

def test_outbox_no_client_notification_when_no_address(db_session):
    """Verifies that requests without verified customer email/telegram do not create bogus client notifications."""
    from core.config import settings
    req = ServiceRequestCreate(
        full_name="Олег Безпоштовий",
        phone="+380509998877",
        email=None,
        telegram_chat_id=None,
        request_purpose="Хочу купити",
        property_type="Квартира",
        property_location="Центр",
        consultation_date=date(2026, 11, 10)
    )
    res = create_service_request(db_session, req)

    # Only 1 outbox item should be created: for admin notification!
    outbox = db_session.query(NotificationOutbox).filter(
        NotificationOutbox.payload_json["request_id"].as_integer() == res.id
    ).all()
    assert len(outbox) == 1
    assert outbox[0].recipient == settings.ADMIN_EMAIL

def test_outbox_telegram_channel_notification(db_session):
    """Verifies that customer with telegram_chat_id and no email receives TELEGRAM channel notification."""
    req = ServiceRequestCreate(
        full_name="Віктор Телеграмів",
        phone="+380501112233",
        email=None,
        telegram_chat_id=987654321,
        request_purpose="Хочу орендувати",
        property_type="Комерційне приміщення",
        property_location="Франківський р-н",
        consultation_date=date(2026, 11, 12)
    )
    res = create_service_request(db_session, req)

    outbox = db_session.query(NotificationOutbox).filter(
        NotificationOutbox.payload_json["request_id"].as_integer() == res.id
    ).all()
    assert len(outbox) == 2
    client_tg = next(o for o in outbox if o.channel == "TELEGRAM")
    assert client_tg.recipient == "987654321"
    assert client_tg.status == "PENDING"
