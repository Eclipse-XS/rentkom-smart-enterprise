import email
from email.header import decode_header
import socket
import threading
import time
from typing import List, Optional
from datetime import datetime, timedelta, timezone
import pytest
from core.models import NotificationOutbox
from core.services.outbox_service import SMTPTransportAdapter, process_outbox
from core.config import settings


class LocalTestSMTPServer:
    """Configurable RFC 5321 local TCP SMTP server for deterministic integration testing."""
    def __init__(
        self,
        port: int = 0,
        reject_auth: bool = False,
        reject_recipient: bool = False,
        temp_fail_data: bool = False,
        hang_seconds: float = 0.0,
    ):
        self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_socket.bind(("127.0.0.1", port))
        self.port = self._server_socket.getsockname()[1]
        self._server_socket.listen(5)
        self.received_messages: List[str] = []
        self.reject_auth = reject_auth
        self.reject_recipient = reject_recipient
        self.temp_fail_data = temp_fail_data
        self.hang_seconds = hang_seconds
        self._is_running = True
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()
        time.sleep(0.05)

    def stop(self):
        self._is_running = False
        try:
            self._server_socket.close()
        except Exception:
            pass

    def _run(self):
        while self._is_running:
            try:
                conn, _ = self._server_socket.accept()
                with conn:
                    if self.hang_seconds > 0:
                        time.sleep(self.hang_seconds)
                    conn.sendall(b"220 rentkom-test-smtp ESMTP Ready\r\n")
                    in_data = False
                    data_buffer = []
                    while True:
                        line = conn.recv(1024)
                        if not line:
                            break
                        line_str = line.decode("utf-8", errors="replace")
                        upper = line_str.upper()
                        if in_data:
                            data_buffer.append(line_str)
                            if "\r\n.\r\n" in line_str or line_str.endswith("\n.\n"):
                                in_data = False
                                if self.temp_fail_data:
                                    conn.sendall(b"451 4.3.0 Temporary local problem - please try later\r\n")
                                else:
                                    self.received_messages.append("".join(data_buffer))
                                    conn.sendall(b"250 2.0.0 Ok: message received\r\n")
                        elif upper.startswith("EHLO") or upper.startswith("HELO"):
                            conn.sendall(b"250-rentkom-test-smtp\r\n250-AUTH PLAIN LOGIN\r\n250 OK\r\n")
                        elif upper.startswith("AUTH"):
                            if self.reject_auth:
                                conn.sendall(b"535 5.7.8 Authentication credentials invalid\r\n")
                            else:
                                conn.sendall(b"235 2.7.0 Authentication successful\r\n")
                        elif upper.startswith("MAIL FROM"):
                            conn.sendall(b"250 2.1.0 Ok\r\n")
                        elif upper.startswith("RCPT TO"):
                            if self.reject_recipient:
                                conn.sendall(b"550 5.1.1 <unknown@example.com>: Recipient address rejected\r\n")
                            else:
                                conn.sendall(b"250 2.1.5 Ok\r\n")
                        elif upper.startswith("DATA"):
                            in_data = True
                            conn.sendall(b"354 End data with <CR><LF>.<CR><LF>\r\n")
                        elif upper.startswith("QUIT"):
                            conn.sendall(b"221 2.0.0 Bye\r\n")
                            break
                        else:
                            conn.sendall(b"250 OK\r\n")
            except Exception:
                break


def test_smtp_missing_configuration_fails_closed():
    """Verify that SMTP provider never fakes success when SMTP_HOST is missing."""
    adapter = SMTPTransportAdapter(host="")
    success, code, err = adapter.send_notification(
        recipient="client@example.com",
        subject="Test Subject",
        payload={"request_id": 101}
    )
    assert not success
    assert code == 503
    assert "SMTP_HOST is not configured" in err


