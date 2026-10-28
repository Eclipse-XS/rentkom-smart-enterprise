from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from core.database import get_db
from core.models import ServiceRequest, Customer
from core.schemas import ServiceRequestCreate, ServiceRequestResponse, ServiceRequestStatusUpdate
from core.services.request_service import create_service_request, update_request_status

router = APIRouter()

@router.post("/", response_model=ServiceRequestResponse, status_code=201)
def submit_request(req_in: ServiceRequestCreate, db: Session = Depends(get_db)):
    """Idempotently creates a new consultation request, finds/creates customer, and enqueues outbox message."""
    return create_service_request(db, req_in)

@router.get("/", response_model=List[ServiceRequestResponse])
def list_requests(
    status: Optional[str] = Query(None, description="Filter by status: NEW, IN_REVIEW, SCHEDULED, COMPLETED, CANCELLED"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    """Lists requests with optional status filtering."""
    q = db.query(ServiceRequest)
    if status:
        q = q.filter(ServiceRequest.status == status)
    return q.order_by(ServiceRequest.created_at.desc()).offset(offset).limit(limit).all()

@router.get("/{request_id}", response_model=ServiceRequestResponse)
def get_request(request_id: int, db: Session = Depends(get_db)):
    """Retrieves a single request by ID."""
    req = db.query(ServiceRequest).filter(ServiceRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    return req

@router.patch("/{request_id}/status", response_model=ServiceRequestResponse)
def change_status(request_id: int, update_data: ServiceRequestStatusUpdate, db: Session = Depends(get_db)):
    """Transitions request to a new status with audit logging."""
    try:
        return update_request_status(db, request_id, update_data)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
