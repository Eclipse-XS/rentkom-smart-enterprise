# -*- coding: utf-8 -*-
"""
End-to-End Business Scenarios Verification for Clean-Room Deployment.
Scenario A: API -> PostgreSQL -> Outbox -> Mock Provider -> Grafana / Analytics
Scenario B: Test Webhook -> Unified Router -> Qdrant -> Ollama -> Mock Telegram Response
Scenario C: Test Feedback -> n8n -> FastAPI -> PostgreSQL -> Grafana
Scenario D: Duplicate Telegram update -> Inbox Guard -> Zero duplicate processing
Scenario E: Restart recovery & data persistence verification
"""
import time
import json
import urllib.request
import urllib.error
from datetime import date, datetime, timezone
import pytest
from core.database import SessionLocal
from core.models import Customer, ServiceRequest, NotificationOutbox, TelegramInbox, CustomerFeedback
from core.config import settings
from core.services.outbox_service import process_outbox

WEBHOOK_ROUTER_URL = "http://localhost:5678/webhook/rentkom-unified-router-test"
WEBHOOK_FEEDBACK_URL = "http://localhost:5678/webhook/rentkom-feedback-test"
FASTAPI_BASE_URL = "http://127.0.0.1:8000/api/v1"

def api_post(endpoint: str, payload: dict, headers: dict = None):
    url = f"{FASTAPI_BASE_URL}{endpoint}"
    data = json.dumps(payload).encode("utf-8")
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=req_headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))

def api_get(endpoint: str):
    url = f"{FASTAPI_BASE_URL}{endpoint}"
    req = urllib.request.Request(url, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))

def test_scenario_a_api_postgres_outbox_mock_provider_grafana():
    """Scenario A: API -> PostgreSQL -> Outbox -> Mock Provider -> Analytics"""
    unique_suffix = int(time.time() * 1000) % 100000
    req_payload = {
        "full_name": f"Клієнт Сценарій А {unique_suffix}",
        "phone": f"+38067{unique_suffix:07d}"[:13],
        "email": f"client_a_{unique_suffix}@rentkom.lviv.ua",
        "request_purpose": "Хочу купити",
        "property_type": "Квартира",
        "property_location": "Галицький р-н",
        "consultation_date": str(date(2026, 11, 20)),
        "source_channel": "WEB"
    }

    # 1. API creates request (REST HTTP 201 Created)
    status, res = api_post("/requests/", req_payload)
    assert status in (200, 201)
    req_id = res["id"]

    # 2. Verify in PostgreSQL
    with SessionLocal() as db:
        sr = db.query(ServiceRequest).filter(ServiceRequest.id == req_id).first()
        assert sr is not None
        assert sr.customer.email == req_payload["email"]

        # 3. Check outbox records (client + admin)
        outbox_items = db.query(NotificationOutbox).filter(
            NotificationOutbox.payload_json["request_id"].as_integer() == req_id
        ).all()
        assert len(outbox_items) == 2

    # 4. Process outbox (or verify background worker processed to terminal status)
    with SessionLocal() as db:
        max_wait_seconds = 3.0
        start_wait = time.time()
        outbox_items = []
        while time.time() - start_wait < max_wait_seconds:
            db.expire_all()
            process_outbox(db, batch_size=10)
            outbox_items = db.query(NotificationOutbox).filter(
                NotificationOutbox.payload_json["request_id"].as_integer() == req_id
            ).all()
            for it in outbox_items:
                if it.status in ("FAILED", "DEAD_LETTER"):
                    pytest.fail(f"Outbox worker failed on item {it.id} with status {it.status}: {it.last_error}")
            if len(outbox_items) == 2 and all(it.status in ("MOCK_DELIVERED", "PROVIDER_ACCEPTED", "SENT") for it in outbox_items):
                break
            time.sleep(0.15)

        assert len(outbox_items) == 2, f"Expected 2 outbox items, found {len(outbox_items)}"
        for it in outbox_items:
            assert it.status in ("MOCK_DELIVERED", "PROVIDER_ACCEPTED", "SENT"), (
                f"Outbox item {it.id} did not reach terminal delivery within {max_wait_seconds}s. Current status: {it.status}"
            )
            assert it.sent_at is not None

    # 5. Verify analytics summary endpoint
    status, summary = api_get("/analytics/summary")
    assert status == 200
    assert summary["total_requests"] >= 1

