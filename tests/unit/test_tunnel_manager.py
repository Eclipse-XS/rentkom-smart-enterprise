import io
import json
from unittest.mock import MagicMock, patch

from tools import manage_tunnel


def _response(payload):
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = json.dumps(payload).encode("utf-8")
    return response


def test_health_requires_reachable_endpoint_not_stale_log(tmp_path):
    log = tmp_path / "cloudflared.log"
    log.write_text("Registered tunnel connection\nUnauthorized: Tunnel not found", encoding="utf-8")
    with patch.object(manage_tunnel, "LOG_FILE", log), patch(
        "tools.manage_tunnel.urllib.request.urlopen", side_effect=OSError("unreachable")
    ):
        assert manage_tunnel.check_tunnel_health("https://stale.trycloudflare.com") is False


def test_sync_preserves_pending_updates_and_allowed_updates(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_SECRET_TOKEN", "secret")
    current = {
        "ok": True,
        "result": {
            "url": "https://old.trycloudflare.com/webhook/rentkom-telegram-ingress-webhook",
            "pending_update_count": 8,
            "allowed_updates": ["message", "callback_query"],
            "last_error_date": 1,
        },
    }
    registered = {"ok": True, "result": True}
    verified = {
        "ok": True,
        "result": {"url": "https://new.trycloudflare.com/webhook/rentkom-telegram-ingress-webhook"},
    }
    calls = []

    def urlopen(request, timeout):
        calls.append(request)
        if len(calls) == 1:
            return _response(current)
        if len(calls) == 2:
            return _response(registered)
        return _response(verified)

    with patch("tools.manage_tunnel.urllib.request.urlopen", side_effect=urlopen), patch.object(
        manage_tunnel, "save_state"
    ):
        ok, _ = manage_tunnel.sync_telegram_webhook("https://new.trycloudflare.com")
    assert ok is True
    body = json.loads(calls[1].data.decode("utf-8"))
    assert body["allowed_updates"] == ["message", "callback_query"]
    assert "drop_pending_updates" not in body


def test_detect_existing_tunnel_rejects_stale_url(monkeypatch, tmp_path):
    monkeypatch.setattr(manage_tunnel, "load_state", lambda: {"pid": 10, "url": "https://stale.trycloudflare.com"})
    monkeypatch.setattr(manage_tunnel, "get_running_cloudflared_pids", lambda: [10])
    monkeypatch.setattr(manage_tunnel, "check_tunnel_health", lambda url: False)
    monkeypatch.setattr(manage_tunnel, "LOG_FILE", tmp_path / "missing.log")
    assert manage_tunnel.detect_existing_tunnel() == (None, None)


def test_supervisor_does_not_create_duplicate_process(monkeypatch):
    monkeypatch.setattr(manage_tunnel, "load_state", lambda: {"supervisor_pid": 321})
    monkeypatch.setattr(manage_tunnel, "is_pid_running", lambda pid: True)
    start = MagicMock()
    monkeypatch.setattr(manage_tunnel, "start_tunnel", start)
    manage_tunnel.run_supervisor()
    start.assert_not_called()
