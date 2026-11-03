import re
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from core.models import Customer, ServiceRequest, RequestStatusHistory, NotificationOutbox, AuditEvent
from core.schemas import ServiceRequestCreate, ServiceRequestStatusUpdate
from core.config import settings

VALID_STATUS_TRANSITIONS = {
    "NEW": {"IN_REVIEW", "CANCELLED"},
    "IN_REVIEW": {"SCHEDULED", "CANCELLED"},
    "SCHEDULED": {"COMPLETED", "CANCELLED"},
    "COMPLETED": set(),  # Terminal state
    "CANCELLED": set(),  # Terminal state
}


def normalize_phone(phone: str) -> str:
    """
    Normalizes a Ukrainian or international phone number into canonical E.164-compatible string (+380...).
    Strips non-digits, converts local trunk prefixes (0xx, 80xx) to canonical +380xx.
    """
    cleaned = re.sub(r"[^\d+]", "", phone.strip())
    if not cleaned.startswith("+"):
        if cleaned.startswith("380"):
            cleaned = "+" + cleaned
        elif cleaned.startswith("0") and len(cleaned) == 10:
            cleaned = "+38" + cleaned
        elif cleaned.startswith("80") and len(cleaned) == 11:
            cleaned = "+3" + cleaned
        else:
            cleaned = "+" + cleaned

    digits_only = re.sub(r"\D", "", cleaned)
    if len(digits_only) < 9 or len(digits_only) > 15:
        raise ValueError(f"Invalid phone number length: '{phone}' (must contain 9-15 digits)")
    return cleaned


def _verify_idempotent_payload_match(existing_req: ServiceRequest, req: ServiceRequestCreate, norm_phone: str) -> None:
    """
    Verifies that a repeated request with the same source_row_id carries a compatible payload.
    Raises ValueError (mapped to HTTP 409 Conflict) if the replay attempts to mutate core attributes.
    """
    existing_phone = existing_req.customer.phone if existing_req.customer else None
    if (
        (existing_phone and existing_phone != norm_phone)
        or existing_req.request_purpose != req.request_purpose
        or existing_req.property_type != req.property_type
    ):
        raise ValueError(
            f"Idempotency conflict: source_row_id '{req.source_row_id}' already exists with a different payload."
        )


