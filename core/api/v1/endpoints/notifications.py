from typing import List
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from core.database import get_db
from core.models import NotificationOutbox
from core.schemas import NotificationOutboxResponse, OutboxProcessResult
from core.services.outbox_service import process_outbox
from core.security import require_admin_auth, require_internal_or_admin_auth, AuthenticatedActor

router = APIRouter()


@router.post("/process", response_model=OutboxProcessResult)
def flush_outbox(
    batch_size: int = Query(10, ge=1, le=100),
    service_actor: AuthenticatedActor = Depends(require_internal_or_admin_auth),
    db: Session = Depends(get_db)
):
    """Protected service endpoint: Flushes pending notifications from outbox with exponential backoff and error logging."""
    return process_outbox(db, batch_size=batch_size)


@router.get("/outbox", response_model=List[NotificationOutboxResponse])
def get_outbox_queue(
    status: str = Query("PENDING", description="Filter by status: PENDING, PROCESSING, RETRY_PENDING, MOCK_DELIVERED, PROVIDER_ACCEPTED, FAILED, FAILED_PERMANENT, SENT"),
    limit: int = Query(50, ge=1, le=200),
    admin: AuthenticatedActor = Depends(require_admin_auth),
    db: Session = Depends(get_db)
):
    """Protected administrative endpoint: Inspects outbox notification queue, recipients, and dispatch payloads."""
    query = db.query(NotificationOutbox)
    upper_status = status.upper()
    if upper_status == "SENT":
        query = query.filter(NotificationOutbox.status.in_(["SENT", "MOCK_DELIVERED", "PROVIDER_ACCEPTED"]))
    elif upper_status == "PENDING":
        query = query.filter(NotificationOutbox.status.in_(["PENDING", "RETRY_PENDING"]))
    elif upper_status == "FAILED":
        query = query.filter(NotificationOutbox.status.in_(["FAILED", "FAILED_PERMANENT"]))
    else:
        query = query.filter(NotificationOutbox.status == status)
    return query.order_by(NotificationOutbox.created_at.desc()).limit(limit).all()
