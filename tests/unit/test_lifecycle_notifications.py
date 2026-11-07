import json
from unittest.mock import patch

from tools import notify_telegram_lifecycle as lifecycle


EXPECTED_STARTUP = (
    "<b>🟢 Rentkom запущено</b>\n\n"
    "Усі основні сервіси доступні.\n"
    "Telegram-бот готовий до роботи."
)
EXPECTED_SHUTDOWN = (
    "<b>🔴 Rentkom зупинено</b>\n\n"
    "Сервер вимкнено.\n"
    "Обробка нових запитів тимчасово недоступна."
)


def test_exact_approved_lifecycle_messages():
    assert lifecycle.MESSAGES["startup"] == EXPECTED_STARTUP
    assert lifecycle.MESSAGES["shutdown"] == EXPECTED_SHUTDOWN


def test_lifecycle_delivery_is_admin_only_html_five_seconds_and_deduplicated(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(lifecycle, "_state_path", lambda: state_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "123456")
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return b'{"ok": true}'

    def fake_urlopen(request, timeout):
        calls.append((json.loads(request.data), timeout))
        return Response()

    monkeypatch.setattr(lifecycle.urllib.request, "urlopen", fake_urlopen)
    with patch.object(lifecycle.sys, "argv", ["notify", "startup"]):
        assert lifecycle.main() == 0
        assert lifecycle.main() == 0

    assert calls == [({
        "chat_id": "123456",
        "text": EXPECTED_STARTUP,
        "parse_mode": "HTML",
    }, 5)]


def test_lifecycle_network_failure_is_non_blocking(tmp_path, monkeypatch):
    monkeypatch.setattr(lifecycle, "_state_path", lambda: tmp_path / "state.json")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "123456")
    monkeypatch.setattr(lifecycle.urllib.request, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError()))
    with patch.object(lifecycle.sys, "argv", ["notify", "shutdown"]):
        assert lifecycle.main() == 0
