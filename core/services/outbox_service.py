import logging
import smtplib
import socket
import ssl
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from typing import Optional, Tuple, Any, Dict
import httpx
from sqlalchemy import or_, and_
from sqlalchemy.orm import Session
from core.models import NotificationOutbox, NotificationAttempt
from core.schemas import OutboxProcessResult
from core.config import settings

logger = logging.getLogger(__name__)


class SMTPTransportAdapter:
    """
    Production-grade SMTP transport adapter supporting STARTTLS, authentication,
    connection timeouts, and structured error reporting without leaking credentials.
    """
    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        use_tls: Optional[bool] = None,
        timeout: Optional[float] = None
    ):
        self.host = host if host is not None else settings.SMTP_HOST
        self.port = port if port is not None else settings.SMTP_PORT
        self.username = username if username is not None else settings.SMTP_USER
        self.password = password if password is not None else settings.SMTP_PASSWORD
        self.use_tls = use_tls if use_tls is not None else settings.SMTP_USE_TLS
        self.timeout = timeout if timeout is not None else settings.SMTP_TIMEOUT_SECONDS

    def send_notification(self, recipient: str, subject: str, payload: Any) -> Tuple[bool, int, Optional[str]]:
        if not self.host:
            return False, 503, "SMTP configuration missing: SMTP_HOST is not configured in settings."

        body_lines = [
            f"Тема: {subject}",
            "",
            "Деталі сповіщення:",
        ]
        if isinstance(payload, dict):
            for k, v in payload.items():
                body_lines.append(f"  • {k}: {v}")
        else:
            body_lines.append(str(payload))

        body_lines.append("\n--\nАгентство нерухомості «Рентком» (м. Львів)\nАвтоматизована служба сповіщень")
        body_text = "\n".join(body_lines)

        msg = MIMEText(body_text, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = settings.ADMIN_EMAIL
        msg["To"] = recipient

        server = None
        try:
            server = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
            if self.use_tls:
                server.starttls()
            if self.username and self.password:
                server.login(self.username, self.password)

            server.sendmail(settings.ADMIN_EMAIL, [recipient], msg.as_string())
            logger.info("[SMTP] Successfully dispatched email to %s", recipient)
            return True, 250, None
        except smtplib.SMTPAuthenticationError as e:
            err = f"SMTP Authentication failed (code={e.smtp_code}): {e.smtp_error}"
            logger.error("[SMTP] %s", err)
            return False, 535, err
        except smtplib.SMTPRecipientsRefused as e:
            codes = [info[0] for info in e.recipients.values()] if isinstance(e.recipients, dict) else [550]
            code = codes[0] if codes else 550
            err = f"SMTP Recipient refused (code={code}) for {recipient}"
            logger.error("[SMTP] %s", err)
            return False, code, err
        except (ssl.SSLError, smtplib.SMTPNotSupportedError) as e:
            err = f"SMTP TLS negotiation failed: {e}"
            logger.error("[SMTP] %s", err)
            return False, 525, err
        except smtplib.SMTPResponseException as e:
            code = e.smtp_code or 500
            err = f"SMTP server response error (code={code}): {e.smtp_error}"
            logger.error("[SMTP] %s", err)
            return False, code, err
        except smtplib.SMTPException as e:
            if "timed out" in str(e).lower() or isinstance(getattr(e, "__cause__", None), (socket.timeout, TimeoutError)):
                err = f"SMTP connection timeout to {self.host}:{self.port} ({e})"
                logger.error("[SMTP] %s", err)
                return False, 504, err
            err = f"SMTP protocol error: {e}"
            logger.error("[SMTP] %s", err)
            return False, 500, err
        except (socket.timeout, TimeoutError):
            err = f"SMTP connection timeout to {self.host}:{self.port}"
            logger.error("[SMTP] %s", err)
            return False, 504, err
        except Exception as e:
            err = f"SMTP transport network error: {e}"
            logger.error("[SMTP] %s", err)
            return False, 502, err
        finally:
            if server:
                try:
                    server.quit()
                except Exception:
                    pass


class TelegramTransportAdapter:
    """
    Verified Telegram Bot API transport adapter.
    Validates token, recipient chat ID, dispatches to api.telegram.org, and verifies response ok == true.
    """
    def __init__(self, bot_token: Optional[str] = None, timeout: float = 10.0):
        self.bot_token = bot_token if bot_token is not None else settings.TELEGRAM_BOT_TOKEN
        self.timeout = timeout

    def send_notification(self, recipient: str, subject: str, payload: Any) -> Tuple[bool, int, Optional[str], Optional[Dict[str, Any]]]:
        if not self.bot_token or not self.bot_token.strip():
            return False, 503, "Telegram delivery failed: TELEGRAM_BOT_TOKEN is not configured.", None

        chat_id = recipient
        if isinstance(payload, dict) and payload.get("telegram_chat_id"):
            chat_id = str(payload.get("telegram_chat_id"))

        if not chat_id or not str(chat_id).strip():
            return False, 422, "Telegram recipient identifier cannot be empty", None

        body_text = f"🔔 *{subject}*\n\n"
        if isinstance(payload, dict):
            for k, v in payload.items():
                if k not in ("telegram_chat_id", "force_fail", "provider_metadata"):
                    body_text += f"• *{k}*: {v}\n"
        else:
            body_text += str(payload)

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        req_data = {
            "chat_id": chat_id,
            "text": body_text,
            "parse_mode": "Markdown"
        }

        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(url, json=req_data)
                try:
                    data = resp.json()
                except Exception:
                    data = {}

                if resp.status_code == 200 and data.get("ok") is True:
                    result_info = data.get("result", {})
                    msg_id = result_info.get("message_id")
                    logger.info("[TELEGRAM] Dispatched message to chat_id=%s (msg_id=%s)", chat_id, msg_id)
                    return True, 200, None, {"provider_message_id": msg_id, "chat_id": chat_id}
                else:
                    desc = data.get("description") or resp.text or "Unknown Telegram API error"
                    err = f"Telegram API error (status={resp.status_code}): {desc}"
                    logger.error("[TELEGRAM] %s", err)
                    return False, resp.status_code, err, data
        except httpx.TimeoutException as e:
            err = f"Telegram API timeout: {e}"
            logger.error("[TELEGRAM] %s", err)
            return False, 504, err, None
        except Exception as e:
            err = f"Telegram transport network error: {e}"
            logger.error("[TELEGRAM] %s", err)
            return False, 502, err, None


def process_outbox(
    db: Session,
    batch_size: int = 10,
    stale_timeout_seconds: int = 300,
    smtp_transport: Optional[SMTPTransportAdapter] = None,
    telegram_transport: Optional[TelegramTransportAdapter] = None,
) -> OutboxProcessResult:
    """
    Authoritative Transactional Outbox processor.
    Guarantees:
      - Atomic lease acquisition via status CAS transition (PENDING/RETRY_PENDING/FAILED -> PROCESSING).
      - Skip locked concurrency protection on supported SQL backends.
      - Worker crash recovery (reclaims stale PROCESSING rows past stale_threshold).
      - Exponential backoff retry calculation on transient failures.
      - Dead-letter FAILED / FAILED_PERMANENT status on exceeding max_retries.
      - Strict provider validation and routing matrix: unknown provider fails closed.
      - Never sets sent_at on failed delivery.
    """
    now = datetime.now(timezone.utc)
    stale_threshold = now - timedelta(seconds=stale_timeout_seconds)

    # Query pending, retryable failed, or stale crashed PROCESSING records
    query = db.query(NotificationOutbox).filter(
        or_(
            and_(
                NotificationOutbox.status.in_(["PENDING", "RETRY_PENDING", "FAILED"]),
                NotificationOutbox.next_retry_at <= now
            ),
            and_(
                NotificationOutbox.status == "PROCESSING",
                NotificationOutbox.next_retry_at <= stale_threshold
            )
        ),
        NotificationOutbox.retry_count < NotificationOutbox.max_retries
    ).order_by(NotificationOutbox.created_at.asc()).limit(batch_size)

    # Concurrency locking: PostgreSQL supports SELECT ... FOR UPDATE SKIP LOCKED
    try:
        bind = db.get_bind()
        if bind and bind.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
    except Exception as e:
        logger.debug("Dialect lock skip: %s", e)

    pending_records = query.all()

    processed_count = 0
    success_count = 0
    failed_count = 0
    details = []

    for item in pending_records:
        # Atomic CAS claim guard:
        # Record must still be PENDING/RETRY_PENDING/FAILED or stale PROCESSING
        claim_filter = or_(
            NotificationOutbox.status.in_(["PENDING", "RETRY_PENDING", "FAILED"]),
            and_(
                NotificationOutbox.status == "PROCESSING",
                NotificationOutbox.next_retry_at <= stale_threshold
            )
        )
        rows_claimed = db.query(NotificationOutbox).filter(
            NotificationOutbox.id == item.id,
            claim_filter
        ).update(
            {"status": "PROCESSING", "next_retry_at": now + timedelta(seconds=stale_timeout_seconds)},
            synchronize_session="fetch"
        )
        db.commit()

        if rows_claimed == 0:
            # Concurrently claimed by another worker or already finalized
            continue

        processed_count += 1
        attempt_num = item.retry_count + 1
        is_success = False
        error_msg = None
        http_code = 200
        telegram_metadata = None

        try:
            # Check for simulated failure flag in payload or settings (for deterministic QA testing)
            force_fail = (
                settings.NOTIFICATION_PROVIDER == "mock_fail"
                or (isinstance(item.payload_json, dict) and item.payload_json.get("force_fail") is True)
            )

            # Recipient validation guard (Case E)
            if item.channel == "EMAIL" and ("@" not in item.recipient or "." not in item.recipient):
                is_success = False
                http_code = 422
                error_msg = f"Invalid email format: '{item.recipient}'"
            elif item.channel == "TELEGRAM" and not item.recipient.strip():
                is_success = False
                http_code = 422
                error_msg = "Telegram recipient identifier cannot be empty"
            elif force_fail:
                is_success = False
                http_code = 503
                error_msg = "Simulated upstream notification dispatch failure"
            elif settings.NOTIFICATION_PROVIDER == "mock":
                # Deterministic simulated delivery for demo / test mode
                is_success = True
                http_code = 250
            elif settings.NOTIFICATION_PROVIDER == "smtp":
                if item.channel == "EMAIL":
                    adapter = smtp_transport or SMTPTransportAdapter()
                    is_success, http_code, error_msg = adapter.send_notification(
                        recipient=item.recipient,
                        subject=item.subject,
                        payload=item.payload_json
                    )
                else:
                    is_success = False
                    http_code = 400
                    error_msg = f"Routing mismatch: channel '{item.channel}' is not supported by SMTP provider."
            elif settings.NOTIFICATION_PROVIDER == "telegram":
                if item.channel == "TELEGRAM":
                    tg_adapter = telegram_transport or TelegramTransportAdapter()
                    is_success, http_code, error_msg, telegram_metadata = tg_adapter.send_notification(
                        recipient=item.recipient,
                        subject=item.subject,
                        payload=item.payload_json
                    )
                else:
                    is_success = False
                    http_code = 400
                    error_msg = f"Routing mismatch: channel '{item.channel}' is not supported by Telegram provider."
            else:
                # Strictly fail closed on unknown provider (NOTIF-01)
                is_success = False
                http_code = 500
                error_msg = f"Unknown notification provider: '{settings.NOTIFICATION_PROVIDER}'"
        except Exception as e:
            is_success = False
            http_code = 500
            error_msg = str(e)

        # Record Attempt Audit
        attempt = NotificationAttempt(
            outbox_id=item.id,
            attempt_number=attempt_num,
            status="SUCCESS" if is_success else "ERROR",
            response_code=http_code,
            error_message=error_msg,
            attempted_at=datetime.now(timezone.utc)
        )
        db.add(attempt)

        if is_success:
            delivered_status = "MOCK_DELIVERED" if settings.NOTIFICATION_PROVIDER == "mock" else "PROVIDER_ACCEPTED"
            item.status = delivered_status
            item.sent_at = datetime.now(timezone.utc)
            item.last_error = None
            if telegram_metadata and isinstance(item.payload_json, dict):
                p_copy = dict(item.payload_json)
                p_copy["provider_metadata"] = telegram_metadata
                item.payload_json = p_copy
            success_count += 1
            details.append({
                "id": item.id,
                "status": delivered_status,
                "delivery_state": delivered_status,
                "recipient": item.recipient,
            })
        else:
            item.sent_at = None  # Never set sent_at on failure
            item.retry_count += 1
            item.last_error = error_msg
            if item.retry_count >= item.max_retries:
                item.status = "FAILED"
                delivery_state = "FAILED_PERMANENT"
            else:
                item.status = "PENDING"
                delivery_state = "RETRY_PENDING"
                # Exponential backoff: 10s * 2^(retry_count - 1): 10s, 20s, 40s, 80s...
                delay_seconds = 10 * (2 ** (item.retry_count - 1))
                item.next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
            failed_count += 1
            details.append({
                "id": item.id,
                "status": item.status,
                "delivery_state": delivery_state,
                "error": error_msg,
                "retry_count": item.retry_count,
            })

        db.commit()

    return OutboxProcessResult(
        processed_count=processed_count,
        success_count=success_count,
        failed_count=failed_count,
        details=details
    )
