from typing import List
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from core.database import get_db
from core.models import NotificationOutbox
from core.schemas import NotificationOutboxResponse, OutboxProcessResult
from core.services.outbox_service import process_outbox

router = APIRouter()

@router.post("/process", response_model=OutboxProcessResult)
def flush_outbox(batch_size: int = Query(10, ge=1, le=100), db: Session = Depends(get_db)):
    """Flushes pending notifications from outbox with exponential backoff and error logging."""
    return process_outbox(db, batch_size=batch_size)

@router.get("/outbox", response_model=List[NotificationOutboxResponse])
def get_outbox_queue(
    status: str = Query("PENDING", description="Filter by status: PENDING, PROCESSING, MOCK_DELIVERED, PROVIDER_ACCEPTED, FAILED, SENT"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db)
):
    """Inspects outbox notifications queue with support for MOCK_DELIVERED and PROVIDER_ACCEPTED."""
    query = db.query(NotificationOutbox)
    if status.upper() == "SENT":
        # 'SENT' serves as a unified filter covering both simulated and live successful deliveries
        query = query.filter(NotificationOutbox.status.in_(["SENT", "MOCK_DELIVERED", "PROVIDER_ACCEPTED"]))
    else:
        query = query.filter(NotificationOutbox.status == status)
    return query.order_by(NotificationOutbox.created_at.desc()).limit(limit).all()
