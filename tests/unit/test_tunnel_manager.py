import json
from unittest.mock import MagicMock, patch

import pytest

from tools import manage_tunnel


def _response(payload):
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = json.dumps(payload).encode()
    return response


def _owned_state(pid=10):
    return {"owner_id": "rentkom", "pid": pid, "url": "https://live.trycloudflare.com",
            "local_target": manage_tunnel.DEFAULT_LOCAL_TARGET, "process_created": "stamp"}


def _cloudflared_info(pid=10, created="stamp"):
    return {"pid": pid, "name": "cloudflared.exe", "created": created,
            "command_line": f"cloudflared tunnel --url {manage_tunnel.DEFAULT_LOCAL_TARGET}"}


def test_health_requires_reachable_endpoint_not_stale_log():
    with patch("tools.manage_tunnel.urllib.request.urlopen", side_effect=OSError("unreachable")):
        assert not manage_tunnel.check_tunnel_health("https://stale.trycloudflare.com")


def test_supervisor_lock_prevents_concurrent_start(monkeypatch):
    lock = MagicMock()
    lock.acquire.return_value = False
    monkeypatch.setattr(manage_tunnel, "SupervisorLock", lambda: lock)
    start = MagicMock()
    monkeypatch.setattr(manage_tunnel, "start_tunnel", start)
    manage_tunnel.run_supervisor()
    start.assert_not_called()


def test_ownership_is_recorded_before_network(monkeypatch, tmp_path):
    lock = MagicMock(); lock.acquire.return_value = True
    monkeypatch.setattr(manage_tunnel, "SupervisorLock", lambda: lock)
    monkeypatch.setattr(manage_tunnel, "get_process_info", lambda pid: {"created": "supervisor"})
    saved = []
    monkeypatch.setattr(manage_tunnel, "load_state", lambda: saved[-1] if saved else {})
    monkeypatch.setattr(manage_tunnel, "save_state", lambda state: saved.append(dict(state)))
    monkeypatch.setattr(manage_tunnel, "detect_existing_tunnel", lambda: (None, None))
    def fail_start(**kwargs):
        assert saved[-1]["supervisor_pid"]
        assert saved[-1]["owner_id"]
        raise RuntimeError("network timeout")
    monkeypatch.setattr(manage_tunnel, "start_tunnel", fail_start)
    monkeypatch.setattr(manage_tunnel, "_cleanup_owned_child", lambda: None)
    monkeypatch.setattr(manage_tunnel, "STATE_FILE", tmp_path / "state.json")
    with pytest.raises(RuntimeError, match="network timeout"):
        manage_tunnel.run_supervisor()
    lock.release.assert_called_once()


def test_stale_pid_or_reused_pid_is_rejected(monkeypatch):
    state = _owned_state()
    monkeypatch.setattr(manage_tunnel, "load_state", lambda: state)
    monkeypatch.setattr(manage_tunnel, "get_process_info", lambda pid: _cloudflared_info(created="different"))
    assert manage_tunnel.detect_existing_tunnel() == (None, None)


def test_healthy_owned_tunnel_is_reused(monkeypatch):
    state = _owned_state()
    monkeypatch.setattr(manage_tunnel, "load_state", lambda: state)
    monkeypatch.setattr(manage_tunnel, "get_process_info", lambda pid: _cloudflared_info())
    monkeypatch.setattr(manage_tunnel, "check_tunnel_health", lambda url: True)
    assert manage_tunnel.detect_existing_tunnel() == (10, state["url"])


def test_legacy_unrestricted_n8n_tunnel_is_not_reused(monkeypatch):
    state = _owned_state()
    state["local_target"] = "http://127.0.0.1:5678"
    monkeypatch.setattr(manage_tunnel, "load_state", lambda: state)
    monkeypatch.setattr(manage_tunnel, "get_process_info", lambda pid: _cloudflared_info())
    monkeypatch.setattr(manage_tunnel, "check_tunnel_health", lambda url: True)
    assert manage_tunnel.detect_existing_tunnel() == (None, None)


