from unittest.mock import patch
from unittest.mock import AsyncMock

import pytest

from core.config import settings
from core.api.v1.endpoints.telegram import set_telegram_bot_mode, set_lr3_notifications_enabled


def _headers():
    return {"X-Internal-Token": settings.INTERNAL_API_KEY}


def _payload(action, user_id=None, chat_id=None, **extra):
    return {
        "action": action,
        "telegram_user_id": user_id if user_id is not None else int(settings.TELEGRAM_ADMIN_USER_ID),
        "chat_id": chat_id if chat_id is not None else int(settings.TELEGRAM_ADMIN_CHAT_ID),
        "chat_type": "private",
        "is_test": True,
        **extra,
    }


@pytest.mark.parametrize("action", ["SHOW_ADMIN", "SHOW_SELECTOR", "SHOW_NOTIFICATIONS", "SHOW_RESOURCES"])
def test_user_cannot_execute_admin_actions(client, action):
    response = client.post(
        "/api/v1/telegram/mode/telegram-control",
        json=_payload(action, user_id=111, chat_id=111), headers=_headers(),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "FORBIDDEN"
    assert response.json()["authorized"] is False


def test_forged_admin_callback_is_rejected_without_state_change(client, db_session):
    set_telegram_bot_mode("LR2", db_session)
    response = client.post(
        "/api/v1/telegram/mode/telegram-control",
        json=_payload("SELECT_MODE", user_id=111, chat_id=111, callback_data="mode:LR6", callback_query_id="x"),
        headers=_headers(),
    )
    assert response.json()["status"] == "FORBIDDEN"
    assert client.get("/api/v1/telegram/mode").json()["mode"] == "LR2"


def test_admin_panel_reports_global_state(client, db_session):
    set_telegram_bot_mode("LR6", db_session)
    set_lr3_notifications_enabled(True, db_session)
    response = client.post(
        "/api/v1/telegram/mode/telegram-control", json=_payload("SHOW_ADMIN"), headers=_headers(),
    )
    assert response.status_code == 200
    assert response.json()["authorized"] is True
    assert "Поточний глобальний режим: LR6" in response.json()["response_text"]
    assert "Сповіщення адміністратора: ON" in response.json()["response_text"]


def test_resources_hide_secrets_and_mark_missing_and_local(client):
    with patch.object(settings, "LR3_SHEET_URL", None), patch.object(settings, "GRAFANA_URL", "http://127.0.0.1:3000"):
        response = client.post(
            "/api/v1/telegram/mode/telegram-control", json=_payload("SHOW_RESOURCES"), headers=_headers(),
        )
    text = response.json()["response_text"]
    assert "LR3 Google Sheets: NOT CONFIGURED" in text
    assert "Grafana: http://127.0.0.1:3000 — LOCAL ONLY" in text
    assert settings.INTERNAL_API_KEY not in text
    assert "TELEGRAM_BOT_TOKEN" not in text
    if settings.TELEGRAM_BOT_TOKEN:
        assert settings.TELEGRAM_BOT_TOKEN not in text


@pytest.mark.parametrize("command", ["/admin", "/mode", "/notifications", "/resources"])
def test_mock_router_denies_user_admin_commands(client, command):
    response = client.post(
        "/api/v1/telegram/webhook-mock",
        json={"update_id": abs(hash(command)) % 100000 + 700000, "message": {"chat": {"id": 111}, "from": {"id": 111}, "text": command}},
    )
    assert response.status_code == 200
    assert response.json()["route"] == "Access Denied"


def test_admin_identity_requires_both_user_and_private_chat(client):
    wrong_chat = client.post(
        "/api/v1/telegram/mode/telegram-control",
        json=_payload("SHOW_ADMIN", chat_id=111), headers=_headers(),
    )
    wrong_user = client.post(
        "/api/v1/telegram/mode/telegram-control",
        json=_payload("SHOW_ADMIN", user_id=111), headers=_headers(),
    )
    group_chat = client.post(
        "/api/v1/telegram/mode/telegram-control",
        json={**_payload("SHOW_ADMIN"), "chat_type": "group"}, headers=_headers(),
    )
    assert wrong_chat.json()["status"] == "FORBIDDEN"
    assert wrong_user.json()["status"] == "FORBIDDEN"
    assert group_chat.json()["status"] == "FORBIDDEN"


def test_stale_owner_completion_returns_conflict(client):
    with patch(
        "core.api.v1.endpoints.telegram.telegram_idempotency.finish_processing",
        new=AsyncMock(return_value=False),
    ):
        response = client.post(
            "/api/v1/telegram/inbox/complete",
            json={
                "update_id": 880001,
                "route": "test",
                "response_text": "test",
                "owner_token": "stale-owner",
            },
            headers={"X-Test-Token": settings.TEST_WEBHOOK_SECRET},
        )
    assert response.status_code == 409
    assert response.json()["detail"] == "Processing lease ownership was lost."
