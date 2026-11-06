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


@pytest.mark.parametrize("action", ["SHOW_ADMIN", "SHOW_SELECTOR", "SHOW_NOTIFICATIONS"])
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
    assert response.json()["response_text"] == "Недостатньо прав."
    assert client.get("/api/v1/telegram/mode").json()["mode"] == "LR2"


def test_forged_notification_callback_is_rejected_without_state_change(client, db_session):
    set_lr3_notifications_enabled(False, db_session)
    response = client.post(
        "/api/v1/telegram/mode/telegram-control",
        json=_payload(
            "SET_NOTIFICATIONS", user_id=222, chat_id=222,
            callback_data="notifications:ON", callback_query_id="forged",
        ),
        headers=_headers(),
    )
    assert response.json()["status"] == "FORBIDDEN"
    assert response.json()["response_text"] == "Недостатньо прав."
    panel = client.post(
        "/api/v1/telegram/mode/telegram-control", json=_payload("SHOW_ADMIN"), headers=_headers(),
    )
    assert "Сповіщення адміністратора: OFF" in panel.json()["response_text"]


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


def test_user_resources_contain_exactly_two_public_actions(client):
    with (
        patch.object(settings, "LR3_FEEDBACK_FORM_URL", "https://forms.example/feedback"),
        patch.object(settings, "LR4_CONSULTATION_FORM_URL", "https://forms.example/consultation"),
        patch.object(settings, "LR3_SHEET_URL", "https://sheets.example/private-feedback"),
        patch.object(settings, "LR4_SHEET_URL", "https://sheets.example/private-requests"),
        patch.object(settings, "GRAFANA_URL", "https://grafana.example/private"),
        patch.object(settings, "N8N_URL", "https://n8n.example/private"),
        patch.object(settings, "FASTAPI_DOCS_URL", "https://api.example/private-docs"),
        patch.object(settings, "QDRANT_DASHBOARD_URL", "https://qdrant.example/private"),
    ):
        response = client.post(
            "/api/v1/telegram/mode/telegram-control",
            json=_payload("SHOW_RESOURCES", user_id=111, chat_id=111), headers=_headers(),
        )
    data = response.json()
    assert data["status"] == "RESOURCES_SENT"
    assert data["authorized"] is False
    assert data["response_text"] == "Rentkom | Корисні посилання"
    assert data["reply_markup"]["inline_keyboard"] == [
        [{"text": "Залишити відгук", "url": "https://forms.example/feedback"}],
        [{"text": "Записатися на консультацію", "url": "https://forms.example/consultation"}],
    ]
    serialized = str(data)
    for private_value in (
        "private-feedback", "private-requests", "grafana.example", "n8n.example",
        "private-docs", "qdrant.example",
    ):
        assert private_value not in serialized


def test_admin_resources_extend_public_menu_without_local_url_buttons(client):
    with (
        patch.object(settings, "LR3_FEEDBACK_FORM_URL", "https://forms.example/feedback"),
        patch.object(settings, "LR4_CONSULTATION_FORM_URL", "https://forms.example/consultation"),
        patch.object(settings, "LR3_SHEET_URL", "https://sheets.example/feedback"),
        patch.object(settings, "LR4_SHEET_URL", "https://sheets.example/requests"),
        patch.object(settings, "GRAFANA_URL", "http://127.0.0.1:3000"),
        patch.object(settings, "N8N_URL", "http://localhost:5678"),
        patch.object(settings, "FASTAPI_DOCS_URL", "https://api.example/docs"),
        patch.object(settings, "QDRANT_DASHBOARD_URL", None),
    ):
        response = client.post(
            "/api/v1/telegram/mode/telegram-control", json=_payload("SHOW_RESOURCES"), headers=_headers(),
        )
    data = response.json()
    assert data["authorized"] is True
    assert "Дані та аналітика" in data["response_text"]
    assert "Інфраструктура" in data["response_text"]
    assert "Локальний доступ: Grafana, n8n." in data["response_text"]
    assert "127.0.0.1" not in str(data)
    assert "localhost" not in str(data)
    buttons = [row[0] for row in data["reply_markup"]["inline_keyboard"]]
    assert [button["text"] for button in buttons] == [
        "Залишити відгук", "Записатися на консультацію",
        "Google Sheets: відгуки", "Google Sheets: заявки", "FastAPI Swagger",
    ]


