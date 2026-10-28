from sqlalchemy.orm import Session
from core.models import CustomerFeedback, Customer, AuditEvent
from core.schemas import CustomerFeedbackCreate

def create_customer_feedback(db: Session, feedback_in: CustomerFeedbackCreate) -> CustomerFeedback:
    if feedback_in.source_row_id:
        existing = db.query(CustomerFeedback).filter(
            CustomerFeedback.source_row_id == feedback_in.source_row_id
        ).first()
        if existing:
            return existing

    # Try to match client_identifier to existing customer (by phone or email)
    matched_customer = db.query(Customer).filter(
        (Customer.phone == feedback_in.client_identifier) |
        (Customer.email == feedback_in.client_identifier)
    ).first()

    feedback = CustomerFeedback(
        customer_id=matched_customer.id if matched_customer else None,
        client_identifier=feedback_in.client_identifier.strip(),
        service_rating=feedback_in.service_rating,
        realtor_rating=feedback_in.realtor_rating,
        feedback_text=feedback_in.feedback_text.strip() if feedback_in.feedback_text else None,
        source_row_id=feedback_in.source_row_id,
        source_channel=feedback_in.source_channel
    )
    db.add(feedback)
    db.flush()

    audit = AuditEvent(
        event_type="FEEDBACK_RECEIVED",
        entity_type="customer_feedbacks",
        entity_id=feedback.id,
        details_json={
            "service_rating": feedback.service_rating,
            "realtor_rating": feedback.realtor_rating
        }
    )
    db.add(audit)
    db.commit()
    db.refresh(feedback)
    return feedback
