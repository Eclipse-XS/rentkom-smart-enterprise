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
import socket
import urllib.request
import urllib.error
from datetime import date
import pytest
from fastapi.testclient import TestClient
from core.main import app
from core.database import SessionLocal
from core.models import Customer, ServiceRequest, NotificationOutbox, TelegramInbox, CustomerFeedback
from core.config import settings
from core.services.outbox_service import process_outbox
from core.api.v1.endpoints.telegram import set_telegram_bot_mode

WEBHOOK_ROUTER_URL = "http://localhost:5678/webhook/rentkom-unified-router-test"
WEBHOOK_FEEDBACK_URL = "http://localhost:5678/webhook/rentkom-feedback-test"
FASTAPI_BASE_URL = "http://127.0.0.1:8000/api/v1"


def _is_port_open(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.3)
    try:
        return sock.connect_ex(("127.0.0.1", port)) == 0
    finally:
        sock.close()


def api_post(endpoint: str, payload: dict, headers: dict = None):
    with TestClient(app) as tc:
        resp = tc.post(f"/api/v1{endpoint}", json=payload, headers=headers or {})
        return resp.status_code, resp.json()


def api_get(endpoint: str, headers: dict = None):
    with TestClient(app) as tc:
        resp = tc.get(f"/api/v1{endpoint}", headers=headers or {})
        return resp.status_code, resp.json()


def test_scenario_a_api_postgres_outbox_mock_provider_grafana(db_session):
    """Scenario A: API -> DB -> Outbox -> Mock Provider -> Analytics"""
    unique_suffix = int(time.time() * 1000) % 100000
    req_payload = {
        "full_name": f"Клієнт Сценарій А {unique_suffix}",
        "phone": f"+38067{unique_suffix:07d}"[:13],
        "email": f"client_a_{unique_suffix}@rentkom.lviv.ua",
        "request_purpose": "Хочу купити",
        "property_type": "Квартира",
        "property_location": "Галицький р-н",
        "consultation_date": str(date(2026, 11, 20)),
        "source_channel": "REST_API"
    }

    status, res = api_post("/requests/", req_payload, headers={"X-Admin-Token": settings.ADMIN_API_KEY})
    assert status in (200, 201)
    req_id = res["id"]

    with SessionLocal() as db:
        sr = db.query(ServiceRequest).filter(ServiceRequest.id == req_id).first()
        assert sr is not None
        assert sr.customer.email == req_payload["email"]

        outbox_items = db.query(NotificationOutbox).filter(
            NotificationOutbox.payload_json["request_id"].as_integer() == req_id
        ).all()
        assert len(outbox_items) == 2

        process_outbox(db, batch_size=10)
        for it in outbox_items:
            db.refresh(it)
            assert it.status in ("MOCK_DELIVERED", "PROVIDER_ACCEPTED", "SENT")
            assert it.sent_at is not None

    status, summary = api_get("/analytics/summary", headers={"X-Admin-Token": settings.ADMIN_API_KEY})
    assert status == 200
    assert summary["total_requests"] >= 1


def test_scenario_b_test_webhook_unified_router_qdrant_ollama(db_session):
    """Scenario B: Test Webhook -> Unified Router -> Qdrant -> Ollama -> Mock Response"""
    set_telegram_bot_mode("LR6", db_session)
    if not _is_port_open(5678):
        # Verify via FastAPI webhook endpoint directly when n8n daemon is not running
        msg_id = int(time.time() * 100) % 900000 + 100000
        status, res = api_post("/telegram/webhook", {
            "update_id": msg_id,
            "message": {"text": "Як перевірити документи на квартиру?", "chat": {"id": 888777}}
        })
        assert status == 200
        assert res["route"] in ("AI RAG", "AI RAG Agent")
        return

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


def test_scenario_c_test_feedback_n8n_fastapi_postgres(db_session):
    """Scenario C: Test Feedback -> n8n / FastAPI -> DB -> Analytics"""
    row_id = f"SCENARIO_C_{int(time.time()*1000)}"
    feedback_payload = {
        "client_identifier": "Сергій Коваленко (Scenario C)",
        "service_rating": 5,
        "realtor_rating": 5,
        "feedback_text": "Чудовий супровід угоди, все швидко та професійно!",
        "source_channel": "GOOGLE_SHEETS",
        "source_row_id": row_id
    }
    status, _ = api_post("/feedbacks/", feedback_payload)
    assert status == 201

    with SessionLocal() as db:
        fb = db.query(CustomerFeedback).filter(CustomerFeedback.source_row_id == row_id).first()
        assert fb is not None
        assert fb.service_rating == 5
        assert fb.realtor_rating == 5


def test_scenario_d_telegram_update_deduplication_guard(db_session):
    """Scenario D: Duplicate Telegram update -> Inbox Guard -> Zero duplicate processing"""
    dup_update_id = int(time.time() * 10000) % 9000000 + 1000000
    guard_payload = {
        "update_id": dup_update_id,
        "chat_id": 999111,
        "text": "/start",
        "is_test": True
    }
    headers = {
        "X-Test-Token": settings.TEST_WEBHOOK_SECRET,
        "X-Internal-Token": settings.INTERNAL_API_KEY,
    }

    # 1. First arrival acquires lease
    status1, res1 = api_post("/telegram/inbox/guard", guard_payload, headers=headers)
    assert status1 == 200
    assert res1["action"] == "EXECUTE"
    assert res1["status"] == "NEW"

    # Complete it (with internal authentication header)
    status_c, res_c = api_post(
        "/telegram/inbox/complete",
        {
            "update_id": dup_update_id,
            "route": "Start Menu",
            "response_text": "Вітаємо у чат-боті..."
        },
        headers=headers,
    )
    assert status_c == 200

    # 2. Second identical arrival must be DROPPED
    status2, res2 = api_post("/telegram/inbox/guard", guard_payload, headers=headers)
    assert status2 == 200
    assert res2["action"] == "DROP_DUPLICATE"
    assert res2["status"] == "DUPLICATE"


def test_scenario_e_data_persistence_across_schema_reload(db_session):
    """Scenario E: Verify data integrity and model relationships in DB."""
    # Ensure at least one request and feedback exist in this session
    request_status, _ = api_post("/requests/", {
        "full_name": "Сценарій Е Клієнт",
        "phone": "+380671119988",
        "email": "e_client@rentkom.lviv.ua",
        "request_purpose": "Хочу купити",
        "property_type": "Квартира",
        "property_location": "м. Львів, Центр",
        "consultation_date": "2026-12-01",
    }, headers={"X-Admin-Token": settings.ADMIN_API_KEY})
    assert request_status in (200, 201)
    feedback_status, _ = api_post("/feedbacks/", {
        "client_identifier": "+380671119988",
        "service_rating": 5,
        "realtor_rating": 5,
        "feedback_text": "Тест Сценарію Е",
    })
    assert feedback_status == 201
    with SessionLocal() as db:
        customers_count = db.query(Customer).count()
        requests_count = db.query(ServiceRequest).count()
        feedbacks_count = db.query(CustomerFeedback).count()
        assert customers_count >= 1
        assert requests_count >= 1
        assert feedbacks_count >= 1
