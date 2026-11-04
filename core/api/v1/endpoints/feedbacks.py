from typing import List
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session
from core.database import get_db
from core.models import CustomerFeedback
from core.schemas import CustomerFeedbackCreate, CustomerFeedbackResponse
from core.services.feedback_service import create_customer_feedback
from core.security import require_admin_auth, AuthenticatedActor, check_public_rate_limit

router = APIRouter()

@router.post("/", response_model=CustomerFeedbackResponse, status_code=status.HTTP_201_CREATED)
def submit_feedback(fb_in: CustomerFeedbackCreate, db: Session = Depends(get_db), _rate_limit: None = Depends(check_public_rate_limit)):
    """Public customer ingress: Validates and stores client review/ratings with optional customer linkage."""
    return create_customer_feedback(db, fb_in)

@router.get("/", response_model=List[CustomerFeedbackResponse])
def list_feedbacks(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    admin: AuthenticatedActor = Depends(require_admin_auth),
    db: Session = Depends(get_db)
):
    """Protected administrative endpoint: Lists customer reviews and client identifiers."""
    return db.query(CustomerFeedback).order_by(CustomerFeedback.created_at.desc()).offset(offset).limit(limit).all()
