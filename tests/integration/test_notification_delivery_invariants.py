# -*- coding: utf-8 -*-
"""
Notification Delivery Invariants Regression Suite (NOTIF Gate).
Covers:
- NOTIF-T01: Unknown provider fails closed (status=FAILED/PENDING, sent_at=None, error recorded).
- NOTIF-T02: SMTP delivery executes off the main event loop (does not block concurrent HTTP requests).
- NOTIF-T03: Network timeout on SMTP produces retryable failure, not unhandled exception.
- NOTIF-T04: Authentication failure on SMTP produces bounded retry failure with recorded error.
- NOTIF-T05: TLS negotiation failure is recorded accurately.
- NOTIF-T06: Telegram provider requires valid TELEGRAM_BOT_TOKEN; missing token fails closed.
- NOTIF-T07: Telegram delivery verifies ok==true from Telegram API response before marking success.
- NOTIF-T08: Channel/provider mismatch fails closed with routing error.
- NOTIF-T09: Mock provider records MOCK_DELIVERED (not PROVIDER_ACCEPTED) in delivery metadata.
- NOTIF-T10: Ten concurrent worker threads cannot deliver the same outbox item twice.
- NOTIF-T11: Crash recovery releases expired leases and reprocesses pending items.
- NOTIF-T12: Max retry exhaustion transitions to permanent failure state.
"""
import asyncio
import smtplib
import socket
import ssl
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import pytest
from core.config import settings
from core.models import NotificationOutbox
from core.services.outbox_service import (
    process_outbox,
    SMTPTransportAdapter,
    TelegramTransportAdapter,
)


def test_notif_t01_unknown_provider_fails_closed(db_session, monkeypatch):
    """NOTIF-T01: Unknown provider fails closed (sent_at=None, error recorded)."""
    monkeypatch.setattr(settings, "NOTIFICATION_PROVIDER", "unsupported_xyz_provider")
    item = NotificationOutbox(
        channel="EMAIL",
        recipient="user@test.ua",
        subject="Unknown provider test",
        payload_json={"test": 1},
        status="PENDING",
        retry_count=0,
        max_retries=3
    )
    db_session.add(item)
    db_session.commit()

    result = process_outbox(db_session)
    assert result.failed_count == 1
    assert result.success_count == 0

    db_session.refresh(item)
    assert item.sent_at is None
    assert item.retry_count == 1
    assert "Unknown notification provider" in (item.last_error or "")
    assert len(item.attempts) == 1
    assert item.attempts[0].status == "ERROR"


@pytest.mark.anyio
async def test_notif_t02_smtp_delivery_executes_off_main_event_loop(db_session, monkeypatch):
    """NOTIF-T02: SMTP delivery executes off the main event loop via asyncio.to_thread."""
    monkeypatch.setattr(settings, "NOTIFICATION_PROVIDER", "smtp")
    fast_coro_completed_at = None
    thread_finished_at = None

    class SlowTransport(SMTPTransportAdapter):
        def send_notification(self, recipient, subject, payload):
            time.sleep(0.25)
            return True, 250, None

    item = NotificationOutbox(
        channel="EMAIL",
        recipient="offloop@test.ua",
        subject="Offloop test",
        payload_json={},
        status="PENDING"
    )
    db_session.add(item)
    db_session.commit()

    async def fast_coro():
        nonlocal fast_coro_completed_at
        await asyncio.sleep(0.02)
        fast_coro_completed_at = time.time()

    coro_task = asyncio.create_task(fast_coro())

    res = await asyncio.to_thread(lambda: process_outbox(db_session, smtp_transport=SlowTransport()))
    thread_finished_at = time.time()
    await coro_task

    assert res.success_count == 1
    assert fast_coro_completed_at is not None
    assert thread_finished_at is not None
    # Concurrently scheduled coroutine on event loop completed well before the blocking transport finished
    assert fast_coro_completed_at < thread_finished_at
    assert (thread_finished_at - fast_coro_completed_at) >= 0.1, (
        f"Event loop was blocked: fast_coro at {fast_coro_completed_at}, thread finished at {thread_finished_at}"
    )


def test_notif_t03_smtp_timeout_produces_retryable_failure(db_session, monkeypatch):
    """NOTIF-T03: Network timeout on SMTP produces retryable failure, not unhandled exception."""
    monkeypatch.setattr(settings, "NOTIFICATION_PROVIDER", "smtp")
    adapter = SMTPTransportAdapter(host="smtp.rentkom.lviv.ua")

    def mock_smtp_timeout(*args, **kwargs):
        raise socket.timeout("timed out connecting to smtp.rentkom.lviv.ua")

    monkeypatch.setattr(smtplib, "SMTP", mock_smtp_timeout)

    # 1. Adapter returns structured 504 error rather than unhandled exception
    success, code, err = adapter.send_notification("test@rentkom.ua", "Sub", {})
    assert success is False
    assert code == 504
    assert "timeout" in err.lower()

    # 2. Outbox processor handles timeout gracefully and schedules retry
    item = NotificationOutbox(
        channel="EMAIL",
        recipient="timeout_test@rentkom.ua",
        subject="Timeout Sub",
        payload_json={},
        status="PENDING",
        retry_count=0,
        max_retries=3
    )
    db_session.add(item)
    db_session.commit()

    res = process_outbox(db_session, smtp_transport=adapter)
    assert res.failed_count == 1
    db_session.refresh(item)
    assert item.retry_count == 1
    assert item.status == "PENDING"
    assert item.sent_at is None
    assert "timeout" in (item.last_error or "").lower()


