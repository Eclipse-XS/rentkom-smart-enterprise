import pytest
from pydantic import ValidationError
from datetime import date
from core.services.request_service import normalize_phone
from core.schemas import ServiceRequestCreate, CustomerFeedbackCreate

def test_normalize_phone():
    assert normalize_phone("+380671112233") == "+380671112233"
    assert normalize_phone("0671112233") == "+380671112233"
    assert normalize_phone("380671112233") == "+380671112233"
    assert normalize_phone("  +38 (067) 111-22-33  ") == "+380671112233"

def test_service_request_validation_success():
    req = ServiceRequestCreate(
        full_name="Олексій Мельник",
        phone="+380671112233",
        request_purpose="Хочу купити",
        property_type="Квартира",
        property_location="вул. Генерала Чупринки",
        consultation_date=date(2026, 11, 1)
    )
    assert req.full_name == "Олексій Мельник"
    assert req.request_purpose == "Хочу купити"

def test_service_request_invalid_purpose():
    with pytest.raises(ValidationError):
        ServiceRequestCreate(
            full_name="Тест",
            phone="+380671112233",
            request_purpose="Невідома мета",
            property_type="Квартира",
            property_location="Львів",
            consultation_date=date(2026, 11, 1)
        )

def test_feedback_rating_range():
    # Valid 1-5
    fb = CustomerFeedbackCreate(
        client_identifier="+380671112233",
        service_rating=5,
        realtor_rating=4
    )
    assert fb.service_rating == 5

    # Invalid > 5
    with pytest.raises(ValidationError):
        CustomerFeedbackCreate(
            client_identifier="+380671112233",
            service_rating=6,
            realtor_rating=4
        )

    # Invalid < 1
    with pytest.raises(ValidationError):
        CustomerFeedbackCreate(
            client_identifier="+380671112233",
            service_rating=0,
            realtor_rating=4
        )
