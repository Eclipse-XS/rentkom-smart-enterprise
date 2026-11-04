from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from core.database import get_db
from core.models import ServiceRequest, Customer
from core.schemas import ServiceRequestCreate, ServiceRequestResponse, ServiceRequestStatusUpdate
from core.services.request_service import create_service_request, update_request_status
from core.security import require_admin_auth, AuthenticatedActor, check_public_rate_limit

router = APIRouter()


@router.post("/", response_model=ServiceRequestResponse, status_code=status.HTTP_201_CREATED)
def submit_request(req_in: ServiceRequestCreate, db: Session = Depends(get_db), _rate_limit: None = Depends(check_public_rate_limit)):
    """Public customer ingress: Idempotently creates a new consultation request, resolves customer, and enqueues outbox message."""
    try:
        return create_service_request(db, req_in)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.get("/", response_model=List[ServiceRequestResponse])
def list_requests(
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by status: NEW, IN_REVIEW, SCHEDULED, COMPLETED, CANCELLED"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    admin: AuthenticatedActor = Depends(require_admin_auth),
    db: Session = Depends(get_db)
):
    """Protected administrative endpoint: Lists consultation requests with customer PII."""
    q = db.query(ServiceRequest)
    if status_filter:
        q = q.filter(ServiceRequest.status == status_filter)
    return q.order_by(ServiceRequest.created_at.desc()).offset(offset).limit(limit).all()


@router.get("/{request_id}", response_model=ServiceRequestResponse)
def get_request(
    request_id: int,
    admin: AuthenticatedActor = Depends(require_admin_auth),
    db: Session = Depends(get_db)
):
    """Protected administrative endpoint: Retrieves a single request with customer details by ID."""
    req = db.query(ServiceRequest).filter(ServiceRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")
    return req


@router.patch("/{request_id}/status", response_model=ServiceRequestResponse)
def change_status(
    request_id: int,
    update_data: ServiceRequestStatusUpdate,
    admin: AuthenticatedActor = Depends(require_admin_auth),
    db: Session = Depends(get_db)
):
    """Protected administrative endpoint: Formally transitions request to a new status with actor audit logging."""
    try:
        return update_request_status(db, request_id, update_data, actor=admin.actor_id)
    except LookupError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
