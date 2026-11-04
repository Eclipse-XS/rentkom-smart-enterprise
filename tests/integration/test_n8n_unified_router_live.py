import json
import os
import socket
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
import pytest
from sqlalchemy import create_engine, text
from core.config import settings

WEBHOOK_URL = "http://localhost:5678/webhook/rentkom-unified-router-test"
DB_PATH = os.path.expanduser("~/.n8n/database.sqlite")
N8N_POSTGRES_URL = os.getenv(
    "N8N_POSTGRES_URL",
    "postgresql+psycopg2://postgres:postgres@127.0.0.1:5432/lab4_real_estate",
)
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


def _has_n8n_execution_store() -> bool:
    try:
        engine = create_engine(N8N_POSTGRES_URL)
        with engine.connect() as conn:
            res = conn.execute(
                text("SELECT to_regclass('public.execution_entity')")
            ).scalar()
            if res is not None:
                return True
    except Exception:
        pass
    out = _docker_psql_query("SELECT to_regclass('public.execution_entity');")
    if out == "execution_entity":
        return True
    return os.path.exists(DB_PATH)


def _require_live_n8n():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        if sock.connect_ex(("127.0.0.1", 5678)) != 0 or not _has_n8n_execution_store():
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
        return resp.status, resp.read().decode("utf-8")


def get_latest_execution():
    row = None
    data_str = None
    try:
        engine = create_engine(N8N_POSTGRES_URL)
        with engine.connect() as conn:
            has_table = conn.execute(
                text("SELECT to_regclass('public.execution_entity')")
            ).scalar()
            if has_table is not None:
                pg_row = conn.execute(
                    text("""
                        SELECT id, status, mode, "startedAt", "stoppedAt", "workflowId"
                        FROM execution_entity
                        WHERE "workflowId" = 'RentkomTelegramUnified01'
                        ORDER BY id DESC LIMIT 1
                    """)
                ).fetchone()
                if pg_row:
                    row = tuple(pg_row)
                    pg_data = conn.execute(
                        text('SELECT data FROM execution_data WHERE "executionId" = :eid'),
                        {"eid": row[0]},
                    ).fetchone()
                    if pg_data:
                        data_str = pg_data[0]
                    else:
                        return {
                            "id": row[0],
                            "status": row[1],
                            "startedAt": str(row[3]),
                            "stoppedAt": str(row[4]),
                            "nodes": [],
                            "raw": [],
                        }
    except Exception:
        row = None

    if row is None:
        out = _docker_psql_query(
            "SELECT id, status, mode, \"startedAt\", \"stoppedAt\", \"workflowId\" "
            "FROM execution_entity WHERE \"workflowId\" = 'RentkomTelegramUnified01' "
            "ORDER BY id DESC LIMIT 1;"
        )
        if out:
            parts = out.split("\t")
            if len(parts) >= 6:
                exec_id = int(parts[0])
                row = (exec_id, parts[1], parts[2], parts[3], parts[4], parts[5])
                data_str = _docker_psql_query(
                    f"SELECT data FROM execution_data WHERE \"executionId\" = {exec_id};"
                )

    if row is None:
        if not os.path.exists(DB_PATH):
            return None
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
        if data_row:
            data_str = data_row[0]
    else:
        exec_id, status, mode, started, stopped, wf_id = row

    nodes_executed = []
    raw = []
    if data_str:
        raw = json.loads(data_str) if isinstance(data_str, str) else data_str
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
        "startedAt": str(started),
        "stoppedAt": str(stopped),
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
    _require_live_n8n()
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
    _require_live_n8n()
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
    _require_live_n8n()
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
    _require_live_n8n()
    prev = get_latest_execution()
    prev_id = prev["id"] if prev else 0

    status, _ = send_to_router("Як перевірити документи на квартиру?")
    assert status == 200
    rec = wait_for_execution(prev_id, timeout_sec=120)

    assert rec is not None
    assert rec["status"] == "success"
    assert "Query RAG Core API" in rec["nodes"]
    assert "Send AI / Fallback Response" in rec["nodes"]
