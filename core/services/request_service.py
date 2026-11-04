import re
import uuid
import time
import hashlib
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from core.models import Customer, ServiceRequest, RequestStatusHistory, NotificationOutbox, AuditEvent, InboundLeadSubmission
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


def compute_canonical_payload_hash(
    intake_channel: str,
    external_event_id: Optional[str],
    normalized_phone: str,
    full_name: str,
    request_purpose: str,
    property_type: str,
    property_location: str,
    consultation_date: str,
    additional_comments: Optional[str] = None,
    email: Optional[str] = None,
) -> str:
    """Computes a deterministic SHA-256 fingerprint over normalized canonical request attributes."""
    norm_channel = (intake_channel or "").strip().upper()
    norm_event = (external_event_id or "").strip()
    norm_phone = (normalized_phone or "").strip()
    norm_name = " ".join((full_name or "").strip().split())
    norm_email = (email or "").strip().lower()
    norm_purpose = (request_purpose or "").strip()
    norm_prop = (property_type or "").strip()
    norm_loc = " ".join((property_location or "").strip().lower().split())
    norm_date = str(consultation_date).strip()
    norm_comments = (additional_comments or "").strip()

    raw = f"{norm_channel}|{norm_event}|{norm_phone}|{norm_name}|{norm_email}|{norm_purpose}|{norm_prop}|{norm_loc}|{norm_date}|{norm_comments}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _verify_idempotent_payload_match(existing_req: ServiceRequest, req: ServiceRequestCreate, norm_phone: str) -> None:
    """
    Verifies that a repeated request with the same source_row_id carries a compatible payload.
    Raises ValueError (mapped to HTTP 409 Conflict) if the replay attempts to mutate core attributes.
    """
    existing_phone = existing_req.customer.phone if existing_req.customer else None
    existing_email = (existing_req.customer.email or "").strip().lower() if existing_req.customer else ""
    new_email = (req.email or "").strip().lower()

    existing_loc = " ".join((existing_req.property_location or "").strip().lower().split())
    new_loc = " ".join((req.property_location or "").strip().lower().split())

    existing_date = str(existing_req.consultation_date)
    new_date = str(req.consultation_date)

    existing_name = " ".join((existing_req.customer.full_name or "").strip().split()) if existing_req.customer else ""
    new_name = " ".join((req.full_name or "").strip().split())

    existing_notes = (existing_req.additional_comments or "").strip()
    new_notes = (req.additional_comments or "").strip()

    if (
        (existing_phone and existing_phone != norm_phone)
        or (existing_name and existing_name != new_name)
        or (existing_email != new_email)
        or (existing_req.request_purpose != req.request_purpose)
        or (existing_req.property_type != req.property_type)
        or (existing_loc != new_loc)
        or (existing_date != new_date)
        or (existing_notes != new_notes)
        or (existing_req.source_channel.upper() != (req.source_channel or "REST_API").upper())
    ):
        raise ValueError(
            f"Idempotency conflict: source_row_id '{req.source_row_id}' already exists with a different payload."
        )


