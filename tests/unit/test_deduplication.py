from datetime import date
from core.models import Customer, ServiceRequest
from core.schemas import ServiceRequestCreate
from core.services.request_service import create_service_request

def test_customer_deduplication(db_session):
    req1 = ServiceRequestCreate(
        full_name="Василь Гончарук",
        phone="+380689998877",
        email="vasyl.goncharuk@example.com",
        request_purpose="Хочу купити",
        property_type="Квартира",
        property_location="Шевченківський р-н",
        consultation_date=date(2026, 11, 2)
    )
    res1 = create_service_request(db_session, req1)

    # Check 1 customer in DB
    customers = db_session.query(Customer).all()
    assert len(customers) == 1
    assert customers[0].phone == "+380689998877"

    # Second request from the same customer with varied phone format
    req2 = ServiceRequestCreate(
        full_name="Василь Гончарук",
        phone="0689998877", # normalized to +380689998877
        email="vasyl.goncharuk@example.com",
        request_purpose="Хочу орендувати",
        property_type="Будинок",
        property_location="Франківський р-н",
        consultation_date=date(2026, 11, 10)
    )
    res2 = create_service_request(db_session, req2)

    # Customer count must still be 1!
    customers_after = db_session.query(Customer).all()
    assert len(customers_after) == 1

    # But requests count must be 2, both linked to the same customer!
    requests = db_session.query(ServiceRequest).all()
    assert len(requests) == 2
    assert requests[0].customer_id == customers_after[0].id
    assert requests[1].customer_id == customers_after[0].id
