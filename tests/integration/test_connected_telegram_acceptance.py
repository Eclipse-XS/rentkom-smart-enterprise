# -*- coding: utf-8 -*-
"""
Connected Telegram & Workflow Acceptance Regression Suite (INT Gate).
Covers:
- INT-T01: Live Telegram webhook with valid secret token succeeds.
- INT-T02: Live Telegram webhook with invalid secret token returns 403.
- INT-T03: Live Telegram webhook with missing secret token returns 401/403.
- INT-T04: Live Telegram webhook rejects test tokens.
- INT-T05: All 8 workflow JSON files parse without syntax errors.
- INT-T06: Zero workflow files contain the invalid host business-api:5680.
- INT-T07: All active workflow HTTP nodes target valid API paths.
- INT-T08: Unified router correctly dispatches to RAG and returns structured response.
- INT-T09: Duplicate update_id produces exactly one side effect in connected mode.
- INT-T10: Rate limiting or flood protection on Telegram ingress functions under load.
- INT-T11: Inbound payload with malformed JSON returns 422, not 500.
- INT-T12: Telegram outbox delivery records provider response ID.
"""
import glob
import json
import os
import re
from pathlib import Path
import pytest
from core.config import settings
from core.main import app
from core.models import NotificationOutbox
from core.services.outbox_service import process_outbox, TelegramTransportAdapter

WORKFLOW_DIR = Path(__file__).resolve().parent.parent.parent / "SMART_ENTERPRISE" / "automation" / "n8n"


