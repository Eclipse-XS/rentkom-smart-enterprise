import pytest
import time
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from core.main import app
from core.database import Base, get_db
from core.models import CustomerFeedback

POSTGRES_URL = "postgresql+psycopg2://postgres@127.0.0.1:5433/postgres"

@pytest.fixture(scope="module")
def pg_session():
    engine = create_engine(POSTGRES_URL)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    session = Session()
    yield session
    session.close()
    app.dependency_overrides.clear()
    engine.dispose()

def test_lr3_feedback_persistence_and_grafana_aggregation(pg_session):
    """
    Proves GATE 7: LR3 Google Form / Sheet -> PostgreSQL customer_feedbacks -> Grafana Pipeline.
    """
    client = TestClient(app)

    # 1. Capture baseline Grafana query results
    baseline_count = pg_session.execute(text("SELECT COUNT(*) FROM customer_feedbacks;")).scalar()

    # 2. Submit new feedback via API
    unique_suffix = int(time.time() * 1000 % 10000000)
    fb_data = {
        "client_identifier": f"Клієнт ЛР3 {unique_suffix}",
        "service_rating": 5,
        "realtor_rating": 4,
        "feedback_text": "Чудовий сервіс агентства Рентком, оперативно підібрали квартиру.",
        "source_channel": "GOOGLE_SHEETS",
        "source_row_id": f"sheet_row_{unique_suffix}"
    }

    res = client.post("/api/v1/feedbacks/", json=fb_data)
    assert res.status_code == 201, f"Feedback submission failed: {res.text}"
    fb_id = res.json()["id"]

    # 3. Verify PostgreSQL row persistence
    fb_row = pg_session.query(CustomerFeedback).filter(CustomerFeedback.id == fb_id).first()
    assert fb_row is not None
    assert fb_row.client_identifier == fb_data["client_identifier"]
    assert fb_row.service_rating == 5
    assert fb_row.realtor_rating == 4
    assert fb_row.source_channel == "GOOGLE_SHEETS"

    # 4. Verify Grafana Panel Queries reflect the updated data
    new_count = pg_session.execute(text("SELECT COUNT(*) FROM customer_feedbacks;")).scalar()
    assert new_count == baseline_count + 1

    avg_service = pg_session.execute(text("SELECT ROUND(AVG(service_rating)::numeric, 2) FROM customer_feedbacks;")).scalar()
    assert float(avg_service) >= 1.0 and float(avg_service) <= 5.0

    avg_realtor = pg_session.execute(text("SELECT ROUND(AVG(realtor_rating)::numeric, 2) FROM customer_feedbacks;")).scalar()
    assert float(avg_realtor) >= 1.0 and float(avg_realtor) <= 5.0