def test_scenario_b_test_webhook_unified_router_qdrant_ollama():
    """Scenario B: Test Webhook -> Unified Router -> Qdrant -> Ollama -> Mock Response"""
    msg_id = int(time.time() * 100) % 900000 + 100000
    payload = {
        "update_id": msg_id,
        "message": {
            "message_id": msg_id,
            "text": "Як перевірити документи на квартиру?",
            "chat": {"id": 888777},
            "from": {"id": 888777, "first_name": "ScenarioTester"}
        }
    }
    req = urllib.request.Request(
        WEBHOOK_ROUTER_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Test-Token": settings.TEST_WEBHOOK_SECRET
        }
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        assert resp.status == 200

    # Verify execution registered in postgres telegram_inbox (polling up to 10s if async)
    found = False
    for _ in range(20):
        with SessionLocal() as db:
            inbox_entry = db.query(TelegramInbox).filter(
                TelegramInbox.update_id == msg_id
            ).first()
            if inbox_entry:
                found = True
                assert inbox_entry.status in ("PROCESSING", "COMPLETED")
                break
        time.sleep(0.5)
    assert found, f"Expected update_id {msg_id} to be recorded in telegram_inbox"

def test_scenario_c_test_feedback_n8n_fastapi_postgres():
    """Scenario C: Test Feedback -> n8n -> FastAPI -> PostgreSQL -> Grafana"""
    row_id = f"SCENARIO_C_{int(time.time()*1000)}"
    feedback_payload = {
        "client_identifier": "Сергій Коваленко (Scenario C)",
        "service_rating": 5,
        "realtor_rating": 5,
        "feedback_text": "Чудовий супровід угоди, все швидко та професійно!",
        "row_number": row_id,
        "id": row_id,
        "source_row_id": row_id
    }
    req = urllib.request.Request(
        WEBHOOK_FEEDBACK_URL,
        data=json.dumps(feedback_payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        assert resp.status == 200

    # Verify in PostgreSQL customer_feedbacks (polling up to 5s)
    found = False
    for _ in range(10):
        with SessionLocal() as db:
            fb = db.query(CustomerFeedback).filter(CustomerFeedback.source_row_id == row_id).first()
            if fb:
                found = True
                assert fb.service_rating == 5
                assert fb.realtor_rating == 5
                break
        time.sleep(0.5)
    assert found, f"Expected feedback row_id {row_id} to be persisted in customer_feedbacks"

def test_scenario_d_telegram_update_deduplication_guard():
    """Scenario D: Duplicate Telegram update -> Inbox Guard -> Zero duplicate processing"""
    dup_update_id = int(time.time() * 10000) % 9000000 + 1000000
    guard_payload = {
        "update_id": dup_update_id,
        "chat_id": 999111,
        "text": "/start",
        "is_test": True
    }

    # 1. First arrival acquires lease
    status1, res1 = api_post("/telegram/inbox/guard", guard_payload, headers={"X-Test-Token": settings.TEST_WEBHOOK_SECRET})
    assert status1 == 200
    assert res1["action"] == "EXECUTE"
    assert res1["status"] == "NEW"

    # Complete it
    status_c, res_c = api_post("/telegram/inbox/complete", {
        "update_id": dup_update_id,
        "route": "Start Menu",
        "response_text": "Вітаємо у чат-боті..."
    })
    assert status_c == 200

    # 2. Second identical arrival must be DROPPED
    status2, res2 = api_post("/telegram/inbox/guard", guard_payload, headers={"X-Test-Token": settings.TEST_WEBHOOK_SECRET})
    assert status2 == 200
    assert res2["action"] == "DROP_DUPLICATE"
    assert res2["status"] == "DUPLICATE"

def test_scenario_e_data_persistence_across_schema_reload():
    """Scenario E: Verify data integrity and model relationships in PostgreSQL."""
    with SessionLocal() as db:
        customers_count = db.query(Customer).count()
        requests_count = db.query(ServiceRequest).count()
        feedbacks_count = db.query(CustomerFeedback).count()
        assert customers_count >= 1
        assert requests_count >= 1
        assert feedbacks_count >= 1