def create_service_request(db: Session, req: ServiceRequestCreate) -> ServiceRequest:
    """
    Concurrency-safe, atomic service request creation and customer deduplication.
    Guarantees:
      1. Idempotent check against duplicate source_row_id (rejecting conflicting payload replays).
      2. Exactly one customer identity per canonical phone number using savepoint collision recovery.
      3. Atomic persistence of Customer, ServiceRequest, RequestStatusHistory, NotificationOutbox, and AuditEvent.
    """
    norm_phone = normalize_phone(req.phone)

    # 1. Check if request with identical source_row_id already exists (idempotency)
    if req.source_row_id:
        existing_req = db.query(ServiceRequest).filter(
            ServiceRequest.source_row_id == req.source_row_id
        ).first()
        if existing_req:
            _verify_idempotent_payload_match(existing_req, req, norm_phone)
            return existing_req

    # 2. Concurrency-safe customer resolution (find or create with savepoint rollback)
    customer = db.query(Customer).filter(Customer.phone == norm_phone).first()
    if not customer:
        try:
            with db.begin_nested():
                customer = Customer(
                    full_name=req.full_name.strip(),
                    phone=norm_phone,
                    email=req.email.strip() if req.email else None,
                    telegram_chat_id=req.telegram_chat_id
                )
                db.add(customer)
                db.flush()
        except IntegrityError:
            # Concurrent worker created the customer with this phone simultaneously
            customer = db.query(Customer).filter(Customer.phone == norm_phone).first()

    if not customer:
        raise RuntimeError("Failed to resolve customer record during request creation.")

    # Update customer email or telegram_chat_id if provided and not yet populated
    if req.email and not customer.email:
        customer.email = req.email.strip()
    if req.telegram_chat_id and not customer.telegram_chat_id:
        customer.telegram_chat_id = req.telegram_chat_id

    # 3. Create Service Request (with savepoint guard against concurrent identical source_row_id)
    service_req = None
    try:
        with db.begin_nested():
            service_req = ServiceRequest(
                customer_id=customer.id,
                request_purpose=req.request_purpose,
                property_type=req.property_type,
                property_location=req.property_location.strip(),
                consultation_date=req.consultation_date,
                additional_comments=req.additional_comments.strip() if req.additional_comments else None,
                status="NEW",
                source_channel=req.source_channel,
                source_row_id=req.source_row_id
            )
            db.add(service_req)
            db.flush()
    except IntegrityError:
        if req.source_row_id:
            existing_req = db.query(ServiceRequest).filter(
                ServiceRequest.source_row_id == req.source_row_id
            ).first()
            if existing_req:
                _verify_idempotent_payload_match(existing_req, req, norm_phone)
                return existing_req
        raise

    # 4. Create Status History Entry
    history = RequestStatusHistory(
        request_id=service_req.id,
        old_status=None,
        new_status="NEW",
        changed_by="system",
        comment=f"Заявку створено через канал {req.source_channel}"
    )
    db.add(history)

    # 5. Transactional Outbox (Atomically enqueued)
    outbox_payload = {
        "request_id": service_req.id,
        "customer_name": customer.full_name,
        "customer_phone": customer.phone,
        "purpose": service_req.request_purpose,
        "property_type": service_req.property_type,
        "location": service_req.property_location,
        "consultation_date": str(service_req.consultation_date)
    }

    # 5a. Client confirmation (Enqueued ONLY if a verified delivery channel is provided)
    if customer.email and "@" in customer.email and "." in customer.email:
        client_outbox = NotificationOutbox(
            channel="EMAIL",
            recipient=customer.email,
            subject=f"Підтвердження консультації: Агентство нерухомості «Рентком» (Заявка #{service_req.id})",
            payload_json=outbox_payload,
            status="PENDING",
            retry_count=0
        )
        db.add(client_outbox)
    elif customer.telegram_chat_id:
        client_outbox = NotificationOutbox(
            channel="TELEGRAM",
            recipient=str(customer.telegram_chat_id),
            subject=f"Підтвердження консультації: Агентство нерухомості «Рентком» (Заявка #{service_req.id})",
            payload_json=outbox_payload,
            status="PENDING",
            retry_count=0
        )
        db.add(client_outbox)

    # 5b. Admin notification
    admin_entry = NotificationOutbox(
        channel="EMAIL",
        recipient=settings.ADMIN_EMAIL,
        subject=f"Нова заявка на консультацію #{service_req.id}: {customer.full_name} ({service_req.property_type})",
        payload_json=outbox_payload,
        status="PENDING",
        retry_count=0
    )
    db.add(admin_entry)

    # 6. Audit Event
    try:
        audit = AuditEvent(
            event_type="REQUEST_CREATED",
            entity_type="service_requests",
            entity_id=service_req.id,
            details_json={"customer_id": customer.id, "source": req.source_channel}
        )
        db.add(audit)

        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(service_req)
    return service_req


def update_request_status(
    db: Session,
    request_id: int,
    update_data: ServiceRequestStatusUpdate,
    actor: str = "operator"
) -> ServiceRequest:
    """
    Formal state machine transition with audit trail.
    Enforces permitted status transitions and records the authenticated actor.
    """
    service_req = db.query(ServiceRequest).filter(ServiceRequest.id == request_id).first()
    if not service_req:
        raise LookupError(f"Service request with ID {request_id} not found")

    old_status = service_req.status
    target_status = update_data.new_status

    if target_status not in VALID_STATUS_TRANSITIONS:
        raise ValueError(f"Unknown status '{target_status}'. Allowed statuses: {sorted(VALID_STATUS_TRANSITIONS.keys())}")

    if old_status == target_status:
        return service_req

    allowed_next_states = VALID_STATUS_TRANSITIONS.get(old_status, set())
    if target_status not in allowed_next_states:
        allowed_list = sorted(list(allowed_next_states)) if allowed_next_states else "None (terminal state)"
        raise ValueError(
            f"Invalid status transition from '{old_status}' to '{target_status}'. "
            f"Permitted next states from '{old_status}': {allowed_list}."
        )

    service_req.status = target_status
    service_req.updated_at = datetime.now(timezone.utc)

    # Record status history with authenticated actor
    history = RequestStatusHistory(
        request_id=service_req.id,
        old_status=old_status,
        new_status=target_status,
        changed_by=actor,
        comment=update_data.comment
    )
    db.add(history)

    # Record audit event
    audit = AuditEvent(
        event_type="STATUS_CHANGED",
        entity_type="service_requests",
        entity_id=service_req.id,
        details_json={
            "old_status": old_status,
            "new_status": target_status,
            "actor": actor,
            "comment": update_data.comment
        }
    )
    db.add(audit)

    db.commit()
    db.refresh(service_req)
    return service_req
