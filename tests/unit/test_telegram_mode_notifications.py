from unittest.mock import AsyncMock, patch

import pytest

from core.api.v1.endpoints.telegram import get_mode_notification, set_telegram_bot_mode


EXPECTED_LR2_TEXT = (
    "🔄 Режим переключено на ЛР2: Сценарний чат-бот агентства «Рентком».\n\n"
    "Сценарні кнопки активовано. Виберіть потрібний розділ меню нижче:"
)
EXPECTED_LR6_TEXT = (
    "🔄 Режим переключено на ЛР6: Інтелектуальний RAG AI-консультант агентства «Рентком» "
    "(база знань Qdrant + Ollama qwen3:4b-instruct).\n\n"
    "Сценарну клавіатуру вимкнено. Ви можете ставити будь-які запитання щодо купівлі, "
    "оренди, перевірки документів або правил агентства."
)


@pytest.fixture(autouse=True)
def _restore_lr2(db_session):
    set_telegram_bot_mode("LR2", db_session)
    yield
    set_telegram_bot_mode("LR2", db_session)


def _auth_headers():
    from core.config import settings
    return {"X-Internal-Token": settings.INTERNAL_API_KEY}


def test_exact_restored_notification_text_and_markup():
    lr2_text, lr2_markup = get_mode_notification("LR2")
    lr6_text, lr6_markup = get_mode_notification("LR6")
    assert lr2_text == EXPECTED_LR2_TEXT
    assert lr2_markup == {
        "keyboard": [[
            {"text": "Процес купівлі/продажу"},
            {"text": "Послуги для орендодавців"},
        ]],
        "resize_keyboard": True,
    }
    assert lr6_text == EXPECTED_LR6_TEXT
    assert lr6_markup == {"remove_keyboard": True}


def test_mode_api_rejects_unauthorized_change(client):
    response = client.post("/api/v1/telegram/mode", json={"mode": "LR6"})
    assert response.status_code == 403


def test_lr2_to_lr6_delivers_keyboard_removal_after_persistence(client):
    with patch(
        "core.api.v1.endpoints.telegram.send_mode_notification",
        new_callable=AsyncMock,
    ) as deliver:
        deliver.return_value = 101
        response = client.post(
            "/api/v1/telegram/mode", json={"mode": "LR6"}, headers=_auth_headers()
        )
    assert response.status_code == 200
    assert response.json()["notification_status"] == "DELIVERED"
    deliver.assert_awaited_once()
    assert deliver.await_args.args[1] == "LR6"


def test_lr6_to_lr2_delivers_original_lr2_keyboard(client):
    with patch(
        "core.api.v1.endpoints.telegram.send_mode_notification",
        new_callable=AsyncMock,
    ) as deliver:
        deliver.return_value = 102
        client.post("/api/v1/telegram/mode", json={"mode": "LR6"}, headers=_auth_headers())
        deliver.reset_mock()
        response = client.post(
            "/api/v1/telegram/mode", json={"mode": "LR2"}, headers=_auth_headers()
        )
    assert response.json()["notification_status"] == "DELIVERED"
    deliver.assert_awaited_once()
    assert deliver.await_args.args[1] == "LR2"


def test_repeated_mode_is_unchanged_and_not_notified(client):
    with patch(
        "core.api.v1.endpoints.telegram.send_mode_notification",
        new_callable=AsyncMock,
    ) as deliver:
        deliver.return_value = 103
        client.post("/api/v1/telegram/mode", json={"mode": "LR2"}, headers=_auth_headers())
        deliver.reset_mock()
        data = client.post(
            "/api/v1/telegram/mode", json={"mode": "LR2"}, headers=_auth_headers()
        ).json()
        deliver.assert_not_awaited()
    assert data["status"] == "UNCHANGED"
    assert data["changed"] is False
    assert data["notification_status"] == "SKIPPED_ALREADY_ACTIVE"


def test_delivery_failure_reports_partial_success_and_keeps_mode(client):
    with patch(
        "core.api.v1.endpoints.telegram.send_mode_notification",
        new_callable=AsyncMock,
        side_effect=RuntimeError("provider unavailable"),
    ):
        response = client.post(
            "/api/v1/telegram/mode", json={"mode": "LR6"}, headers=_auth_headers()
        )
    data = response.json()
    assert response.status_code == 200
    assert data["status"] == "PARTIAL_SUCCESS"
    assert data["notification_status"] == "FAILED"
    assert "provider unavailable" in data["notification_error"]
    assert client.get("/api/v1/telegram/mode").json()["mode"] == "LR6"


