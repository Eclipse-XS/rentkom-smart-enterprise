#!/usr/bin/env python3
"""Live synthetic LR2 -> LR6 -> RAG -> LR2 acceptance through deployed n8n."""

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")
WEBHOOK_URL = "http://127.0.0.1:5678/webhook/rentkom-unified-router-test"
API_BASE = "http://127.0.0.1:8000/api/v1"
CHAT_ID = int(os.environ["TELEGRAM_ADMIN_CHAT_ID"])
USER_ID = int(os.getenv("TELEGRAM_ADMIN_USER_ID", str(CHAT_ID)))


def _post(url, payload, headers, timeout=15):
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json; charset=utf-8", **headers},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
        return response.status, json.loads(raw or "{}")


def _database():
    return psycopg2.connect(
        dbname=os.getenv("POSTGRES_DB", "lab4_real_estate"), user="postgres",
        password=os.environ["POSTGRES_PASSWORD"], host="127.0.0.1",
        port=int(os.getenv("POSTGRES_HOST_PORT", "5433")),
    )


def _wait_for_terminal(update_id, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with _database() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT status, response_payload FROM telegram_inbox WHERE bot_id=%s AND update_id=%s",
                ("rentkom_bot", update_id),
            )
            row = cursor.fetchone()
        if row and row[0] in ("COMPLETED", "FAILED"):
            return row
        time.sleep(0.5)
    raise RuntimeError(f"Telegram update {update_id} did not reach a terminal inbox state")


def _send(update, timeout=30):
    status, _ = _post(WEBHOOK_URL, update, {"X-Test-Token": os.environ["TEST_WEBHOOK_SECRET"]})
    if status != 200:
        raise RuntimeError(f"n8n ingress returned HTTP {status}")
    inbox_status, payload = _wait_for_terminal(update["update_id"], timeout)
    if inbox_status != "COMPLETED":
        raise RuntimeError(f"Telegram update {update['update_id']} ended as {inbox_status}")
    return payload or {}


def _message(update_id, text, chat_type="private"):
    return {"update_id": update_id, "message": {
        "message_id": update_id % 1000000, "date": int(time.time()), "text": text,
        "chat": {"id": CHAT_ID, "type": chat_type},
        "from": {"id": USER_ID, "is_bot": False, "first_name": "RoutingSmoke"},
    }}


def _callback(update_id, data):
    return {"update_id": update_id, "callback_query": {
        "id": f"smoke-{update_id}",
        "from": {"id": USER_ID, "is_bot": False, "first_name": "RoutingSmoke"},
        "message": {"message_id": update_id % 1000000, "date": int(time.time()),
                    "text": "🔄 Режим роботи", "chat": {"id": CHAT_ID, "type": "private"}},
        "data": data,
    }}


def _current_mode():
    with urllib.request.urlopen(f"{API_BASE}/telegram/mode", timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))["mode"]


def _set_mode(mode):
    _post(f"{API_BASE}/telegram/mode", {"mode": mode},
          {"X-Internal-Token": os.environ["INTERNAL_API_KEY"]})


def main():
    base = int(time.time() * 1000)
    original_mode = _current_mode()
    checks = []
    try:
        _set_mode("LR2")
        payload = _send(_message(base, "/mode"))
        checks.append(("mode_menu", "Режим роботи" in str(payload)))

        payload = _send(_callback(base + 1, "mode:LR6"))
        checks.extend((("select_lr6_confirmation", "LR6" in str(payload)),
                       ("select_lr6_persisted", _current_mode() == "LR6")))

        payload = _send(_message(base + 2, "Які документи потрібно перевірити перед купівлею житла?"), 120)
        answer = str(payload.get("response_text", ""))
        checks.append(("lr6_visible_answer", len(answer) > 20 and "тимчасово недоступна" not in answer.lower()))

        payload = _send(_message(base + 3, "Цей запит має бути безпечно відхилений", chat_type="channel"))
        checks.append(("lr6_failure_visible", "тимчасово недоступна" in str(payload).lower()))

        payload = _send(_callback(base + 4, "mode:LR2"))
        checks.extend((("select_lr2_confirmation", "LR2" in str(payload)),
                       ("select_lr2_persisted", _current_mode() == "LR2")))

        payload = _send(_message(base + 5, "Невідома команда"))
        checks.append(("lr2_fallback", bool(payload.get("response_text"))))
    finally:
        _set_mode(original_mode)

    failed = [name for name, passed in checks if not passed]
    print(json.dumps({"passed": [name for name, passed in checks if passed], "failed": failed}, ensure_ascii=False))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