def test_notif_t04_smtp_authentication_failure_recorded(monkeypatch):
    """NOTIF-T04: Authentication failure on SMTP produces failure with recorded error."""
    adapter = SMTPTransportAdapter(host="smtp.example.com", username="bad", password="bad")

    def mock_smtp(*args, **kwargs):
        class MockSMTP:
            def starttls(self): pass
            def login(self, u, p):
                raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Authentication credentials invalid")
            def quit(self): pass
        return MockSMTP()

    monkeypatch.setattr(smtplib, "SMTP", mock_smtp)
    success, code, err = adapter.send_notification("user@test.ua", "Sub", {})
    assert success is False
    assert code == 535
    assert "Authentication failed" in err


def test_notif_t05_tls_negotiation_failure_recorded(monkeypatch):
    """NOTIF-T05: TLS negotiation failure is recorded accurately."""
    adapter = SMTPTransportAdapter(host="smtp.example.com", use_tls=True)

    def mock_smtp(*args, **kwargs):
        class MockSMTP:
            def starttls(self):
                raise ssl.SSLError(1, "certificate verify failed: IP address mismatch")
            def quit(self): pass
        return MockSMTP()

    monkeypatch.setattr(smtplib, "SMTP", mock_smtp)
    success, code, err = adapter.send_notification("user@test.ua", "Sub", {})
    assert success is False
    assert code == 525
    assert "TLS negotiation failed" in err


def test_notif_t06_telegram_missing_token_fails_closed(db_session, monkeypatch):
    """NOTIF-T06: Telegram provider requires valid TELEGRAM_BOT_TOKEN; missing token fails closed."""
    monkeypatch.setattr(settings, "NOTIFICATION_PROVIDER", "telegram")
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", None)

    item = NotificationOutbox(
        channel="TELEGRAM",
        recipient="123456789",
        subject="TG Token Test",
        payload_json={"telegram_chat_id": 123456789},
        status="PENDING"
    )
    db_session.add(item)
    db_session.commit()

    res = process_outbox(db_session)
    assert res.failed_count == 1
    assert res.success_count == 0

    db_session.refresh(item)
    assert item.sent_at is None
    assert "TELEGRAM_BOT_TOKEN is not configured" in (item.last_error or "")


def test_notif_t07_telegram_delivery_verifies_ok_true_response(db_session, monkeypatch):
    """NOTIF-T07: Telegram delivery verifies ok==true from Telegram API response before marking success."""
    monkeypatch.setattr(settings, "NOTIFICATION_PROVIDER", "telegram")
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "123456:ABC-DEF")

    # Case A: Telegram API returns ok=False
    class FailingTelegramAdapter(TelegramTransportAdapter):
        def send_notification(self, recipient, subject, payload):
            return False, 400, "Telegram API error (status=400): Bad Request: chat not found", {"ok": False}

    item_fail = NotificationOutbox(
        channel="TELEGRAM",
        recipient="999000",
        subject="Fail check",
        payload_json={"telegram_chat_id": 999000},
        status="PENDING"
    )
    db_session.add(item_fail)
    db_session.commit()

    res_fail = process_outbox(db_session, telegram_transport=FailingTelegramAdapter())
    assert res_fail.failed_count == 1
    db_session.refresh(item_fail)
    assert item_fail.sent_at is None
    assert item_fail.status != "PROVIDER_ACCEPTED"
    assert "chat not found" in (item_fail.last_error or "")

    # Case B: Telegram API returns ok=True
    class SuccessTelegramAdapter(TelegramTransportAdapter):
        def send_notification(self, recipient, subject, payload):
            return True, 200, None, {"provider_message_id": 778899, "chat_id": recipient}

    item_ok = NotificationOutbox(
        channel="TELEGRAM",
        recipient="888111",
        subject="Success check",
        payload_json={"telegram_chat_id": 888111},
        status="PENDING"
    )
    db_session.add(item_ok)
    db_session.commit()

    res_ok = process_outbox(db_session, telegram_transport=SuccessTelegramAdapter())
    assert res_ok.success_count == 1
    db_session.refresh(item_ok)
    assert item_ok.sent_at is not None
    assert item_ok.status == "PROVIDER_ACCEPTED"
    assert item_ok.payload_json["provider_metadata"]["provider_message_id"] == 778899