def _mode_control_payload(action, callback_data=None, user_id=None):
    from core.config import settings
    admin_id = int(settings.TELEGRAM_ADMIN_USER_ID)
    return {
        "action": action,
        "chat_id": int(settings.TELEGRAM_ADMIN_CHAT_ID),
        "telegram_user_id": user_id if user_id is not None else admin_id,
        "chat_type": "private",
        "callback_query_id": "callback-1" if action == "SELECT_MODE" else None,
        "callback_data": callback_data,
        "is_test": False,
    }


def test_single_mode_command_sends_inline_selector(client):
    async def telegram_result(method, payload):
        assert method == "sendMessage"
        assert payload["reply_markup"]["inline_keyboard"][0][0] == {
            "text": "Сценарний режим (LR2)", "callback_data": "mode:LR2"
        }
        assert payload["reply_markup"]["inline_keyboard"][0][1] == {
            "text": "AI-консультант (LR6)", "callback_data": "mode:LR6"
        }
        return {"ok": True, "result": {"message_id": 501}}

    with patch("core.api.v1.endpoints.telegram._telegram_api", side_effect=telegram_result):
        response = client.post(
            "/api/v1/telegram/mode/telegram-control",
            json=_mode_control_payload("SHOW_SELECTOR"), headers=_auth_headers(),
        )
    assert response.json()["status"] == "SELECTOR_SENT"
    assert response.json()["notification_message_id"] == 501


def test_mode_callback_answers_and_switches_to_lr6(client):
    calls = []

    async def telegram_result(method, payload):
        calls.append((method, payload))
        return {"ok": True, "result": {"message_id": 502}}

    with patch("core.api.v1.endpoints.telegram._telegram_api", side_effect=telegram_result):
        response = client.post(
            "/api/v1/telegram/mode/telegram-control",
            json=_mode_control_payload("SELECT_MODE", "mode:LR6"), headers=_auth_headers(),
        )
    data = response.json()
    assert data["status"] == "SUCCESS"
    assert data["callback_answered"] is True
    assert [call[0] for call in calls] == ["answerCallbackQuery", "sendMessage"]
    assert calls[1][1]["text"] == EXPECTED_LR6_TEXT
    assert calls[1][1]["reply_markup"] == {"remove_keyboard": True}
    assert client.get("/api/v1/telegram/mode").json()["mode"] == "LR6"


def test_repeated_callback_is_unchanged_without_activation_message(client, db_session):
    set_telegram_bot_mode("LR6", db_session)
    calls = []

    async def telegram_result(method, payload):
        calls.append((method, payload))
        return {"ok": True, "result": {"message_id": 503}}

    with patch("core.api.v1.endpoints.telegram._telegram_api", side_effect=telegram_result):
        response = client.post(
            "/api/v1/telegram/mode/telegram-control",
            json=_mode_control_payload("SELECT_MODE", "mode:LR6"), headers=_auth_headers(),
        )
    assert response.json()["status"] == "UNCHANGED"
    assert "Зміни не потрібні" in calls[1][1]["text"]


def test_mode_callback_rejects_wrong_telegram_user(client):
    calls = []

    async def telegram_result(method, payload):
        calls.append((method, payload))
        return {"ok": True, "result": True}

    with patch("core.api.v1.endpoints.telegram._telegram_api", side_effect=telegram_result):
        response = client.post(
            "/api/v1/telegram/mode/telegram-control",
            json=_mode_control_payload("SELECT_MODE", "mode:LR6", user_id=999999),
            headers=_auth_headers(),
        )
    assert response.json()["status"] == "FORBIDDEN"
    assert [call[0] for call in calls] == ["answerCallbackQuery"]
    assert client.get("/api/v1/telegram/mode").json()["mode"] == "LR2"


def test_inbox_guard_normalizes_and_deduplicates_mode_callback(client):
    from core.config import settings
    update = {
        "update_id": 981001,
        "callback_query": {
            "id": "callback-guard-1",
            "from": {"id": int(settings.TELEGRAM_ADMIN_USER_ID)},
            "message": {"chat": {"id": int(settings.TELEGRAM_ADMIN_CHAT_ID)}},
            "data": "mode:LR6",
        },
    }
    payload = {"update_id": update["update_id"], "body": update, "is_test": True}
    headers = {"X-Test-Token": settings.TEST_WEBHOOK_SECRET}
    first = client.post("/api/v1/telegram/inbox/guard", json=payload, headers=headers)
    second = client.post("/api/v1/telegram/inbox/guard", json=payload, headers=headers)
    assert first.status_code == 200
    assert first.json()["mode_action"] == "SELECT_MODE"
    assert first.json()["callback_query_id"] == "callback-guard-1"
    assert first.json()["telegram_user_id"] == int(settings.TELEGRAM_ADMIN_USER_ID)
    assert second.json()["action"] == "DROP_DUPLICATE"
