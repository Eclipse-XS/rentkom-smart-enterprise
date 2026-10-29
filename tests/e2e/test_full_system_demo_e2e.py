import os
import json
import base64
import urllib.request
import pytest
from datetime import date
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.main import app
from core.database import get_db, Base
from core.schemas import ServiceRequestCreate
from core.services.outbox_service import process_outbox

def api_post(endpoint: str, payload: dict):
    req = urllib.request.Request(
        f"http://127.0.0.1:3000{endpoint}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": "Basic " + base64.b64encode(b"admin:admin").decode("utf-8"),
            "Content-Type": "application/json"
        }
    )
    with urllib.request.urlopen(req) as res:
        return json.loads(res.read())

POSTGRES_URL = "postgresql+psycopg2://postgres@127.0.0.1:5433/postgres"

@pytest.fixture(scope="module")
def real_pg_session():
    engine = create_engine(POSTGRES_URL)
    try:
        Base.metadata.create_all(bind=engine, checkfirst=True)
    except Exception:
        pass
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

    response = client.post("/api/v1/requests", json=req_payload)
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

    # 4. Verify Grafana live query reflects the new request
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
    assert "Вітаємо" in start_data["response_text"]
    assert "reply_markup" in start_data

    # 2. Menu query: Buying Process
    buy_resp = client.post("/api/v1/telegram/webhook-mock", json={
        "message": {"text": "🏢 Процес купівлі/продажу", "chat": {"id": 123456}}
    })
    assert buy_resp.status_code == 200
    buy_data = buy_resp.json()
    assert buy_data["route"] == "Купівля/Продаж"
    assert "Процес купівлі/продажу" in buy_data["response_text"]

    # 3. Legal/RAG query via real Qdrant and real Ollama
    rag_resp = client.post("/api/v1/rag/ask", json={
        "question": "Скільки етапів перевірки документів визначено у регламенті агентства?"
    })
    assert rag_resp.status_code == 200
    rag_data = rag_resp.json()
    assert "6" in rag_data["answer"] or "етап" in rag_data["answer"] or "240-00-00" in rag_data["answer"]
    assert len(rag_data["sources"]) > 0
    assert any("Керівництво_з_перевірки_документів" in s for s in rag_data["sources"])
    assert rag_data["confidence_score"] > 0.6
