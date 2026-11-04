from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from core.database import get_db
from core.models import InboundLeadSubmission
from core.schemas import InboundLeadResponse, LeadPromotionRequest, ServiceRequestResponse
from core.services.request_service import promote_lead_submission
from core.security import require_admin_auth, AuthenticatedActor

router = APIRouter()


@router.get("/", response_model=List[InboundLeadResponse])
def list_leads(
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by status: UNVERIFIED, VERIFIED, REJECTED, PROMOTED"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    admin: AuthenticatedActor = Depends(require_admin_auth),
    db: Session = Depends(get_db)
):
    """Protected administrative endpoint: Lists inbound lead submissions for review."""
    q = db.query(InboundLeadSubmission)
    if status_filter:
        q = q.filter(InboundLeadSubmission.status == status_filter)
    return q.order_by(InboundLeadSubmission.created_at.desc()).offset(offset).limit(limit).all()


@router.get("/{submission_id}", response_model=InboundLeadResponse)
def get_lead(
    submission_id: str,
    admin: AuthenticatedActor = Depends(require_admin_auth),
    db: Session = Depends(get_db)
):
    """Protected administrative endpoint: Retrieves an inbound lead submission by UUID."""
    lead = db.query(InboundLeadSubmission).filter(
        InboundLeadSubmission.submission_id == submission_id
    ).first()
    if not lead:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead submission not found")
    return lead


@router.post("/{submission_id}/promote", response_model=ServiceRequestResponse, status_code=status.HTTP_200_OK)
def promote_lead(
    submission_id: str,
    body: Optional[LeadPromotionRequest] = None,
    admin: AuthenticatedActor = Depends(require_admin_auth),
    db: Session = Depends(get_db)
):
    """Protected administrative endpoint: Atomically promotes an unverified lead into a verified service request."""
    try:
        review_notes = body.review_notes if body else None
        return promote_lead_submission(
            db,
            submission_id=submission_id,
            actor=admin.actor_id,
            review_notes=review_notes
        )
    except LookupError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
