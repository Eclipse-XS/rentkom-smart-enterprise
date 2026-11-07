import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
import pytest
from core.config import settings

WEBHOOK_URL = "http://localhost:5678/webhook/rentkom-unified-router-test"
N8N_POSTGRES_CONTAINER = os.getenv("N8N_POSTGRES_CONTAINER", "rentkom-postgres")


def _docker_psql_query(sql: str) -> str:
    try:
        res = subprocess.run(
            [
                "docker", "exec", N8N_POSTGRES_CONTAINER,
                "psql", "-U", "postgres", "-d", "lab4_real_estate",
                "-t", "-A", "-F", "\t", "-c", sql
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
        )
        if res.returncode == 0:
            return res.stdout.strip()
    except Exception:
        pass
    return ""


def _set_bot_mode(mode: str):
    req = urllib.request.Request(
        "http://127.0.0.1:8000/api/v1/telegram/mode",
        data=json.dumps({"mode": mode}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Internal-Token": settings.INTERNAL_API_KEY}
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def _require_live_n8n():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        if sock.connect_ex(("127.0.0.1", 5678)) != 0:
            pytest.skip("Live n8n instance on 127.0.0.1:5678 is not running; skipping live n8n HTTP webhook test.")
    finally:
        sock.close()


def send_to_router(message_text, message_id=None, chat_id=12345):
    if message_id is None:
        message_id = int(time.time() * 10000) % 9000000 + 1000000
    payload = {
        "update_id": message_id,
        "message": {
            "message_id": message_id,
            "text": message_text,
            "chat": {"id": chat_id},
            "from": {"id": chat_id, "first_name": "ForensicTester"}
        }
    }
    req = urllib.request.Request(
        WEBHOOK_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Test-Token": settings.TEST_WEBHOOK_SECRET
        }
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return message_id, resp.status, resp.read().decode("utf-8")


def wait_for_inbox_completion(update_id: int, timeout_sec: float = 20.0):
    start = time.time()
    while time.time() - start < timeout_sec:
        out = _docker_psql_query(
            f"SELECT status, response_payload->>'response_text' FROM telegram_inbox WHERE update_id = {update_id};"
        )
        if out and "\t" in out:
            parts = out.split("\t", 1)
            status, text_resp = parts[0], parts[1]
            if status == "COMPLETED":
                return status, text_resp
        time.sleep(0.5)
    return None, None


@pytest.mark.integration
def test_router_scenario_1_start_menu():
    """Verify /start command executes through n8n to Greeting & Persistent Keyboard in LR2 mode."""
    _require_live_n8n()
    _set_bot_mode("LR2")
    up_id, status, _ = send_to_router("/start")
    assert status == 200
    st, resp_text = wait_for_inbox_completion(up_id, timeout_sec=15)
    assert st == "COMPLETED"
    assert "Rentkom" in resp_text


@pytest.mark.integration
def test_router_scenario_2_buying_process():
    """Verify buying button routes to Buying Process Handler in LR2 mode."""
    _require_live_n8n()
    _set_bot_mode("LR2")
    up_id, status, _ = send_to_router("🏠 Купівля та продаж")
    assert status == 200
    st, resp_text = wait_for_inbox_completion(up_id, timeout_sec=15)
    assert st == "COMPLETED"
    assert "Купівля та продаж нерухомості" in resp_text


@pytest.mark.integration
def test_router_scenario_3_landlord_services():
    """Verify renting button routes to Landlord Services Handler in LR2 mode."""
    _require_live_n8n()
    _set_bot_mode("LR2")
    up_id, status, _ = send_to_router("🔑 Послуги для орендодавців")
    assert status == 200
    st, resp_text = wait_for_inbox_completion(up_id, timeout_sec=15)
    assert st == "COMPLETED"
    assert "Послуги для орендодавців" in resp_text


@pytest.mark.integration
def test_router_scenario_4_and_5_rag_query():
    """Verify free question triggers RAG pipeline and returns grounded answer in LR6 mode."""
    _require_live_n8n()
    _set_bot_mode("LR6")
    try:
        up_id, status, _ = send_to_router("Як перевірити документи на квартиру?")
        assert status == 200
        st, resp_text = wait_for_inbox_completion(up_id, timeout_sec=120)

        assert st == "COMPLETED"
        assert len(resp_text) > 20
        assert ("документ" in resp_text.lower() or "базі знань" in resp_text.lower() or "джерела" in resp_text.lower())
    finally:
        _set_bot_mode("LR2")
