#!/usr/bin/env python3
"""
Rentkom Smart Enterprise — Telegram Operating Mode Controller.
Switches the Telegram bot between:
  - LR2: Independent Rule-Based Business Bot (deterministic menu, reply keyboard, isolated fallback)
  - LR6: Independent RAG AI Assistant (enterprise knowledge base, semantic search, Ollama LLM)
Persists setting into PostgreSQL public.system_config across restarts.
"""

import os
import sys
import json
import urllib.request
import urllib.error
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except Exception:
    pass


def get_internal_key():
    return os.getenv("INTERNAL_API_KEY", "").strip()


def get_current_mode_api():
    url = "http://127.0.0.1:8000/api/v1/telegram/mode"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "RentkomModeCLI/1.0"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("mode", "LR2")
    except Exception:
        return None


def set_mode_api(target_mode):
    url = "http://127.0.0.1:8000/api/v1/telegram/mode"
    key = get_internal_key()
    if not key:
        raise RuntimeError("INTERNAL_API_KEY is not configured")
    payload = json.dumps({"mode": target_mode}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-Internal-Token": key,
            "User-Agent": "RentkomModeCLI/1.0"
        },
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    target = sys.argv[1].strip().upper() if len(sys.argv) > 1 else "STATUS"

    if target in ("STATUS", "--STATUS", "-S"):
        mode = get_current_mode_api()
        if not mode:
            try:
                import psycopg2
                port = int(os.getenv("POSTGRES_HOST_PORT", "5433"))
                conn = psycopg2.connect(
                    dbname=os.getenv("POSTGRES_DB", "lab4_real_estate"),
                    user=os.getenv("POSTGRES_USER", "postgres"),
                    password=os.getenv("POSTGRES_PASSWORD", "postgres"),
                    host=os.getenv("POSTGRES_BIND_IP", "127.0.0.1"),
                    port=port
                )
                cur = conn.cursor()
                cur.execute("SELECT value FROM public.system_config WHERE key = 'telegram_bot_mode';")
                row = cur.fetchone()
                mode = row[0] if row else "LR2"
                conn.close()
            except Exception as e:
                mode = f"Не вдалося з'єднатися з API чи БД ({e})"

        desc = {
            "LR2": "Сценарний діалоговий бот ЛР2 (правила, клавіатура, сценарний fallback)",
            "LR6": "Локальний RAG ШІ-асистент ЛР6 (база знань, Qdrant, Ollama qwen3:4b)"
        }.get(mode, "Невідомий режим")

        print("============================================================")
        print("  RENTKOM SMART ENTERPRISE: ТЕЛЕГРАМ-БОТ — АКТИВНИЙ РЕЖИМ   ")
        print("============================================================")
        print(f"  Поточний режим:  {mode}")
        print(f"  Опис контуру:    {desc}")
        print("============================================================")
        return

    if target not in ("LR2", "LR6"):
        print(f"[!] Неприпустимий режим '{target}'. Оберіть 'LR2' або 'LR6'.")
        sys.exit(1)

    print(f"[*] Перемикання режиму Telegram-бота Rentkom на {target}...")
    try:
        res = set_mode_api(target)
        status = res.get("status")
        if status == "PARTIAL_SUCCESS":
            print(f"[PARTIAL] Режим {res.get('mode')} збережено, але Telegram-сповіщення не доставлено: {res.get('notification_error')}")
            sys.exit(2)
        if status == "UNCHANGED":
            print(f"[UNCHANGED] Режим {res.get('mode')} вже активний; повторне сповіщення не надсилалось.")
        else:
            print(
                f"[SUCCESS] Режим активовано, Telegram-сповіщення доставлено: "
                f"{res.get('mode')} (message_id={res.get('notification_message_id')})"
            )
    except Exception as api_err:
        print(f"[ERROR] Перемикання через авторизований API не виконано: {api_err}")
        sys.exit(1)

    desc = "Сценарний бізнес-бот ЛР2" if target == "LR2" else "RAG AI-асистент ЛР6"
    print(f"[OK] Бот тепер функціонує в ізольованому режимі: {desc}")


if __name__ == "__main__":
    main()
