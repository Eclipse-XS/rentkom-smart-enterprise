import json
import os
import sqlite3
import time
import urllib.request
import urllib.error
import pytest
from core.config import settings

WEBHOOK_URL = "http://localhost:5678/webhook/rentkom-unified-router-test"
DB_PATH = os.path.expanduser("~/.n8n/database.sqlite")

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
        return resp.status, resp.read().decode("utf-8")

def get_latest_execution():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, status, mode, startedAt, stoppedAt, workflowId
        FROM execution_entity
        WHERE workflowId = 'RentkomTelegramUnified01'
        ORDER BY id DESC LIMIT 1
    """)
    row = cur.fetchone()
    if not row:
        conn.close()
        return None
    exec_id, status, mode, started, stopped, wf_id = row
    cur.execute("SELECT data FROM execution_data WHERE executionId = ?", (exec_id,))
    data_row = cur.fetchone()
    conn.close()

    nodes_executed = []
    raw = []
    if data_row:
        raw = json.loads(data_row[0])
        for item in raw:
            if isinstance(item, dict) and any(n in item for n in [
                "Test Webhook Ingress", "Query RAG Core API", "Greeting & Persistent Keyboard",
                "Buying Process Handler", "Landlord Services Handler", "Intelligent Message Router"
            ]):
                nodes_executed = list(item.keys())
                break
    return {
        "id": exec_id,
        "status": status,
        "startedAt": started,
        "stoppedAt": stopped,
        "nodes": nodes_executed,
        "raw": raw
    }

def wait_for_execution(prev_id, timeout_sec=15):
    start = time.time()
    while time.time() - start < timeout_sec:
        time.sleep(0.5)
        rec = get_latest_execution()
        if rec and rec["id"] > prev_id and rec["status"] in ["success", "error"]:
            return rec
    return get_latest_execution()

@pytest.mark.integration
def test_router_scenario_1_start_menu():
    """Verify /start command is routed to Greeting & Persistent Keyboard."""
    prev = get_latest_execution()
    prev_id = prev["id"] if prev else 0
    status, _ = send_to_router("/start")
    assert status == 200
    rec = wait_for_execution(prev_id, timeout_sec=15)
    assert rec is not None
    assert rec["status"] == "success"
    assert "Greeting & Persistent Keyboard" in rec["nodes"]
    assert "Intelligent Message Router" in rec["nodes"]

@pytest.mark.integration
def test_router_scenario_2_buying_process():
    """Verify buying button routes to Buying Process Handler."""
    prev = get_latest_execution()
    prev_id = prev["id"] if prev else 0
    status, _ = send_to_router("🏢 Процес купівлі/продажу")
    assert status == 200
    rec = wait_for_execution(prev_id, timeout_sec=15)
    assert rec is not None
    assert rec["status"] == "success"
    assert "Buying Process Handler" in rec["nodes"]

@pytest.mark.integration
def test_router_scenario_3_landlord_services():
    """Verify renting button routes to Landlord Services Handler."""
    prev = get_latest_execution()
    prev_id = prev["id"] if prev else 0
    status, _ = send_to_router("🔑 Послуги для орендодавців")
    assert status == 200
    rec = wait_for_execution(prev_id, timeout_sec=15)
    assert rec is not None
    assert rec["status"] == "success"
    assert "Landlord Services Handler" in rec["nodes"]

@pytest.mark.integration
def test_router_scenario_4_and_5_rag_query():
    """Verify free question triggers RAG pipeline and returns grounded answer."""
    prev = get_latest_execution()
    prev_id = prev["id"] if prev else 0

    status, _ = send_to_router("Як перевірити документи на квартиру?")
    assert status == 200
    rec = wait_for_execution(prev_id, timeout_sec=120)

    assert rec is not None
    assert rec["status"] == "success"
    assert "Query RAG Core API" in rec["nodes"]
    assert "Send AI / Fallback Response" in rec["nodes"]