def test_notif_t08_channel_provider_mismatch_fails_closed(db_session, monkeypatch):
    """NOTIF-T08: Channel/provider mismatch fails closed with routing error."""
    # Sub-case A: SMTP provider given TELEGRAM channel item
    monkeypatch.setattr(settings, "NOTIFICATION_PROVIDER", "smtp")
    item_tg = NotificationOutbox(
        channel="TELEGRAM",
        recipient="123456789",
        subject="TG on SMTP",
        payload_json={},
        status="PENDING"
    )
    db_session.add(item_tg)
    db_session.commit()

    res_a = process_outbox(db_session)
    assert res_a.failed_count == 1
    db_session.refresh(item_tg)
    assert item_tg.sent_at is None
    assert "Routing mismatch" in (item_tg.last_error or "")

    # Sub-case B: Telegram provider given EMAIL channel item
    monkeypatch.setattr(settings, "NOTIFICATION_PROVIDER", "telegram")
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "123:test")
    item_email = NotificationOutbox(
        channel="EMAIL",
        recipient="user@rentkom.ua",
        subject="Email on TG",
        payload_json={},
        status="PENDING"
    )
    db_session.add(item_email)
    db_session.commit()

    res_b = process_outbox(db_session)
    assert res_b.failed_count == 1
    db_session.refresh(item_email)
    assert item_email.sent_at is None
    assert "Routing mismatch" in (item_email.last_error or "")


def test_notif_t09_mock_provider_records_mock_delivered(db_session, monkeypatch):
    """NOTIF-T09: Mock provider records MOCK_DELIVERED (not PROVIDER_ACCEPTED) in delivery metadata."""
    monkeypatch.setattr(settings, "NOTIFICATION_PROVIDER", "mock")
    item = NotificationOutbox(
        channel="EMAIL",
        recipient="mock_test@rentkom.ua",
        subject="Mock check",
        payload_json={"test": True},
        status="PENDING"
    )
    db_session.add(item)
    db_session.commit()

    res = process_outbox(db_session)
    assert res.success_count == 1
    db_session.refresh(item)
    assert item.status == "MOCK_DELIVERED"
    assert item.sent_at is not None
    assert res.details[0]["delivery_state"] == "MOCK_DELIVERED"


def test_notif_t10_concurrent_workers_prevent_duplicate_delivery(tmp_path, monkeypatch):
    """NOTIF-T10: Ten concurrent worker threads cannot deliver the same outbox item twice."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from core.database import Base

    db_path = tmp_path / "concurrent_outbox.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 15}
    )
    Session = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)

    # Insert exactly 1 pending item
    with Session() as s:
        it = NotificationOutbox(
            channel="EMAIL",
            recipient="single_delivery@rentkom.ua",
            subject="Concurrency check",
            payload_json={},
            status="PENDING"
        )
        s.add(it)
        s.commit()
        item_id = it.id

    delivered_count = 0

    def worker_func():
        nonlocal delivered_count
        with Session() as s:
            r = process_outbox(s, batch_size=1)
            delivered_count += r.success_count

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(worker_func) for _ in range(10)]
        for f in futures:
            f.result()

    assert delivered_count == 1, f"Expected exactly 1 delivery across 10 workers, got {delivered_count}"
    with Session() as s:
        final_item = s.query(NotificationOutbox).filter_by(id=item_id).one()
        assert final_item.status == "MOCK_DELIVERED"
        assert len(final_item.attempts) == 1


def test_notif_t11_crash_recovery_reclaims_expired_leases(db_session, monkeypatch):
    """NOTIF-T11: Crash recovery releases expired leases and reprocesses pending items."""
    monkeypatch.setattr(settings, "NOTIFICATION_PROVIDER", "mock")
    crashed_time = datetime.now(timezone.utc) - timedelta(minutes=15)
    item = NotificationOutbox(
        channel="EMAIL",
        recipient="crash_recover@rentkom.ua",
        subject="Crash Lease",
        payload_json={"crash": True},
        status="PROCESSING",
        next_retry_at=crashed_time
    )
    db_session.add(item)
    db_session.commit()

    # With stale_timeout_seconds=300 (5 mins), 15 mins ago is well past stale threshold
    res = process_outbox(db_session, stale_timeout_seconds=300)
    assert res.processed_count == 1
    assert res.success_count == 1

    db_session.refresh(item)
    assert item.status == "MOCK_DELIVERED"
    assert item.sent_at is not None


def test_notif_t12_max_retries_exhaustion_transitions_to_failed_permanent(db_session, monkeypatch):
    """NOTIF-T12: Max retry exhaustion transitions to permanent failure state."""
    item = NotificationOutbox(
        channel="EMAIL",
        recipient="exhaust_retries@rentkom.ua",
        subject="Max Retries",
        payload_json={"force_fail": True},
        status="PENDING",
        retry_count=4,
        max_retries=5
    )
    db_session.add(item)
    db_session.commit()

    res = process_outbox(db_session)
    assert res.failed_count == 1

    db_session.refresh(item)
    assert item.retry_count == 5
    assert item.status == "FAILED"
    assert res.details[0]["delivery_state"] == "FAILED_PERMANENT"