def create_lead_submission(db: Session, req: ServiceRequestCreate) -> InboundLeadSubmission:
    """
    Public customer ingress: stores an unverified lead staging record without touching
    or modifying confirmed customer profiles or leaking existing customer PII.
    """
    norm_phone = normalize_phone(req.phone)
    norm_channel = (req.source_channel or "REST_API").strip().upper()
    event_id = req.source_row_id.strip() if req.source_row_id else None

    norm_email = (req.email or "").strip().lower() or None
    payload_hash = compute_canonical_payload_hash(
        intake_channel=norm_channel,
        external_event_id=event_id,
        normalized_phone=norm_phone,
        full_name=req.full_name,
        request_purpose=req.request_purpose,
        property_type=req.property_type,
        property_location=req.property_location,
        consultation_date=str(req.consultation_date),
        additional_comments=req.additional_comments,
        email=norm_email,
    )

    # 1. Idempotency check: if event_id is supplied
    if event_id:
        existing_lead = db.query(InboundLeadSubmission).filter(
            InboundLeadSubmission.intake_channel == norm_channel,
            InboundLeadSubmission.external_event_id == event_id,
        ).first()
        if existing_lead:
            if existing_lead.payload_hash == payload_hash:
                return existing_lead
            raise ValueError(
                f"Idempotency conflict: source_row_id '{req.source_row_id}' already exists with a different payload."
            )

    # 2. Persist unverified lead
    submission_id = str(uuid.uuid4())
    lead = InboundLeadSubmission(
        submission_id=submission_id,
        intake_channel=norm_channel,
        external_event_id=event_id,
        payload_hash=payload_hash,
        full_name=req.full_name.strip(),
        phone=req.phone.strip(),
        normalized_phone=norm_phone,
        email=req.email.strip() if req.email else None,
        telegram_chat_id=req.telegram_chat_id,
        request_purpose=req.request_purpose,
        property_type=req.property_type,
        property_location=req.property_location.strip(),
        consultation_date=req.consultation_date,
        additional_comments=req.additional_comments.strip() if req.additional_comments else None,
        status="UNVERIFIED",
    )

    try:
        with db.begin_nested():
            db.add(lead)
            db.flush()
    except IntegrityError:
        if event_id:
            for _ in range(10):
                existing_lead = db.query(InboundLeadSubmission).filter(
                    InboundLeadSubmission.intake_channel == norm_channel,
                    InboundLeadSubmission.external_event_id == event_id,
                ).first()
                if existing_lead:
                    if existing_lead.payload_hash == payload_hash:
                        return existing_lead
                    raise ValueError(
                        f"Idempotency conflict: source_row_id '{req.source_row_id}' already exists with a different payload."
                    )
                time.sleep(0.05)
        raise

    # 3. Staff alert outbox item (Admin notification only - no PII sent to untrusted submitted destination)
    admin_entry = NotificationOutbox(
        channel="EMAIL",
        recipient=settings.ADMIN_EMAIL,
        subject=f"Новий вхідний лід #{submission_id[:8]}: {lead.full_name} ({lead.property_type})",
        payload_json={
            "submission_id": submission_id,
            "name": lead.full_name,
            "phone": lead.normalized_phone,
            "purpose": lead.request_purpose,
            "property_type": lead.property_type,
            "location": lead.property_location,
            "consultation_date": str(lead.consultation_date),
        },
        status="PENDING",
        retry_count=0
    )
    db.add(admin_entry)

    # 4. Audit event
    audit = AuditEvent(
        event_type="LEAD_SUBMITTED",
        entity_type="inbound_lead_submissions",
        entity_id=lead.id,
        details_json={"submission_id": submission_id, "channel": norm_channel}
    )
    db.add(audit)
    db.commit()
    db.refresh(lead)
    return lead


