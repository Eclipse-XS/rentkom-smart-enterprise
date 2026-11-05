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
            {"text": "🏢 Процес купівлі/продажу"},
            {"text": "🔑 Послуги для орендодавців"},
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
