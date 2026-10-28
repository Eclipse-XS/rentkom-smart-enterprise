# -*- coding: utf-8 -*-
"""
Verification of Gate 1: Telegram Connected Mode Authentication & Security Contract
Validates:
1. Demo Mode + valid test token -> Allowed (HTTP 200).
2. Demo Mode + invalid test token -> Rejected (HTTP 401).
3. Connected Mode + authorized internal request -> Allowed (HTTP 200).
4. Connected Mode + unauthorized request -> Rejected (HTTP 401).
5. Connected Mode + mock dispatcher call -> Forbidden (HTTP 403).
6. Duplicate Telegram update -> Dropped without double processing.
7. Test Webhook has NO route to real Telegram Send Message (HTTP 403).
8. Real Telegram Send Message requires valid internal credentials (HTTP 401/422).
"""
import pytest
import time
import random
from fastapi.testclient import TestClient
from core.main import app
from core.config import settings

client = TestClient(app)

def test_demo_mode_valid_test_token():
    # Save original settings
    orig_env = settings.ENVIRONMENT
    orig_allow = settings.ALLOW_TEST_WEBHOOK
    try:
        settings.ENVIRONMENT = "demo"
        settings.ALLOW_TEST_WEBHOOK = True

        uid = int(time.time() * 1000) % 90000000 + random.randint(10000, 99999)
        resp = client.post(
            "/api/v1/telegram/inbox/guard",
            headers={"X-Test-Token": settings.TEST_WEBHOOK_SECRET},
            json={
                "update_id": uid,
                "chat_id": 12345,
                "text": "Тестове повідомлення",
                "is_test": True
            }
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data["action"] == "EXECUTE"
        assert data["status"] == "NEW"
        assert data["is_test"] is True
    finally:
        settings.ENVIRONMENT = orig_env
        settings.ALLOW_TEST_WEBHOOK = orig_allow

def test_demo_mode_invalid_test_token():
    orig_env = settings.ENVIRONMENT
    try:
        settings.ENVIRONMENT = "demo"
        resp = client.post(
            "/api/v1/telegram/inbox/guard",
            headers={"X-Test-Token": "WrongSecretToken"},
            json={
                "update_id": 999102,
                "chat_id": 12345,
                "text": "Тестове повідомлення",
                "is_test": True
            }
        )
        assert resp.status_code == 401
        assert "Unauthorized" in resp.json()["detail"]
    finally:
        settings.ENVIRONMENT = orig_env

def test_connected_mode_authorized_internal_request():
    orig_env = settings.ENVIRONMENT
    try:
        settings.ENVIRONMENT = "connected"
        uid = int(time.time() * 1000) % 90000000 + random.randint(10000, 99999)
        resp = client.post(
            "/api/v1/telegram/inbox/guard",
            headers={"X-Internal-Token": settings.INTERNAL_API_KEY},
            json={
                "update_id": uid,
                "chat_id": 12345,
                "text": "Живе повідомлення від Telegram Webhook",
                "is_test": False
            }
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data["action"] == "EXECUTE"
        assert data["status"] == "NEW"
        assert data["is_test"] is False
    finally:
        settings.ENVIRONMENT = orig_env

def test_connected_mode_unauthorized_request():
    orig_env = settings.ENVIRONMENT
    try:
        settings.ENVIRONMENT = "connected"
        resp = client.post(
            "/api/v1/telegram/inbox/guard",
            headers={"X-Internal-Token": "InvalidToken"},
            json={
                "update_id": 999104,
                "chat_id": 12345,
                "text": "Несанкціонований запит",
                "is_test": False
            }
        )
        assert resp.status_code == 401
        assert "Unauthorized" in resp.json()["detail"]
    finally:
        settings.ENVIRONMENT = orig_env

def test_connected_mode_fails_closed_when_internal_key_missing():
    orig_env = settings.ENVIRONMENT
    orig_key = settings.INTERNAL_API_KEY
    try:
        settings.ENVIRONMENT = "connected"
        settings.INTERNAL_API_KEY = None # Unset / missing
        resp = client.post(
            "/api/v1/telegram/inbox/guard",
            headers={"X-Internal-Token": "SomeToken"},
            json={
                "update_id": 999199,
                "chat_id": 12345,
                "text": "Тест без налаштованого INTERNAL_API_KEY",
                "is_test": False
            }
        )
        assert resp.status_code == 500
        assert "INTERNAL_API_KEY is not configured" in resp.json()["detail"]
    finally:
        settings.ENVIRONMENT = orig_env
        settings.INTERNAL_API_KEY = orig_key

def test_demo_mode_fails_closed_when_test_secret_missing():
    orig_env = settings.ENVIRONMENT
    orig_sec = settings.TEST_WEBHOOK_SECRET
    try:
        settings.ENVIRONMENT = "demo"
        settings.TEST_WEBHOOK_SECRET = None # Unset / missing
        resp = client.post(
            "/api/v1/telegram/inbox/guard",
            headers={"X-Test-Token": "SomeToken"},
            json={
                "update_id": 999198,
                "chat_id": 12345,
                "text": "Тест без налаштованого TEST_WEBHOOK_SECRET",
                "is_test": True
            }
        )
        assert resp.status_code == 500
        assert "TEST_WEBHOOK_SECRET is not configured" in resp.json()["detail"]
    finally:
        settings.ENVIRONMENT = orig_env
        settings.TEST_WEBHOOK_SECRET = orig_sec

def test_connected_mode_mock_dispatcher_forbidden():
    orig_env = settings.ENVIRONMENT
    try:
        settings.ENVIRONMENT = "connected"
        resp = client.post(
            "/api/v1/telegram/mock-dispatcher",
            headers={"X-Test-Token": settings.TEST_WEBHOOK_SECRET},
            json={
                "chat_id": 12345,
                "text": "Спроба виклику моку в Connected mode"
            }
        )
        assert resp.status_code == 403
        assert "Mock Telegram Dispatcher is disabled" in resp.json()["detail"]
    finally:
        settings.ENVIRONMENT = orig_env

def test_telegram_duplicate_update_dropped():
    orig_env = settings.ENVIRONMENT
    try:
        settings.ENVIRONMENT = "demo"
        update_id = int(time.time() * 1000) % 90000000 + random.randint(10000, 99999)
        # 1. First execution
        resp1 = client.post(
            "/api/v1/telegram/inbox/guard",
            headers={"X-Test-Token": settings.TEST_WEBHOOK_SECRET},
            json={
                "update_id": update_id,
                "chat_id": 12345,
                "text": "Перевірка дублікатів",
                "is_test": True
            }
        )
        assert resp1.status_code == 200
        assert resp1.json()["action"] == "EXECUTE"

        # 2. Second execution with identical update_id
        resp2 = client.post(
            "/api/v1/telegram/inbox/guard",
            headers={"X-Test-Token": settings.TEST_WEBHOOK_SECRET},
            json={
                "update_id": update_id,
                "chat_id": 12345,
                "text": "Перевірка дублікатів",
                "is_test": True
            }
        )
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["action"] == "DROP_DUPLICATE"
        assert data2["status"] == "DUPLICATE"
    finally:
        settings.ENVIRONMENT = orig_env

def test_test_webhook_cannot_route_to_real_telegram():
    resp = client.post(
        "/api/v1/telegram/send-message",
        headers={"X-Internal-Token": settings.INTERNAL_API_KEY},
        json={
            "chat_id": 12345,
            "text": "Спроба відправити тест у реальний Telegram",
            "is_test": True # Marked as test
        }
    )
    assert resp.status_code == 403
    assert "Test webhook ingress cannot route or dispatch messages to real Telegram" in resp.json()["detail"]

def test_real_telegram_send_requires_credentials_and_internal_auth():
    orig_token = settings.TELEGRAM_BOT_TOKEN
    try:
        # Without credentials:
        settings.TELEGRAM_BOT_TOKEN = None
        resp = client.post(
            "/api/v1/telegram/send-message",
            headers={"X-Internal-Token": settings.INTERNAL_API_KEY},
            json={
                "chat_id": 12345,
                "text": "Реальне повідомлення",
                "is_test": False
            }
        )
        assert resp.status_code == 422
        assert "Telegram Bot credentials not configured" in resp.json()["detail"]

        # Without internal authorization:
        resp_unauth = client.post(
            "/api/v1/telegram/send-message",
            headers={"X-Internal-Token": "WrongInternalKey"},
            json={
                "chat_id": 12345,
                "text": "Реальне повідомлення",
                "is_test": False
            }
        )
        assert resp_unauth.status_code == 401
    finally:
        settings.TELEGRAM_BOT_TOKEN = orig_token

def test_guard_response_contains_zero_secrets():
    """Verify TelegramInboxGuardResponse strictly contains only non-sensitive business fields."""
    orig_env = settings.ENVIRONMENT
    try:
        settings.ENVIRONMENT = "demo"
        resp = client.post(
            "/api/v1/telegram/inbox/guard",
            headers={"X-Test-Token": settings.TEST_WEBHOOK_SECRET},
            json={
                "update_id": 999109,
                "chat_id": 12345,
                "text": "Перевірка відсутності секретів у guard",
                "is_test": True
            }
        )
        assert resp.status_code == 200
        data = resp.json()
        # Strictly verify exact fields
        allowed_keys = {"action", "status", "update_id", "chat_id", "text", "is_test", "cached_response"}
        assert set(data.keys()) == allowed_keys, f"Guard response leaked unexpected fields: {set(data.keys()) - allowed_keys}"
        assert "test_token" not in data
        assert "internal_token" not in data
        assert "token" not in data
    finally:
        settings.ENVIRONMENT = orig_env

def test_workflow_export_contains_zero_plaintext_tokens():
    """Verify unified_telegram_router.json contains zero static plaintext tokens or secrets."""
    import json
    with open("SMART_ENTERPRISE/automation/n8n/unified_telegram_router.json", "r", encoding="utf-8") as f:
        content = f.read()

    assert "RentkomTestSecret2026" not in content
    assert "RentkomInternalServiceKey2026" not in content
    if settings.INTERNAL_API_KEY:
        assert settings.INTERNAL_API_KEY not in content
    if settings.TEST_WEBHOOK_SECRET:
        assert settings.TEST_WEBHOOK_SECRET not in content

def test_n8n_execution_output_contains_zero_plaintext_tokens():
    """Verify n8n SQLite execution_data contains zero plaintext tokens in node output payloads."""
    import os
    import sqlite3
    db_path = os.path.expanduser("~/.n8n/database.sqlite")
    if not os.path.exists(db_path):
        pytest.skip("n8n sqlite not found locally")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    row = cur.execute("""
        SELECT data FROM execution_data
        WHERE executionId IN (
            SELECT id FROM execution_entity WHERE workflowId = 'RentkomTelegramUnified01'
        )
        ORDER BY executionId DESC LIMIT 1
    """).fetchone()
    conn.close()

    if row and row[0]:
        payload_str = row[0]
        assert "RentkomTestSecret2026" not in payload_str
        assert "RentkomInternalServiceKey2026" not in payload_str
        if settings.INTERNAL_API_KEY:
            assert settings.INTERNAL_API_KEY not in payload_str
