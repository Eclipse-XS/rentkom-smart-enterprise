from datetime import date, datetime, timezone
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, field_validator, ConfigDict
import re

class CustomerBase(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=255)
    phone: str = Field(..., min_length=7, max_length=50)
    email: Optional[str] = None
    telegram_chat_id: Optional[int] = None

    @field_validator("phone")
    @classmethod
    def clean_phone(cls, v: str) -> str:
        v = v.strip()
        # canonical format check / clean
        digits = re.sub(r"[^\d+]", "", v)
        if len(digits) < 7:
            raise ValueError("Phone number must have at least 7 digits")
        return digits

class CustomerCreate(CustomerBase):
    pass

class CustomerResponse(CustomerBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime

class ServiceRequestCreate(BaseModel):
    full_name: str = Field(..., min_length=2)
    phone: str = Field(..., min_length=7)
    email: Optional[str] = None
    telegram_chat_id: Optional[int] = None
    request_purpose: str = Field(..., pattern="^(Хочу купити|Хочу орендувати|Хочу продати|Хочу здати в оренду)$")
    property_type: str = Field(..., pattern="^(Квартира|Будинок|Комерційне приміщення|Земля)$")
    property_location: str = Field(..., min_length=2)
    consultation_date: date
    additional_comments: Optional[str] = None
    source_channel: str = "REST_API"
    source_row_id: Optional[str] = None

class ServiceRequestStatusUpdate(BaseModel):
    new_status: str = Field(..., pattern="^(NEW|IN_REVIEW|SCHEDULED|COMPLETED|CANCELLED)$")
    comment: Optional[str] = None
    changed_by: str = "operator"

class ServiceRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_id: int
    customer: Optional[CustomerResponse] = None
    request_purpose: str
    property_type: str
    property_location: str
    consultation_date: date
    additional_comments: Optional[str] = None
    status: str
    source_channel: str
    source_row_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime

class CustomerFeedbackCreate(BaseModel):
    client_identifier: str = Field(..., min_length=2)
    service_rating: int = Field(..., ge=1, le=5)
    realtor_rating: int = Field(..., ge=1, le=5)
    feedback_text: Optional[str] = None
    source_channel: str = "REST_API"
    source_row_id: Optional[str] = None

class CustomerFeedbackResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    client_identifier: str
    service_rating: int
    realtor_rating: int
    feedback_text: Optional[str] = None
    source_channel: str
    created_at: datetime

class NotificationOutboxResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    channel: str
    recipient: str
    subject: str
    status: str
    payload_json: Optional[Dict[str, Any]] = None
    retry_count: int
    next_retry_at: datetime
    created_at: datetime
    sent_at: Optional[datetime] = None
    last_error: Optional[str] = None

class OutboxProcessResult(BaseModel):
    processed_count: int
    success_count: int
    failed_count: int
    details: List[Dict[str, Any]] = []

class SourceCitation(BaseModel):
    document_name: str
    chunk_id: Optional[str] = None
    score: float = 0.0

class RAGQueryRequest(BaseModel):
    question: str = Field(..., min_length=2)
    conversation_history: Optional[List[Dict[str, str]]] = None

class RAGQueryResponse(BaseModel):
    answer: str
    sources: List[str] = []
    citations: List[SourceCitation] = []
    confidence_score: Optional[float] = None
    latency_seconds: float
    mode: str = "grounded" # grounded, fallback, rejected

class TelegramInboxGuardRequest(BaseModel):
    update_id: Optional[int] = None
    chat_id: Optional[int] = None
    text: Optional[str] = None
    message: Optional[Dict[str, Any]] = None
    body: Optional[Dict[str, Any]] = None
    bot_id: str = "rentkom_bot"
    is_test: bool = False

class TelegramInboxGuardResponse(BaseModel):
    action: str # "EXECUTE" or "DROP_DUPLICATE"
    status: str # "NEW", "PROCESSING", "COMPLETED", "DUPLICATE"
    update_id: int
    chat_id: int
    text: str
    is_test: bool = False
    cached_response: Optional[Dict[str, Any]] = None

class TelegramRealSendRequest(BaseModel):
    chat_id: int
    text: str
    reply_markup: Optional[Dict[str, Any]] = None
    parse_mode: Optional[str] = None
    is_test: bool = False

class TelegramInboxCompleteRequest(BaseModel):
    update_id: int
    bot_id: str = "rentkom_bot"
    route: str
    response_text: str
    reply_markup: Optional[Dict[str, Any]] = None
    sources: Optional[List[str]] = None
    confidence_score: Optional[float] = None

class TelegramMockDispatchRequest(BaseModel):
    chat_id: int
    text: str
    reply_markup: Optional[Dict[str, Any]] = None
    parse_mode: Optional[str] = None
    is_test: bool = True

class TelegramMockDispatchResponse(BaseModel):
    ok: bool = True
    result: Dict[str, Any]
    delivery_mode: str = "INTEGRATION_TESTED"

class HealthResponse(BaseModel):
    status: str # healthy, degraded, unhealthy
    database: str
    qdrant: str
    ollama: str
    outbox_pending_count: int
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class AnalyticsSummary(BaseModel):
    total_requests: int
    requests_by_status: Dict[str, int]
    requests_by_property_type: Dict[str, int]
    requests_by_purpose: Dict[str, int]
    total_feedbacks: int
    average_service_rating: float
    average_realtor_rating: float
    pending_notifications: int
