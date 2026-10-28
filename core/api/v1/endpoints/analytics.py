from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from core.database import get_db
from core.models import ServiceRequest, CustomerFeedback, NotificationOutbox
from core.schemas import AnalyticsSummary

router = APIRouter()

@router.get("/summary", response_model=AnalyticsSummary)
def get_analytics_summary(db: Session = Depends(get_db)):
    """Provides high-level aggregated operational and customer satisfaction metrics for Grafana."""
    # 1. Total Requests
    total_requests = db.query(ServiceRequest).count()

    # 2. Requests by Status
    status_counts = dict(
        db.query(ServiceRequest.status, func.count(ServiceRequest.id))
        .group_by(ServiceRequest.status).all()
    )

    # 3. Requests by Property Type
    property_counts = dict(
        db.query(ServiceRequest.property_type, func.count(ServiceRequest.id))
        .group_by(ServiceRequest.property_type).all()
    )

    # 4. Requests by Purpose
    purpose_counts = dict(
        db.query(ServiceRequest.request_purpose, func.count(ServiceRequest.id))
        .group_by(ServiceRequest.request_purpose).all()
    )

    # 5. Customer Feedback metrics
    total_feedbacks = db.query(CustomerFeedback).count()
    avg_service = db.query(func.avg(CustomerFeedback.service_rating)).scalar() or 0.0
    avg_realtor = db.query(func.avg(CustomerFeedback.realtor_rating)).scalar() or 0.0

    # 6. Outbox backlog
    pending_notifications = db.query(NotificationOutbox).filter(
        NotificationOutbox.status == "PENDING"
    ).count()

    return AnalyticsSummary(
        total_requests=total_requests,
        requests_by_status=status_counts,
        requests_by_property_type=property_counts,
        requests_by_purpose=purpose_counts,
        total_feedbacks=total_feedbacks,
        average_service_rating=round(float(avg_service), 2),
        average_realtor_rating=round(float(avg_realtor), 2),
        pending_notifications=pending_notifications
    )
