from typing import List, Optional, Union
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session
from core.database import get_db
from core.models import ServiceRequest
from core.schemas import (
    ServiceRequestCreate,
    ServiceRequestResponse,
    ServiceRequestStatusUpdate,
    LeadSubmissionAcknowledgment,
)
from core.services.request_service import (
    create_service_request,
    create_lead_submission,
    update_request_status,
)
from core.security import (
    require_admin_auth,
    get_optional_internal_or_admin_auth,
    AuthenticatedActor,
    check_public_rate_limit,
)

router = APIRouter()


@router.post(
    "/",
    response_model=Union[ServiceRequestResponse, LeadSubmissionAcknowledgment],
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        201: {"model": ServiceRequestResponse},
        202: {"model": LeadSubmissionAcknowledgment},
        409: {"description": "Conflict"},
    },
)
def submit_request(
    req_in: ServiceRequestCreate,
    response: Response,
    db: Session = Depends(get_db),
    actor: Optional[AuthenticatedActor] = Depends(get_optional_internal_or_admin_auth),
    _rate_limit: None = Depends(check_public_rate_limit),
):
    """
    Customer consultation intake:
    - Anonymous/public submissions return 202 Accepted with opaque submission_id and no customer PII (SEC-01).
    - Authorized internal/admin submissions return 201 Created with full ServiceRequestResponse.
    """
    try:
        if actor:
            response.status_code = status.HTTP_201_CREATED
            return create_service_request(db, req_in, actor=actor.actor_id)

        submission = create_lead_submission(db, req_in)
        response.status_code = status.HTTP_202_ACCEPTED
        return LeadSubmissionAcknowledgment(
            submission_id=submission.submission_id,
            status="RECEIVED",
            message="Request accepted for processing",
        )
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
        err_msg = str(e)
        if "conflict" in err_msg.lower():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=err_msg)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err_msg)
