import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from core.database import Base
from core.models import Customer, ServiceRequest, CustomerFeedback
from tools.seed_demo_data import seed_demo_data

POSTGRES_URL = "postgresql+psycopg2://postgres@127.0.0.1:5433/postgres"

@pytest.fixture(scope="module")
def pg_session():
    engine = create_engine(POSTGRES_URL)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    session = Session()
    yield session
    session.close()
    engine.dispose()

def test_gate7_initial_and_repeat_seed_idempotency(pg_session):
    """
    Verify seed_demo_data creates 25 demo requests and repeat execution does not duplicate.
    """
    # 1. Run seed
    stats1 = seed_demo_data(db=pg_session)
    assert stats1["requests_created"] >= 0

    count_reqs_1 = pg_session.query(ServiceRequest).filter(
        ServiceRequest.source_row_id.like("demo_seed_v2:%")
    ).count()
    assert count_reqs_1 == 25

    count_fbs_1 = pg_session.query(CustomerFeedback).filter(
        CustomerFeedback.source_row_id.like("demo_feedback:%")
    ).count()
    assert count_fbs_1 == 30

    # 2. Run seed a second time (idempotency check)
    stats2 = seed_demo_data(db=pg_session)
    assert stats2["requests_created"] == 0
    assert stats2["requests_updated"] == 25
    assert stats2["feedbacks_created"] == 0

    count_reqs_2 = pg_session.query(ServiceRequest).filter(
        ServiceRequest.source_row_id.like("demo_seed_v2:%")
    ).count()
    assert count_reqs_2 == 25

    count_fbs_2 = pg_session.query(CustomerFeedback).filter(
        CustomerFeedback.source_row_id.like("demo_feedback:%")
    ).count()
    assert count_fbs_2 == 30

def test_gate7_interrupted_seed_and_recovery(pg_session):
    """
    Verify partial interruption recovery: simulate crash at index 10, then resume to 25.
    """
    # Clean up demo requests and their status history temporarily to test partial run
    pg_session.execute(text("""
        DELETE FROM request_status_history WHERE request_id IN (
            SELECT id FROM service_requests WHERE source_row_id LIKE 'demo_seed_v2:%'
        );
        DELETE FROM service_requests WHERE source_row_id LIKE 'demo_seed_v2:%';
    """))
    pg_session.commit()

    # Simulate interruption at index 10
    with pytest.raises(RuntimeError, match="Simulated interruption at request index 10"):
        seed_demo_data(db=pg_session, simulate_failure_at_request=10)

    count_partial = pg_session.query(ServiceRequest).filter(
        ServiceRequest.source_row_id.like("demo_seed_v2:%")
    ).count()
    assert count_partial == 10

    # Re-run after interruption (recovery)
    stats_recovery = seed_demo_data(db=pg_session)
    assert stats_recovery["requests_created"] == 15
    assert stats_recovery["requests_updated"] == 10

    count_final = pg_session.query(ServiceRequest).filter(
        ServiceRequest.source_row_id.like("demo_seed_v2:%")
    ).count()
    assert count_final == 25

def test_gate7_preservation_of_non_demo_records(pg_session):
    """
    Verify that existing non-demo business records are strictly preserved during demo seeding.
    """
    # Insert a non-demo customer and service request
    non_demo_phone = "+380509998877_custom_prod"
    cust = pg_session.query(Customer).filter(Customer.phone == non_demo_phone).first()
    if not cust:
        cust = Customer(full_name="Реальний Клієнт Продакшен", phone=non_demo_phone, email="prod@real.com")
        pg_session.add(cust)
        pg_session.flush()

    prod_req = pg_session.query(ServiceRequest).filter(
        ServiceRequest.source_row_id == "prod_actual_entry_1001"
    ).first()
    if not prod_req:
        prod_req = ServiceRequest(
            customer_id=cust.id,
            request_purpose="Хочу купити",
            property_type="Будинок",
            property_location="м. Львів, Центр",
            consultation_date="2026-12-01",
            additional_comments="Ні в якому разі не видаляти",
            status="NEW",
            source_channel="REST_API",
            source_row_id="prod_actual_entry_1001"
        )
        pg_session.add(prod_req)
        pg_session.commit()

    # Re-run demo seed
    seed_demo_data(db=pg_session)

    # Check non-demo record remains intact
    verified_prod = pg_session.query(ServiceRequest).filter(
        ServiceRequest.source_row_id == "prod_actual_entry_1001"
    ).first()
    assert verified_prod is not None
    assert verified_prod.customer.full_name == "Реальний Клієнт Продакшен"
    assert verified_prod.additional_comments == "Ні в якому разі не видаляти"
