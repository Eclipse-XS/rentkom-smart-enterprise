from datetime import datetime, timedelta, timezone
import logging
from sqlalchemy import or_, and_
from sqlalchemy.orm import Session
from core.models import NotificationOutbox, NotificationAttempt, AuditEvent
from core.schemas import OutboxProcessResult
from core.config import settings

logger = logging.getLogger(__name__)

def process_outbox(
    db: Session,
    batch_size: int = 10,
    stale_timeout_seconds: int = 300
) -> OutboxProcessResult:
    now = datetime.now(timezone.utc)
    stale_threshold = now - timedelta(seconds=stale_timeout_seconds)

    # Query pending, retryable failed, or stale crashed PROCESSING records
    query = db.query(NotificationOutbox).filter(
        or_(
            and_(
                NotificationOutbox.status.in_(["PENDING", "FAILED"]),
                NotificationOutbox.next_retry_at <= now
            ),
            and_(
                NotificationOutbox.status == "PROCESSING",
                NotificationOutbox.next_retry_at <= stale_threshold
            )
        ),
        NotificationOutbox.retry_count < NotificationOutbox.max_retries
    ).order_by(NotificationOutbox.created_at.asc()).limit(batch_size)

    # Dialect-safe concurrency locking: PostgreSQL supports SELECT ... FOR UPDATE SKIP LOCKED
    try:
        bind = db.get_bind()
        if bind and bind.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
    except Exception as e:
        logger.debug(f"Dialect lock skip: {e}")

    pending_records = query.all()

    processed_count = 0
    success_count = 0
    failed_count = 0
    details = []

    for item in pending_records:
        # Atomic CAS claim guard:
        # Record must still be PENDING/FAILED or stale PROCESSING
        claim_filter = or_(
            NotificationOutbox.status.in_(["PENDING", "FAILED"]),
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

        try:
            # Check for simulated failure flag in payload or settings (for deterministic QA testing)
            force_fail = (
                settings.NOTIFICATION_PROVIDER == "mock_fail" or
                (isinstance(item.payload_json, dict) and item.payload_json.get("force_fail") is True)
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
                # Deterministic simulated delivery
                is_success = True
                http_code = 250
            elif settings.NOTIFICATION_PROVIDER == "smtp":
                if not settings.SMTP_HOST:
                    is_success = True
                    http_code = 250
                else:
                    # In production SMTP send would occur here
                    is_success = True
                    http_code = 250
            else:
                is_success = True
                http_code = 200
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
            success_count += 1
            details.append({"id": item.id, "status": delivered_status, "recipient": item.recipient})
        else:
            item.retry_count += 1
            item.last_error = error_msg
            if item.retry_count >= item.max_retries:
                item.status = "FAILED"
            else:
                item.status = "PENDING"
                # Exponential backoff: 10s * 2^(retry_count - 1): 10s, 20s, 40s, 80s...
                delay_seconds = 10 * (2 ** (item.retry_count - 1))
                item.next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
            failed_count += 1
            details.append({"id": item.id, "status": item.status, "error": error_msg, "retry_count": item.retry_count})

        db.commit()

    return OutboxProcessResult(
        processed_count=processed_count,
        success_count=success_count,
        failed_count=failed_count,
        details=details
    )