def test_smtp_unreachable_host_connection_refused_retries(db_session):
    """Negative case 1: Connection refused results in status=PENDING with retry backoff, not false success."""
    adapter = SMTPTransportAdapter(host="127.0.0.1", port=65533, use_tls=False, timeout=0.5)

    item = NotificationOutbox(
        channel="EMAIL",
        recipient="client@example.com",
        subject="Test Unreachable",
        payload_json={"test": "unreachable"},
        status="PENDING",
        retry_count=0
    )
    db_session.add(item)
    db_session.commit()

    orig_provider = settings.NOTIFICATION_PROVIDER
    try:
        settings.NOTIFICATION_PROVIDER = "smtp"
        res = process_outbox(db_session, batch_size=10, smtp_transport=adapter)
        assert res.processed_count == 1
        assert res.failed_count == 1
        assert res.success_count == 0
        assert res.details[0]["delivery_state"] == "RETRY_PENDING"

        db_session.refresh(item)
        assert item.status == "PENDING"
        assert item.retry_count == 1
        assert item.last_error is not None
        assert len(item.attempts) == 1
        assert item.attempts[0].status == "ERROR"
        assert item.attempts[0].response_code in (502, 504)
    finally:
        settings.NOTIFICATION_PROVIDER = orig_provider


def test_smtp_timeout_records_error_and_schedules_retry(db_session):
    """Negative case 2: SMTP server timeout is caught and recorded with code 504."""
    server = LocalTestSMTPServer(hang_seconds=1.2)
    server.start()
    adapter = SMTPTransportAdapter(host="127.0.0.1", port=server.port, use_tls=False, timeout=0.2)

    item = NotificationOutbox(
        channel="EMAIL",
        recipient="timeout_user@example.com",
        subject="Timeout Test",
        payload_json={"case": "timeout"},
        status="PENDING",
    )
    db_session.add(item)
    db_session.commit()

    orig_provider = settings.NOTIFICATION_PROVIDER
    try:
        settings.NOTIFICATION_PROVIDER = "smtp"
        res = process_outbox(db_session, batch_size=10, smtp_transport=adapter)
        assert res.failed_count == 1
        db_session.refresh(item)
        assert item.status == "PENDING"
        assert item.retry_count == 1
        assert item.attempts[0].response_code == 504
        assert "timeout" in (item.last_error or "").lower()
    finally:
        settings.NOTIFICATION_PROVIDER = orig_provider
        server.stop()


def test_smtp_authentication_error_535_does_not_leak_password(db_session):
    """Negative case 3: Authentication failure records code 535 and never leaks password in error logs."""
    server = LocalTestSMTPServer(reject_auth=True)
    server.start()
    secret_pw = "super_secret_smtp_pass_999"
    adapter = SMTPTransportAdapter(
        host="127.0.0.1",
        port=server.port,
        username="mailer@rentkom.lviv.ua",
        password=secret_pw,
        use_tls=False,
        timeout=2.0,
    )

    item = NotificationOutbox(
        channel="EMAIL",
        recipient="auth_test@example.com",
        subject="Auth Failure Test",
        payload_json={"case": "auth_535"},
        status="PENDING",
    )
    db_session.add(item)
    db_session.commit()

    orig_provider = settings.NOTIFICATION_PROVIDER
    try:
        settings.NOTIFICATION_PROVIDER = "smtp"
        res = process_outbox(db_session, batch_size=10, smtp_transport=adapter)
        assert res.failed_count == 1
        db_session.refresh(item)
        assert item.attempts[0].response_code == 535
        assert secret_pw not in (item.last_error or "")
    finally:
        settings.NOTIFICATION_PROVIDER = orig_provider
        server.stop()


