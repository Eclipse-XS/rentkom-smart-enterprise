from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, BigInteger, String, Text, Date, DateTime, ForeignKey, JSON, CheckConstraint, func, UniqueConstraint
)
from sqlalchemy.orm import relationship
from core.database import Base

def utc_now():
    return datetime.now(timezone.utc)

class Customer(Base):
    __tablename__ = "customers"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    full_name = Column(String(255), nullable=False)
    phone = Column(String(50), nullable=False, unique=True, index=True)
    email = Column(String(255), nullable=True, index=True)
    telegram_chat_id = Column(BigInteger, nullable=True)
    created_at = Column(DateTime, default=utc_now, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=utc_now, server_default=func.now(), onupdate=utc_now, nullable=False)

    requests = relationship("ServiceRequest", back_populates="customer", cascade="all, delete-orphan")
    feedbacks = relationship("CustomerFeedback", back_populates="customer")

class ServiceRequest(Base):
    __tablename__ = "service_requests"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    customer_id = Column(BigInteger().with_variant(Integer, "sqlite"), ForeignKey("customers.id"), nullable=False, index=True)
    request_purpose = Column(String(50), nullable=False)  # Хочу купити, Хочу орендувати, etc.
    property_type = Column(String(50), nullable=False)    # Квартира, Будинок, etc.
    property_location = Column(Text, nullable=False)
    consultation_date = Column(Date, nullable=False)
    additional_comments = Column(Text, nullable=True)
    status = Column(String(50), default="NEW", nullable=False, index=True) # NEW, IN_REVIEW, SCHEDULED, COMPLETED, CANCELLED
    source_channel = Column(String(50), default="GOOGLE_FORMS", nullable=False) # GOOGLE_FORMS, TELEGRAM, REST_API, MANUAL
    source_row_id = Column(String(255), nullable=True, unique=True)
    created_at = Column(DateTime, default=utc_now, server_default=func.now(), nullable=False, index=True)
    updated_at = Column(DateTime, default=utc_now, server_default=func.now(), onupdate=utc_now, nullable=False)

    customer = relationship("Customer", back_populates="requests")
    status_history = relationship("RequestStatusHistory", back_populates="request", cascade="all, delete-orphan")

class RequestStatusHistory(Base):
    __tablename__ = "request_status_history"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    request_id = Column(BigInteger().with_variant(Integer, "sqlite"), ForeignKey("service_requests.id"), nullable=False, index=True)
    old_status = Column(String(50), nullable=True)
    new_status = Column(String(50), nullable=False)
    changed_by = Column(String(100), default="system", nullable=False)
    comment = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)

    request = relationship("ServiceRequest", back_populates="status_history")

class CustomerFeedback(Base):
    __tablename__ = "customer_feedbacks"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    customer_id = Column(BigInteger().with_variant(Integer, "sqlite"), ForeignKey("customers.id"), nullable=True)
    client_identifier = Column(String(255), nullable=False)
    service_rating = Column(Integer, nullable=False)  # 1-5
    realtor_rating = Column(Integer, nullable=False)  # 1-5
    feedback_text = Column(Text, nullable=True)
    source_row_id = Column(String(255), nullable=True, unique=True)
    source_channel = Column(String(50), default="GOOGLE_FORMS", nullable=False)
    created_at = Column(DateTime, default=utc_now, server_default=func.now(), nullable=False, index=True)

    customer = relationship("Customer", back_populates="feedbacks")

class NotificationOutbox(Base):
    __tablename__ = "notification_outbox"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    channel = Column(String(20), nullable=False) # EMAIL, TELEGRAM, SMS
    recipient = Column(String(255), nullable=False)
    subject = Column(String(255), nullable=False)
    payload_json = Column(JSON, default=dict, nullable=False)
    status = Column(String(20), default="PENDING", nullable=False, index=True) # PENDING, PROCESSING, MOCK_DELIVERED, PROVIDER_ACCEPTED, FAILED, SENT
    retry_count = Column(Integer, default=0, nullable=False)
    max_retries = Column(Integer, default=5, nullable=False)
    last_error = Column(Text, nullable=True)
    next_retry_at = Column(DateTime, default=utc_now, server_default=func.now(), nullable=False, index=True)
    created_at = Column(DateTime, default=utc_now, server_default=func.now(), nullable=False)
    sent_at = Column(DateTime, nullable=True)

    attempts = relationship("NotificationAttempt", back_populates="outbox", cascade="all, delete-orphan")

class NotificationAttempt(Base):
    __tablename__ = "notification_attempts"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    outbox_id = Column(BigInteger().with_variant(Integer, "sqlite"), ForeignKey("notification_outbox.id"), nullable=False, index=True)
    attempt_number = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False) # SUCCESS, ERROR
    response_code = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    attempted_at = Column(DateTime, default=utc_now, server_default=func.now(), nullable=False)

    outbox = relationship("NotificationOutbox", back_populates="attempts")

class AuditEvent(Base):
    __tablename__ = "audit_events"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    event_type = Column(String(100), nullable=False)
    entity_type = Column(String(50), nullable=False)
    entity_id = Column(BigInteger, nullable=True)
    details_json = Column(JSON, default=dict, nullable=False)
    created_at = Column(DateTime, default=utc_now, server_default=func.now(), nullable=False, index=True)

class TelegramInbox(Base):
    __tablename__ = "telegram_inbox"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    bot_id = Column(String(100), default="rentkom_bot", nullable=False, index=True)
    update_id = Column(BigInteger, nullable=False, index=True)
    chat_id = Column(BigInteger, nullable=True)
    message_text = Column(Text, nullable=True)
    status = Column(String(20), default="PROCESSING", nullable=False, index=True) # PROCESSING, COMPLETED, FAILED
    response_payload = Column(JSON, nullable=True)
    locked_at = Column(DateTime, default=utc_now, server_default=func.now(), nullable=False)
    completed_at = Column(DateTime, nullable=True)
    retry_count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=utc_now, server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("bot_id", "update_id", name="uq_telegram_inbox_bot_update"),
    )
