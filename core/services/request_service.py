import re
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from core.models import Customer, ServiceRequest, RequestStatusHistory, NotificationOutbox, AuditEvent
from core.schemas import ServiceRequestCreate, ServiceRequestStatusUpdate
from core.config import settings

def normalize_phone(phone: str) -> str:
    cleaned = re.sub(r"[^\d+]", "", phone.strip())
    if not cleaned.startswith("+") and cleaned.startswith("380"):
        cleaned = "+" + cleaned
    elif not cleaned.startswith("+") and cleaned.startswith("0"):
        cleaned = "+38" + cleaned
    return cleaned

def create_service_request(db: Session, req: ServiceRequestCreate) -> ServiceRequest:
    norm_phone = normalize_phone(req.phone)

    # Check if request with identical source_row_id already exists (idempotency)
    if req.source_row_id:
        existing_req = db.query(ServiceRequest).filter(
            ServiceRequest.source_row_id == req.source_row_id
        ).first()
        if existing_req:
            return existing_req

    # 1. Deduplicate / Find or Create Customer
    customer = db.query(Customer).filter(Customer.phone == norm_phone).first()
    if not customer:
        customer = Customer(
            full_name=req.full_name.strip(),
            phone=norm_phone,
            email=req.email.strip() if req.email else None,
            telegram_chat_id=req.telegram_chat_id
        )
        db.add(customer)
        db.flush() # obtain customer.id
    else:
        # Update email/name if provided and missing
        if req.email and not customer.email:
            customer.email = req.email.strip()
        if req.telegram_chat_id and not customer.telegram_chat_id:
            customer.telegram_chat_id = req.telegram_chat_id

    # 2. Create Service Request
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

    # 3. Create Status History Entry
    history = RequestStatusHistory(
        request_id=service_req.id,
        old_status=None,
        new_status="NEW",
        changed_by="system",
        comment=f"Заявку створено через канал {req.source_channel}"
    )
    db.add(history)

    # 4. Transactional Outbox (Atomically enqueued)
    outbox_payload = {
        "request_id": service_req.id,
        "customer_name": customer.full_name,
        "customer_phone": customer.phone,
        "purpose": service_req.request_purpose,
        "property_type": service_req.property_type,
        "location": service_req.property_location,
        "consultation_date": str(service_req.consultation_date)
    }

    # 4a. Client confirmation (Enqueued ONLY if a verified delivery channel is provided)
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
    else:
        # Zero unverified/hallucinated recipient generation
        pass

    # 4b. Admin notification (preserving LR4 business contract, sent to configured ADMIN_EMAIL)
    admin_entry = NotificationOutbox(
        channel="EMAIL",
        recipient=settings.ADMIN_EMAIL,
        subject=f"Нова заявка на консультацію #{service_req.id}: {customer.full_name} ({service_req.property_type})",
        payload_json=outbox_payload,
        status="PENDING",
        retry_count=0
    )
    db.add(admin_entry)

    # 5. Audit Event
    audit = AuditEvent(
        event_type="REQUEST_CREATED",
        entity_type="service_requests",
        entity_id=service_req.id,
        details_json={"customer_id": customer.id, "source": req.source_channel}
    )
    db.add(audit)

    db.commit()
    db.refresh(service_req)
    return service_req

def update_request_status(db: Session, request_id: int, update_data: ServiceRequestStatusUpdate) -> ServiceRequest:
    service_req = db.query(ServiceRequest).filter(ServiceRequest.id == request_id).first()
    if not service_req:
        raise ValueError(f"Request with ID {request_id} not found")

    old_status = service_req.status
    if old_status == update_data.new_status:
        return service_req

    service_req.status = update_data.new_status
    service_req.updated_at = datetime.now(timezone.utc)

    history = RequestStatusHistory(
        request_id=service_req.id,
        old_status=old_status,
        new_status=update_data.new_status,
        changed_by=update_data.changed_by,
        comment=update_data.comment
    )
    db.add(history)

    audit = AuditEvent(
        event_type="STATUS_CHANGED",
        entity_type="service_requests",
        entity_id=service_req.id,
        details_json={"old_status": old_status, "new_status": update_data.new_status}
    )
    db.add(audit)

    db.commit()
    db.refresh(service_req)
    return service_req