def test_smtp_recipient_refused_550_and_temporary_451_failure(db_session):
    """Negative cases 4 & 5: Recipient rejection (550) and temporary server error (451)."""
    # 1. Recipient refused (550)
    s_refused = LocalTestSMTPServer(reject_recipient=True)
    s_refused.start()
    adapter_refused = SMTPTransportAdapter(host="127.0.0.1", port=s_refused.port, use_tls=False, timeout=2.0)

    item_550 = NotificationOutbox(
        channel="EMAIL",
        recipient="unknown@example.com",
        subject="Refused Recipient Test",
        payload_json={"case": "550"},
        status="PENDING",
    )
    db_session.add(item_550)
    db_session.commit()

    # 2. Temporary server failure (451)
    s_temp = LocalTestSMTPServer(temp_fail_data=True)
    s_temp.start()
    adapter_temp = SMTPTransportAdapter(host="127.0.0.1", port=s_temp.port, use_tls=False, timeout=2.0)

    orig_provider = settings.NOTIFICATION_PROVIDER
    try:
        settings.NOTIFICATION_PROVIDER = "smtp"
        process_outbox(db_session, batch_size=10, smtp_transport=adapter_refused)
        db_session.refresh(item_550)
        assert item_550.attempts[0].response_code == 550
        assert item_550.status == "PENDING"

        item_451 = NotificationOutbox(
            channel="EMAIL",
            recipient="temp@example.com",
            subject="Temp 451 Test",
            payload_json={"case": "451"},
            status="PENDING",
        )
        db_session.add(item_451)
        db_session.commit()

        process_outbox(db_session, batch_size=10, smtp_transport=adapter_temp)
        db_session.refresh(item_451)
        assert item_451.attempts[0].response_code == 451
        assert item_451.status == "PENDING"
    finally:
        settings.NOTIFICATION_PROVIDER = orig_provider
        s_refused.stop()
        s_temp.stop()


def test_real_smtp_delivery_and_idempotent_reprocessing(db_session):
    """
    Positive integration test: Starts local test SMTP server, dispatches email over TCP socket,
    proves that the email was actually delivered (PROVIDER_ACCEPTED), and verifies that repeated
    processing does not re-send the email.
    """
    server = LocalTestSMTPServer()
    server.start()

    adapter = SMTPTransportAdapter(
        host="127.0.0.1",
        port=server.port,
        use_tls=False,
        timeout=5.0
    )

    item = NotificationOutbox(
        channel="EMAIL",
        recipient="real_client@example.com",
        subject="Підтвердження запиту на консультацію #777",
        payload_json={"request_id": 777, "customer_name": "Іван Франко", "purpose": "Хочу купити"},
        status="PENDING",
        retry_count=0
    )
    db_session.add(item)
    db_session.commit()

    orig_provider = settings.NOTIFICATION_PROVIDER
    try:
        settings.NOTIFICATION_PROVIDER = "smtp"
        res = process_outbox(db_session, batch_size=10, smtp_transport=adapter)
        assert res.processed_count == 1
        assert res.success_count == 1
        assert res.failed_count == 0

        db_session.refresh(item)
        assert item.status == "PROVIDER_ACCEPTED"
        assert item.sent_at is not None
        assert len(item.attempts) == 1
        assert item.attempts[0].status == "SUCCESS"
        assert item.attempts[0].response_code == 250

        # Verify that message was received over real TCP socket by local SMTP server
        assert len(server.received_messages) == 1
        received = server.received_messages[0]
        assert "real_client@example.com" in received

        parsed_email = email.message_from_string(received)
        assert parsed_email["To"] == "real_client@example.com"
        assert parsed_email["From"] == settings.ADMIN_EMAIL

        subj_parts = decode_header(parsed_email["Subject"])
        subj_text = "".join(
            part.decode(enc or "utf-8") if isinstance(part, bytes) else part
            for part, enc in subj_parts
        )
        assert "Підтвердження запиту на консультацію #777" in subj_text

        payload_bytes = parsed_email.get_payload(decode=True)
        body_text = payload_bytes.decode("utf-8")
        assert "Іван Франко" in body_text
        assert "Хочу купити" in body_text

        # Repeated processing must not re-send
        res_repeat = process_outbox(db_session, batch_size=10, smtp_transport=adapter)
        assert res_repeat.processed_count == 0
        assert len(server.received_messages) == 1
    finally:
        settings.NOTIFICATION_PROVIDER = orig_provider
        server.stop()