def test_dns_failure_is_reported_and_only_spawned_pid_is_killed(monkeypatch, tmp_path):
    monkeypatch.setattr(manage_tunnel, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(manage_tunnel, "LOG_FILE", tmp_path / "cloudflared.log")
    monkeypatch.setattr(manage_tunnel, "STATE_FILE", tmp_path / "tunnel_state.json")
    monkeypatch.setattr(manage_tunnel, "find_cloudflared_binary", lambda: "cloudflared")
    process = MagicMock(pid=44); process.poll.return_value = 1
    monkeypatch.setattr(manage_tunnel.subprocess, "Popen", lambda *a, **k: process)
    monkeypatch.setattr(manage_tunnel, "get_process_info", lambda pid: {})
    killed = []
    monkeypatch.setattr(manage_tunnel, "terminate_pid", lambda pid, force=False: killed.append(pid))
    (tmp_path / "cloudflared.log").write_text("lookup api.trycloudflare.com: no such host")
    with pytest.raises(RuntimeError, match="no such host"):
        manage_tunnel.start_cloudflare_tunnel(timeout=0, max_retries=1)
    assert killed == []  # exited itself; no global process cleanup


def test_cloudflare_timeout_is_reported(monkeypatch, tmp_path):
    monkeypatch.setattr(manage_tunnel, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(manage_tunnel, "LOG_FILE", tmp_path / "cloudflared.log")
    monkeypatch.setattr(manage_tunnel, "STATE_FILE", tmp_path / "tunnel_state.json")
    monkeypatch.setattr(manage_tunnel, "find_cloudflared_binary", lambda: "cloudflared")
    process = MagicMock(pid=45); process.poll.return_value = 1
    monkeypatch.setattr(manage_tunnel.subprocess, "Popen", lambda *a, **k: process)
    monkeypatch.setattr(manage_tunnel, "get_process_info", lambda pid: {})
    (tmp_path / "cloudflared.log").write_text("context deadline exceeded")
    with pytest.raises(RuntimeError, match="context deadline exceeded"):
        manage_tunnel.start_cloudflare_tunnel(timeout=0, max_retries=1)


def test_sync_preserves_pending_updates_and_allowed_updates(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_SECRET_TOKEN", "secret")
    current = {"ok": True, "result": {"url": "https://old/x", "pending_update_count": 8,
                                        "allowed_updates": ["message", "callback_query"],
                                        "last_error_date": 1}}
    registered = {"ok": True, "result": True}
    verified = {"ok": True, "result": {"url": f"https://new.trycloudflare.com{manage_tunnel.WEBHOOK_PATH}",
                                         "pending_update_count": 8,
                                         "allowed_updates": ["message", "callback_query"]}}
    responses, calls = [current, registered, verified], []
    def urlopen(request, timeout):
        calls.append(request)
        return _response(responses.pop(0))
    with patch("tools.manage_tunnel.urllib.request.urlopen", side_effect=urlopen), \
            patch.object(manage_tunnel, "save_state"), patch.object(manage_tunnel, "load_state", return_value={}):
        ok, _ = manage_tunnel.sync_telegram_webhook("https://new.trycloudflare.com")
    assert ok
    body = json.loads(calls[1].data)
    assert body["allowed_updates"] == ["message", "callback_query"]
    assert "drop_pending_updates" not in body


def test_webhook_registration_failure(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr(manage_tunnel, "get_telegram_webhook_info", lambda: {"url": "old"})
    with patch("tools.manage_tunnel.urllib.request.urlopen", side_effect=OSError("blocked")):
        ok, reason = manage_tunnel.sync_telegram_webhook("https://new.trycloudflare.com")
    assert not ok and "setWebhook failed" in reason


def test_url_rotation_requires_webhook_update(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    old = f"https://old.trycloudflare.com{manage_tunnel.WEBHOOK_PATH}"
    monkeypatch.setattr(manage_tunnel, "get_telegram_webhook_info", lambda: {"url": old})
    with patch("tools.manage_tunnel.urllib.request.urlopen", return_value=_response({"ok": False, "description": "rejected"})):
        ok, _ = manage_tunnel.sync_telegram_webhook("https://new.trycloudflare.com")
    assert not ok


def test_safe_stop_does_not_kill_unrelated_processes(monkeypatch, tmp_path):
    monkeypatch.setattr(manage_tunnel, "STATE_FILE", tmp_path / "state.json")
    state = _owned_state(pid=21)
    state.update({"supervisor_pid": 20, "supervisor_process_created": "sup"})
    monkeypatch.setattr(manage_tunnel, "load_state", lambda: state)
    def info(pid):
        if pid == 20:
            return {"pid": 20, "name": "python.exe", "created": "sup",
                    "command_line": "python tools/manage_tunnel.py --action daemon"}
        if pid == 21:
            return _cloudflared_info(pid=21)
        return {"pid": pid, "name": "cloudflared.exe", "created": "other",
                "command_line": "cloudflared tunnel --url http://unrelated:9000"}
    monkeypatch.setattr(manage_tunnel, "get_process_info", info)
    killed = []
    monkeypatch.setattr(manage_tunnel, "terminate_pid", lambda pid, force=False: killed.append(pid))
    manage_tunnel.stop_tunnel()
    assert killed == [20, 21]
    assert 99 not in killed


def test_unicode_and_space_paths_are_passed_as_one_argument(monkeypatch, tmp_path):
    binary = str(tmp_path / "Каталог з пробілами" / "cloudflared.exe")
    monkeypatch.setattr(manage_tunnel, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(manage_tunnel, "LOG_FILE", tmp_path / "cloudflared.log")
    monkeypatch.setattr(manage_tunnel, "STATE_FILE", tmp_path / "tunnel_state.json")
    monkeypatch.setattr(manage_tunnel, "find_cloudflared_binary", lambda: binary)
    captured = {}
    process = MagicMock(pid=50); process.poll.return_value = 1
    def popen(args, **kwargs):
        captured["args"] = args
        return process
    monkeypatch.setattr(manage_tunnel.subprocess, "Popen", popen)
    monkeypatch.setattr(manage_tunnel, "get_process_info", lambda pid: {})
    with pytest.raises(RuntimeError):
        manage_tunnel.start_cloudflare_tunnel(timeout=0, max_retries=1)
    assert captured["args"][0] == binary


def test_supervisor_crash_releases_lock(tmp_path):
    first = manage_tunnel.SupervisorLock(tmp_path / "lock")
    second = manage_tunnel.SupervisorLock(tmp_path / "lock")
    assert first.acquire()
    assert not second.acquire()
    first.release()
    assert second.acquire()
    second.release()


def test_crash_recovery_kills_only_recorded_owned_child(monkeypatch):
    state = _owned_state(pid=77)
    monkeypatch.setattr(manage_tunnel, "load_state", lambda: state)
    monkeypatch.setattr(manage_tunnel, "get_process_info", lambda pid: _cloudflared_info(pid=pid))
    killed = []
    monkeypatch.setattr(manage_tunnel, "terminate_pid", lambda pid, force=False: killed.append(pid))
    manage_tunnel._cleanup_owned_child()
    assert killed == [77]


def test_unreachable_cloudflare_selects_localhost_run(monkeypatch):
    monkeypatch.setattr(manage_tunnel, "tcp_endpoint_reachable", lambda host, port: False)
    fallback = MagicMock(return_value=(12, "https://abc.lhr.life"))
    monkeypatch.setattr(manage_tunnel, "start_localhost_run_tunnel", fallback)
    assert manage_tunnel.start_tunnel() == (12, "https://abc.lhr.life")
    fallback.assert_called_once()


def test_localhost_run_process_ownership_is_provider_specific():
    state = {"owner_id": "rentkom", "provider": "localhost_run", "pid": 88,
             "process_created": "stamp", "local_target": manage_tunnel.DEFAULT_LOCAL_TARGET}
    owned = {"pid": 88, "name": "ssh.exe", "created": "stamp",
             "command_line": "ssh -T -R 80:127.0.0.1:8765 nokey@localhost.run"}
    unrelated = dict(owned, command_line="ssh -R 80:127.0.0.1:9000 other@example.com")
    assert manage_tunnel.is_owned_tunnel(state, owned)
    assert not manage_tunnel.is_owned_tunnel(state, unrelated)
