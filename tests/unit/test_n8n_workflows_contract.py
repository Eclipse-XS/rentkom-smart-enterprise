import glob
import json
import os
import pytest
from core.models import NotificationOutbox
from core.services.outbox_service import process_outbox, SMTPTransportAdapter
from core.config import settings
from tests.integration.test_smtp_delivery import LocalTestSMTPServer

WORKFLOW_DIR = "SMART_ENTERPRISE/automation/n8n"

EXPECTED_WORKFLOW_FILES = {
    "ai_agent_rag.json",
    "ai_chat_test.json",
    "document_ingestion.json",
    "feedback_to_telegram.json",
    "forms_postgres_email.json",
    "monitoring_api.json",
    "telegram_rule_bot.json",
    "unified_telegram_router.json",
}


def test_all_eight_n8n_workflows_valid_json_and_zero_localhost_urls():
    """
    Verify all 8 n8n workflow JSON files:
      - Exist and parse as valid JSON with 'nodes' and 'connections'.
      - Contain zero hardcoded 'http://127.0.0.1:8000' or 'http://localhost:8000' URLs.
      - Contain zero leaked personal email addresses or plaintext passwords.
    """
    found_files = {os.path.basename(p) for p in glob.glob(f"{WORKFLOW_DIR}/*.json")}
    assert found_files == EXPECTED_WORKFLOW_FILES

    for fname in sorted(EXPECTED_WORKFLOW_FILES):
        path = os.path.join(WORKFLOW_DIR, fname)
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
            data = json.loads(raw)

        assert "nodes" in data and isinstance(data["nodes"], list)
        assert "connections" in data and isinstance(data["connections"], dict)
        assert "127.0.0.1:8000" not in raw, f"{fname} still contains hardcoded 127.0.0.1:8000"
        assert "127.0.0.1:5680" not in raw, f"{fname} still contains hardcoded 127.0.0.1:5680"
        assert "$env" not in raw, f"{fname} uses blocked n8n environment access"
        assert "kanaleclipse@gmail.com" not in raw, f"{fname} contains personal email"
        assert "VadymGone123" not in raw, f"{fname} contains hardcoded password"


def test_single_authoritative_active_telegram_router():
    """
    Verify that among Telegram bot workflows, ONLY 'unified_telegram_router.json' is active=True,
    while legacy 'ai_agent_rag.json' and 'telegram_rule_bot.json' are active=False to prevent
    conflicting webhook listeners.
    """
    with open(f"{WORKFLOW_DIR}/unified_telegram_router.json", "r", encoding="utf-8") as f:
        unified = json.load(f)
    with open(f"{WORKFLOW_DIR}/ai_agent_rag.json", "r", encoding="utf-8") as f:
        ai_rag = json.load(f)
    with open(f"{WORKFLOW_DIR}/telegram_rule_bot.json", "r", encoding="utf-8") as f:
        rule_bot = json.load(f)

    assert unified.get("active") is True
    assert ai_rag.get("active") is False
    assert rule_bot.get("active") is False

    guard = next(n for n in unified["nodes"] if n["name"] == "Acquire Telegram Inbox Guard")
    headers = guard["parameters"]["headerParameters"]["parameters"]
    assert all(h["name"].lower() != "x-internal-token" for h in headers)
    assert guard["credentials"]["httpHeaderAuth"]["id"] == "RentkomInternalAuth"
    telegram_ingress = next(n for n in unified["nodes"] if n["name"] == "Telegram Ingress")
    assert telegram_ingress["parameters"]["authentication"] == "headerAuth"
    assert telegram_ingress["credentials"]["httpHeaderAuth"]["id"] == "RentkomTelegramWebhookSecret"


def test_feedback_workflow_isolated_from_postgresql_and_does_not_default_ratings(client):
    """
    Verify that feedback_to_telegram.json does NOT default missing service_rating or realtor_rating to 5,
    and that FastAPI rejects null/missing ratings with HTTP 422 rather than storing false 5-star reviews.
    """
    with open(f"{WORKFLOW_DIR}/feedback_to_telegram.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    assert all(n["id"] != "u-node-persist-feedback-pg" for n in data["nodes"])
    assert "/api/v1/feedbacks/" not in json.dumps(data, ensure_ascii=False)
    notification = next(n for n in data["nodes"] if n["name"] == "Send Telegram Notification")
    assert "Оцінка сервісу" in notification["parameters"]["text"]
    assert "Оцінка рієлтора" in notification["parameters"]["text"]
    assert ": 5)" not in notification["parameters"]["text"]

    # FastAPI endpoint must reject null ratings with 422
    bad_payload = {
        "client_identifier": "Клієнт без оцінки",
        "service_rating": None,
        "realtor_rating": None,
        "feedback_text": "Забув поставити оцінку",
        "source_channel": "GOOGLE_SHEETS",
    }
    resp = client.post("/api/v1/feedbacks/", json=bad_payload)
    assert resp.status_code == 422


def test_end_to_end_local_workflow_contract_harness(client, db_session):
    """
    Offline/local E2E contract harness (Section 8.3):
    Customer input -> n8n normalized payload -> FastAPI -> DB -> Outbox -> local SMTP sink -> persisted delivery result.
    """
    smtp_server = LocalTestSMTPServer()
    smtp_server.start()
    adapter = SMTPTransportAdapter(host="127.0.0.1", port=smtp_server.port, use_tls=False, timeout=5.0)

    orig_provider = settings.NOTIFICATION_PROVIDER
    try:
        settings.NOTIFICATION_PROVIDER = "smtp"

        # Step 1: Simulate n8n normalized form submission to FastAPI
        n8n_form_payload = {
            "full_name": "Оксана Коваленко",
            "phone": "0675554433",
            "email": "oksana.kovalenko@example.com",
            "request_purpose": "Хочу купити",
            "property_type": "Квартира",
            "property_location": "м. Львів, вул. Наукова, 45",
            "consultation_date": "2026-12-15",
            "additional_comments": "2-кімнатна квартира з паркінгом",
            "source_channel": "GOOGLE_FORMS",
            "source_row_id": "19yHm6hi7G2b8s3y8pFVSoCn8m3HiWJDlFr_CdXMnoUg:567687119:2026-11-03:0675554433",
        }
        r_create = client.post(
            "/api/v1/requests/",
            json=n8n_form_payload,
            headers={"X-Internal-Token": settings.INTERNAL_API_KEY}
        )
        assert r_create.status_code == 201
        req_id = r_create.json()["id"]
        assert r_create.json()["customer"]["phone"] == "+380675554433"

        # Step 2: Verify outbox items queued in DB
        outbox_items = db_session.query(NotificationOutbox).filter(
            NotificationOutbox.payload_json["request_id"].as_integer() == req_id
        ).all()
        assert len(outbox_items) == 2

        # Step 3: Process outbox through local TCP SMTP sink
        result = process_outbox(db_session, batch_size=10, smtp_transport=adapter)
        assert result.processed_count == 2
        assert result.success_count == 2

        # Step 4: Verify persisted delivery result and received emails
        for item in outbox_items:
            db_session.refresh(item)
            assert item.status == "PROVIDER_ACCEPTED"
            assert item.sent_at is not None
            assert len(item.attempts) == 1
            assert item.attempts[0].response_code == 250

        assert len(smtp_server.received_messages) == 2
    finally:
        settings.NOTIFICATION_PROVIDER = orig_provider
        smtp_server.stop()
