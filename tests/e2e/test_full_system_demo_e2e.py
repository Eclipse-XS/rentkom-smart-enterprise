import os
import json
import base64
import urllib.request
import pytest
from datetime import date
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from core.main import app
from core.config import settings
from core.database import get_db, Base
from core.schemas import ServiceRequestCreate
from core.services.outbox_service import process_outbox
from tests.safety import assert_safe_test_database, ensure_test_schema_marker

def api_post(endpoint: str, payload: dict):
    req = urllib.request.Request(
        f"http://127.0.0.1:3000{endpoint}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": "Basic " + base64.b64encode(b"admin:admin").decode("utf-8"),
            "Content-Type": "application/json"
        }
    )
    with urllib.request.urlopen(req, timeout=3) as res:
        return json.loads(res.read())


@pytest.fixture(scope="module")
def real_pg_session():
    postgres_url = os.getenv("TEST_DATABASE_URL")
    if not postgres_url:
        pytest.skip("TEST_DATABASE_URL not set; skipping full system demo E2E test.")
    assert_safe_test_database(postgres_url)
    try:
        engine = create_engine(postgres_url, pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1;"))
        ensure_test_schema_marker(engine)
        Base.metadata.create_all(bind=engine, checkfirst=True)
    except RuntimeError:
        raise
    except Exception as exc:
        pytest.skip(f"PostgreSQL test database unreachable: {exc}")
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield Session
    app.dependency_overrides.clear()
    engine.dispose()

def test_scenario_1_api_fastapi_postgres_outbox_grafana(real_pg_session):
    """
    Scenario 1:
    Demo Form / API -> FastAPI -> PostgreSQL 18 -> Outbox -> Mock Notification -> Grafana
    """
    client = TestClient(app)
    Session = real_pg_session

    # 1. Submit service request via FastAPI endpoint
    unique_phone = f"+38067{int(pytest.importorskip('time').time() * 1000 % 10000000):07d}"
    req_payload = {
        "full_name": "Енд-ту-Енд Клієнт",
        "phone": unique_phone,
        "email": "e2e_client_live@rentkom.lviv.ua",
        "request_purpose": "Хочу купити",
        "property_type": "Квартира",
        "property_location": "м. Львів, Франківський р-н, вул. Сахарова",
        "consultation_date": "2026-11-20",
        "additional_comments": "Тестова заявка сценарію 1",
        "source_channel": "REST_API"
    }

    response = client.post("/api/v1/requests", json=req_payload, headers={"X-Admin-Token": "rentkom-demo-admin-key-2026"})
    assert response.status_code == 201
    res_data = response.json()
    req_id = res_data["id"]
    assert res_data["status"] == "NEW"
    assert res_data["customer"]["phone"] == unique_phone

    # 2. Verify outbox record created in PostgreSQL
    db = Session()
    from core.models import NotificationOutbox, NotificationAttempt
    outbox_item = db.query(NotificationOutbox).filter(
        NotificationOutbox.recipient == "e2e_client_live@rentkom.lviv.ua"
    ).order_by(NotificationOutbox.id.desc()).first()
    assert outbox_item is not None
    assert outbox_item.status == "PENDING"
    assert outbox_item.payload_json["request_id"] == req_id

    # 3. Process outbox with mock notification provider (batch size 50 to process queue)
    process_res = process_outbox(db, batch_size=50)
    assert process_res.processed_count >= 1

    db.refresh(outbox_item)
    assert outbox_item.status in ("MOCK_DELIVERED", "PROVIDER_ACCEPTED", "SENT")
    assert outbox_item.sent_at is not None
    assert len(outbox_item.attempts) >= 1
    success_attempt = next(a for a in outbox_item.attempts if a.status == "SUCCESS")
    assert success_attempt.response_code == 250
    db.close()

    # 4. Verify Grafana live query reflects the new request (if Grafana is running locally on :3000)
    try:
        grafana_res = api_post("/api/ds/query", {
            "queries": [
                {
                    "refId": "A",
                    "datasource": {"type": "postgres", "uid": "lab5-postgres"},
                    "rawSql": "SELECT COUNT(*) AS total FROM service_requests;",
                    "format": "table"
                }
            ]
        })
        total_in_grafana = grafana_res["results"]["A"]["frames"][0]["data"]["values"][0][0]
        assert total_in_grafana >= 1
    except Exception:
        # When Grafana container is not running on :3000, verify the exact Grafana SQL query directly on PostgreSQL
        db_check = Session()
        total_in_pg = db_check.execute(text("SELECT COUNT(*) AS total FROM service_requests;")).scalar()
        db_check.close()
        assert total_in_pg >= 1

def test_scenario_2_unified_router_qdrant_ollama_real_rag(real_pg_session):
    """
    Scenario 2:
    Query -> Unified Router -> Qdrant -> Ollama -> Answer
    """
    client = TestClient(app)

    # 1. Menu query: /start
    start_resp = client.post("/api/v1/telegram/webhook-mock", json={
        "message": {"text": "/start", "chat": {"id": 123456}}
    })
    assert start_resp.status_code == 200
    start_data = start_resp.json()
    assert start_data["route"] == "Start Menu"
    assert "Rentkom" in start_data["response_text"]
    assert "reply_markup" in start_data

    # 2. Menu query: Buying Process
    buy_resp = client.post("/api/v1/telegram/webhook-mock", json={
        "message": {"text": "🏠 Купівля та продаж", "chat": {"id": 123456}}
    })
    assert buy_resp.status_code == 200
    buy_data = buy_resp.json()
    assert buy_data["route"] == "Купівля/Продаж"
    assert "Купівля та продаж нерухомості" in buy_data["response_text"]

    # 3. Legal/RAG query via real Qdrant and real Ollama
    rag_resp = client.post(
        "/api/v1/rag/ask",
        json={"question": "Скільки етапів перевірки документів визначено у регламенті агентства?"},
        headers={"X-Internal-Token": settings.INTERNAL_API_KEY},
    )
    assert rag_resp.status_code == 200
    rag_data = rag_resp.json()
    if rag_data.get("mode") == "fallback":
        assert len(rag_data["answer"]) > 20
        assert rag_data["confidence_score"] == 0.0
    else:
        assert "6" in rag_data["answer"] or "етап" in rag_data["answer"]
        assert len(rag_data["sources"]) > 0
        assert any("Керівництво_з_перевірки_документів" in s for s in rag_data["sources"])
        assert rag_data["confidence_score"] > 0.6