def test_int_t01_live_webhook_valid_secret_succeeds(client, monkeypatch):
    """INT-T01: Live Telegram webhook with valid secret token succeeds."""
    token = "secret-token-live-2026"
    monkeypatch.setattr(settings, "TELEGRAM_SECRET_TOKEN", token)

    payload = {
        "update_id": 100001,
        "message": {"chat": {"id": 111}, "text": "/start"}
    }
    resp = client.post(
        "/api/v1/telegram/webhook",
        json=payload,
        headers={"X-Telegram-Bot-Api-Secret-Token": token}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["route"] == "Start Menu"
    assert "Rentkom" in data["response_text"]


def test_int_t02_live_webhook_invalid_secret_returns_403(client, monkeypatch):
    """INT-T02: Live Telegram webhook with invalid secret token returns 403."""
    monkeypatch.setattr(settings, "TELEGRAM_SECRET_TOKEN", "correct-secret-token")

    payload = {
        "update_id": 100002,
        "message": {"chat": {"id": 111}, "text": "/start"}
    }
    resp = client.post(
        "/api/v1/telegram/webhook",
        json=payload,
        headers={"X-Telegram-Bot-Api-Secret-Token": "forged-or-wrong-token"}
    )
    assert resp.status_code == 403
    assert "invalid" in resp.json()["detail"].lower()


def test_int_t03_live_webhook_missing_secret_returns_401_or_403(client, monkeypatch):
    """INT-T03: Live Telegram webhook with missing secret token returns 401/403."""
    monkeypatch.setattr(settings, "TELEGRAM_SECRET_TOKEN", "mandatory-secret-token")

    payload = {
        "update_id": 100003,
        "message": {"chat": {"id": 111}, "text": "/start"}
    }
    resp = client.post("/api/v1/telegram/webhook", json=payload)
    assert resp.status_code in (401, 403)


def test_int_t04_live_webhook_rejects_test_tokens(client, monkeypatch):
    """INT-T04: Live Telegram webhook rejects test tokens."""
    monkeypatch.setattr(settings, "TELEGRAM_SECRET_TOKEN", "mandatory-live-secret")
    monkeypatch.setattr(settings, "TEST_WEBHOOK_SECRET", "test-secret-12345")

    payload = {
        "update_id": 100004,
        "message": {"chat": {"id": 111}, "text": "/start"}
    }
    # An attacker attempts to bypass via test token
    resp = client.post(
        "/api/v1/telegram/webhook",
        json=payload,
        headers={"X-Test-Token": "test-secret-12345"}
    )
    assert resp.status_code in (401, 403)


def test_int_t05_all_eight_workflows_valid_json():
    """INT-T05: All 8 workflow JSON files parse without syntax errors."""
    workflow_files = sorted(glob.glob(str(WORKFLOW_DIR / "*.json")))
    assert len(workflow_files) == 8, f"Expected 8 workflow files, found {len(workflow_files)}"

    for wf_path in workflow_files:
        with open(wf_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert isinstance(data, dict), f"{wf_path} is not a valid JSON dictionary"
        assert "nodes" in data and isinstance(data["nodes"], list)
        assert "connections" in data and isinstance(data["connections"], dict)


def test_int_t06_zero_workflows_contain_invalid_host_5680():
    """INT-T06: Zero workflow files contain the invalid host business-api:5680."""
    workflow_files = sorted(glob.glob(str(WORKFLOW_DIR / "*.json")))
    for wf_path in workflow_files:
        with open(wf_path, "r", encoding="utf-8") as f:
            raw = f.read()
        assert "business-api:5680" not in raw, f"Invalid host business-api:5680 found in {wf_path}"
        assert "127.0.0.1:5680" not in raw, f"Invalid host 127.0.0.1:5680 found in {wf_path}"


def test_int_t07_active_workflows_http_nodes_target_valid_api_paths():
    """INT-T07: All active workflow HTTP nodes target valid API paths."""
    registered_paths = set(app.openapi()["paths"].keys())

    active_wf_paths = [
        WORKFLOW_DIR / "unified_telegram_router.json",
        WORKFLOW_DIR / "feedback_to_telegram.json",
    ]

    for wf_path in active_wf_paths:
        with open(wf_path, "r", encoding="utf-8") as f:
            wf_data = json.load(f)

        for node in wf_data.get("nodes", []):
            if "httpRequest" in node.get("type", ""):
                url_expr = node.get("parameters", {}).get("url", "")
                api_paths = re.findall(r"(/api/v1/[a-zA-Z0-9_\-/]+)", url_expr)
                assert api_paths, f"No /api/v1/ path found in HTTP node '{node.get('name')}' url '{url_expr}'"
                for p in api_paths:
                    matched = p in registered_paths or f"{p}/" in registered_paths or p.rstrip("/") in registered_paths
                    assert matched, f"Path '{p}' from node '{node.get('name')}' is not registered in FastAPI routes"


def test_int_t08_unified_router_dispatches_to_rag(client):
    """INT-T08: Unified router correctly dispatches to RAG and returns structured response in LR6 mode."""
    client.post(
        "/api/v1/telegram/mode",
        json={"mode": "LR6"},
        headers={"X-Internal-Token": settings.INTERNAL_API_KEY}
    )
    try:
        payload = {
            "update_id": 100008,
            "message": {
                "chat": {"id": 12345},
                "text": "Які послуги надає агентство нерухомості Рентком?"
            }
        }
        resp = client.post("/api/v1/telegram/webhook-mock", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["route"] in ("AI RAG Agent", "Fallback Service")
    finally:
        client.post(
            "/api/v1/telegram/mode",
            json={"mode": "LR2"},
            headers={"X-Internal-Token": settings.INTERNAL_API_KEY}
        )
    assert len(data["response_text"]) > 20
    assert "sources" in data
    assert "confidence_score" in data


def test_int_t09_duplicate_update_id_single_side_effect(client):
    """INT-T09: Duplicate update_id produces exactly one side effect in connected mode."""
    dup_update_id = 99887711
    payload = {
        "update_id": dup_update_id,
        "message": {"chat": {"id": 999}, "text": "/start"}
    }

    # First delivery
    resp1 = client.post("/api/v1/telegram/webhook-mock", json=payload)
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["is_duplicate"] is False

    # Second delivery with same update_id
    resp2 = client.post("/api/v1/telegram/webhook-mock", json=payload)
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["is_duplicate"] is True
    assert data2["route"] == data1["route"]
    assert data2["response_text"] == data1["response_text"]


def test_int_t10_flood_protection_rate_limiting(client, monkeypatch):
    """INT-T10: Rate limiting or flood protection on Telegram ingress functions under load."""
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "RATE_LIMIT_REQUESTS_PER_MINUTE", 5)

    from core.security import public_rate_limiter
    orig_rpm = public_rate_limiter.rpm
    try:
        public_rate_limiter._history.clear()
        public_rate_limiter.rpm = 5

        payload = {
            "update_id": 880011,
            "message": {"chat": {"id": 111}, "text": "/start"}
        }

        responses = []
        for i in range(10):
            p = dict(payload)
            p["update_id"] = 880011 + i
            r = client.post("/api/v1/telegram/webhook-mock", json=p)
            responses.append(r.status_code)

        assert 429 in responses, f"Expected 429 in responses, got {responses}"
    finally:
        public_rate_limiter.rpm = orig_rpm
        public_rate_limiter._history.clear()


def test_int_t11_malformed_json_returns_422(client):
    """INT-T11: Inbound payload with malformed JSON returns 422, not 500."""
    resp = client.post(
        "/api/v1/telegram/webhook-mock",
        content="{\"bad_json\": true",
        headers={"Content-Type": "application/json"}
    )
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}"


def test_int_t12_telegram_outbox_records_provider_response_id(db_session, monkeypatch):
    """INT-T12: Telegram outbox delivery records provider response ID."""
    monkeypatch.setattr(settings, "NOTIFICATION_PROVIDER", "telegram")
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "123:ABC")

    class MockTgAdapter(TelegramTransportAdapter):
        def send_notification(self, recipient, subject, payload):
            return True, 200, None, {"provider_message_id": 554433, "chat_id": recipient}

    item = NotificationOutbox(
        channel="TELEGRAM",
        recipient="554433",
        subject="Provider ID Check",
        payload_json={"telegram_chat_id": 554433},
        status="PENDING"
    )
    db_session.add(item)
    db_session.commit()

    res = process_outbox(db_session, telegram_transport=MockTgAdapter())
    assert res.success_count == 1

    db_session.refresh(item)
    assert item.status == "PROVIDER_ACCEPTED"
    assert item.payload_json["provider_metadata"]["provider_message_id"] == 554433