def test_admin_resources_include_all_configured_remote_actions(client):
    configured = {
        "LR3_FEEDBACK_FORM_URL": "https://forms.example/feedback",
        "LR4_CONSULTATION_FORM_URL": "https://forms.example/consultation",
        "LR3_SHEET_URL": "https://sheets.example/feedback",
        "LR4_SHEET_URL": "https://sheets.example/requests",
        "GRAFANA_URL": "https://grafana.example/admin",
        "N8N_URL": "https://n8n.example/admin",
        "FASTAPI_DOCS_URL": "https://api.example/docs",
        "QDRANT_DASHBOARD_URL": "https://qdrant.example/dashboard",
    }
    patches = [patch.object(settings, key, value) for key, value in configured.items()]
    for active_patch in patches:
        active_patch.start()
    try:
        response = client.post(
            "/api/v1/telegram/mode/telegram-control",
            json=_payload("SHOW_RESOURCES"), headers=_headers(),
        )
    finally:
        for active_patch in reversed(patches):
            active_patch.stop()

    data = response.json()
    assert data["authorized"] is True
    buttons = [row[0] for row in data["reply_markup"]["inline_keyboard"]]
    assert [button["text"] for button in buttons] == [
        "Залишити відгук",
        "Записатися на консультацію",
        "Google Sheets: відгуки",
        "Google Sheets: заявки",
        "Grafana",
        "n8n",
        "FastAPI Swagger",
        "Qdrant Dashboard",
    ]


@pytest.mark.parametrize(
    ("user_id", "chat_id", "chat_type"),
    [
        (111, 111, "private"),
        (None, 111, "private"),
        (111, None, "private"),
        (None, None, "group"),
    ],
)
def test_resources_never_expose_admin_urls_to_unauthorized_identity(
    client, user_id, chat_id, chat_type,
):
    admin_user = int(settings.TELEGRAM_ADMIN_USER_ID)
    admin_chat = int(settings.TELEGRAM_ADMIN_CHAT_ID)
    payload = _payload(
        "SHOW_RESOURCES",
        user_id=admin_user if user_id is None else user_id,
        chat_id=admin_chat if chat_id is None else chat_id,
    )
    payload["chat_type"] = chat_type
    with (
        patch.object(settings, "LR3_SHEET_URL", "https://admin.example/feedback"),
        patch.object(settings, "LR4_SHEET_URL", "https://admin.example/requests"),
        patch.object(settings, "GRAFANA_URL", "https://admin.example/grafana"),
        patch.object(settings, "N8N_URL", "https://admin.example/n8n"),
        patch.object(settings, "FASTAPI_DOCS_URL", "https://admin.example/docs"),
        patch.object(settings, "QDRANT_DASHBOARD_URL", "https://admin.example/qdrant"),
    ):
        response = client.post(
            "/api/v1/telegram/mode/telegram-control", json=payload, headers=_headers(),
        )
    data = response.json()
    assert data["authorized"] is False
    assert len(data["reply_markup"]["inline_keyboard"]) == 2
    assert "admin.example" not in str(data)


def test_missing_public_resource_is_a_message_not_broken_button(client):
    with (
        patch.object(settings, "LR3_FEEDBACK_FORM_URL", ""),
        patch.object(settings, "LR4_CONSULTATION_FORM_URL", "https://forms.example/consultation"),
    ):
        response = client.post(
            "/api/v1/telegram/mode/telegram-control",
            json=_payload("SHOW_RESOURCES", user_id=111, chat_id=111), headers=_headers(),
        )
    data = response.json()
    assert "Недоступно: Залишити відгук." in data["response_text"]
    assert len(data["reply_markup"]["inline_keyboard"]) == 1


@pytest.mark.parametrize("command", ["/admin", "/mode", "/notifications"])
def test_mock_router_denies_user_admin_commands(client, command):
    response = client.post(
        "/api/v1/telegram/webhook-mock",
        json={"update_id": abs(hash(command)) % 100000 + 700000, "message": {"chat": {"id": 111}, "from": {"id": 111}, "text": command}},
    )
    assert response.status_code == 200
    assert response.json()["route"] == "Access Denied"


def test_mock_router_user_resources_are_public_only(client):
    response = client.post(
        "/api/v1/telegram/webhook-mock",
        json={"update_id": 799991, "message": {"chat": {"id": 111, "type": "private"}, "from": {"id": 111}, "text": "/resources"}},
    )
    assert response.status_code == 200
    assert response.json()["route"] == "Resources"
    assert len(response.json()["reply_markup"]["inline_keyboard"]) == 2


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