def promote_lead_submission(
    db: Session,
    submission_id: str,
    actor: str = "admin",
    review_notes: Optional[str] = None
) -> ServiceRequest:
    """
    Atomic, authorized promotion of an unverified lead into verified ServiceRequest and Customer.
    Preserves confirmed customer records and prevents contact hijacking.
    """
    lead = db.query(InboundLeadSubmission).filter(
        InboundLeadSubmission.submission_id == submission_id
    ).with_for_update().first()

    if not lead:
        raise LookupError(f"Inbound lead submission '{submission_id}' not found.")

    if lead.status == "PROMOTED":
        if lead.promoted_request_id:
            existing_req = db.query(ServiceRequest).filter(
                ServiceRequest.id == lead.promoted_request_id
            ).first()
            if existing_req:
                return existing_req

    if lead.status == "REJECTED":
        raise ValueError(f"Cannot promote lead '{submission_id}' because it has been REJECTED.")

    norm_phone = lead.normalized_phone

    # 1. Concurrency-safe customer resolution
    customer = db.query(Customer).filter(Customer.phone == norm_phone).first()
    if not customer:
        try:
            with db.begin_nested():
                customer = Customer(
                    full_name=lead.full_name,
                    phone=norm_phone,
                    email=lead.email,
                    telegram_chat_id=lead.telegram_chat_id
                )
                db.add(customer)
                db.flush()
        except IntegrityError:
            customer = db.query(Customer).filter(Customer.phone == norm_phone).first()

    # NOTE: Never overwrite existing confirmed customer contact details from unverified leads
    # customer.email and customer.telegram_chat_id remain untouched if already set or unverified

    # 2. Create ServiceRequest
    service_req = ServiceRequest(
        customer_id=customer.id,
        request_purpose=lead.request_purpose,
        property_type=lead.property_type,
        property_location=lead.property_location,
        consultation_date=lead.consultation_date,
        additional_comments=lead.additional_comments,
        status="NEW",
        source_channel=lead.intake_channel,
        source_row_id=lead.external_event_id,
    )
    db.add(service_req)
    db.flush()

    # 3. Create Status History Entry
    history = RequestStatusHistory(
        request_id=service_req.id,
        old_status=None,
        new_status="NEW",
        changed_by=actor,
        comment=f"Лід {lead.submission_id} верифіковано та просунуто оператором {actor}",
    )
    db.add(history)

    # 4. Outbox notifications
    outbox_payload = {
        "request_id": service_req.id,
        "customer_name": customer.full_name,
        "customer_phone": customer.phone,
        "purpose": service_req.request_purpose,
        "property_type": service_req.property_type,
        "location": service_req.property_location,
        "consultation_date": str(service_req.consultation_date),
    }

    # Client notification ONLY sent to verified customer contact, not unverified attacker contact
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

    # Admin notification
    admin_entry = NotificationOutbox(
        channel="EMAIL",
        recipient=settings.ADMIN_EMAIL,
        subject=f"Нова заявка на консультацію #{service_req.id}: {customer.full_name} ({service_req.property_type})",
        payload_json=outbox_payload,
        status="PENDING",
        retry_count=0
    )
    db.add(admin_entry)

    # 5. Update lead status
    lead.status = "PROMOTED"
    lead.promoted_request_id = service_req.id
    lead.reviewed_by = actor
    lead.reviewed_at = datetime.now(timezone.utc)
    lead.review_notes = review_notes

    # 6. Audit event
    audit = AuditEvent(
        event_type="LEAD_PROMOTED",
        entity_type="inbound_lead_submissions",
        entity_id=lead.id,
        details_json={
            "submission_id": lead.submission_id,
            "promoted_request_id": service_req.id,
            "actor": actor
        }
    )
    db.add(audit)
    db.commit()
    db.refresh(service_req)
    return service_req


def create_service_request(db: Session, req: ServiceRequestCreate, actor: str = "system") -> ServiceRequest:
    """
    Concurrency-safe, atomic service request creation and customer deduplication.
    Authorized internal/admin path:
      1. Idempotent check against duplicate source_row_id (rejecting conflicting payload replays).
      2. Exactly one customer identity per canonical phone number using savepoint collision recovery.
      3. Preserves existing customer email and telegram_chat_id against unverified overwrites.
      4. Atomic persistence of Customer, ServiceRequest, RequestStatusHistory, NotificationOutbox, and AuditEvent.
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
            customer = db.query(Customer).filter(Customer.phone == norm_phone).first()

    if not customer:
        raise RuntimeError("Failed to resolve customer record during request creation.")

    # SECURITY FIX (SEC-02): Do NOT modify existing customer.email or customer.telegram_chat_id
    # Unverified input must never overwrite or populate verified customer contacts.

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
        changed_by=actor,
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

    # 5a. Client confirmation (Enqueued ONLY to customer's verified contact destination)
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
            details_json={"customer_id": customer.id, "source": req.source_channel, "actor": actor}
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
    Formal state machine transition with audit trail and row-level locking (DATA-02).
    Enforces permitted status transitions and records the authenticated actor.
    """
    service_req = db.query(ServiceRequest).filter(
        ServiceRequest.id == request_id
    ).with_for_update().first()

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

    # Atomic CAS status transition to eliminate concurrent status overwrite race (DATA-02)
    rows_updated = db.query(ServiceRequest).filter(
        ServiceRequest.id == request_id,
        ServiceRequest.status == old_status
    ).update(
        {"status": target_status, "updated_at": datetime.now(timezone.utc)},
        synchronize_session="fetch"
    )
    if rows_updated == 0:
        db.rollback()
        current_req = db.query(ServiceRequest).filter_by(id=request_id).first()
        cur_status = current_req.status if current_req else "UNKNOWN"
        raise ValueError(
            f"Concurrent status modification conflict: status was changed to '{cur_status}' by another transaction."
        )

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
