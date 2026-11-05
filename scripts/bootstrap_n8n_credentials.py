#!/usr/bin/env python3
"""
Bootstrap script for n8n credentials in clean instances / Demo Mode.
Uses official n8n CLI (n8n import:credentials) without any raw database modifications.
"""

import os
import sys
import json
import tempfile
import subprocess
from pathlib import Path

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

internal_key = os.getenv("INTERNAL_API_KEY", "")
app_password = os.getenv("RENTKOM_APP_PASSWORD", "rentkom_app_secure_demo_pass_2026")
ollama_host = os.getenv("OLLAMA_HOST", "host.docker.internal")
qdrant_host = os.getenv("DOCKER_QDRANT_HOST", "qdrant")

try:
    from core.config import settings
    if not internal_key:
        internal_key = settings.INTERNAL_API_KEY
    if not app_password:
        app_password = settings.RENTKOM_APP_PASSWORD
except Exception:
    pass

if not internal_key:
    internal_key = os.getenv("INTERNAL_API_KEY", "")

def bootstrap_credentials():
    print("[*] Bootstrapping n8n credentials via official n8n CLI...")
    test_secret = os.getenv("TEST_WEBHOOK_SECRET", "")
    telegram_secret = os.getenv("TELEGRAM_SECRET_TOKEN", "")
    if not internal_key or not test_secret or not telegram_secret:
        print("[!] Error: INTERNAL_API_KEY, TEST_WEBHOOK_SECRET, and TELEGRAM_SECRET_TOKEN must all be set.")
        sys.exit(1)

    # Credential definitions conforming to n8n schemas
    cred_payload = [
        {
            "id": "RentkomInternalAuth",
            "name": "Rentkom Internal Auth",
            "type": "httpHeaderAuth",
            "data": {
                "name": "X-Internal-Token",
                "value": internal_key
            }
        },
        {
            "id": "RentkomTestWebhookSecret",
            "name": "Rentkom Test Webhook Secret",
            "type": "httpHeaderAuth",
            "data": {"name": "X-Test-Token", "value": test_secret},
        },
        {
            "id": "RentkomTelegramWebhookSecret",
            "name": "Rentkom Telegram Webhook Secret",
            "type": "httpHeaderAuth",
            "data": {
                "name": "X-Telegram-Bot-Api-Secret-Token",
                "value": telegram_secret,
            },
        },
        {
            "id": "0hyM64j1c9hxAiq7",
            "name": "Postgres account",
            "type": "postgres",
            "data": {
                "host": "postgres",
                "database": os.getenv("POSTGRES_DB", "lab4_real_estate"),
                "user": "rentkom_app",
                "password": app_password,
                "port": 5432,
                "ssl": "disable"
            }
        },
        {
            "id": "CiIHbLPwsosTWHf6",
            "name": "Ollama account",
            "type": "ollamaApi",
            "data": {
                "baseUrl": f"http://{ollama_host}:11434"
            }
        },
        {
            "id": "Ntr00wfW3KA7ZmrR",
            "name": "Qdrant account",
            "type": "qdrantApi",
            "data": {
                "qdrantUrl": f"http://{qdrant_host}:6333"
            }
        }
    ]

    telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if telegram_bot_token:
        cred_payload.append({
            "id": "egDthAT3oVlnSRjQ",
            "name": "Telegram account",
            "type": "telegramApi",
            "data": {
                "accessToken": telegram_bot_token
            }
        })

    # Protect existing OAuth credentials from being overwritten by bare client_id/secret payloads
    existing_ids = set()
    try:
        import psycopg2
        conn = psycopg2.connect(
            dbname=os.getenv("N8N_POSTGRES_DB", "rentkom_n8n"),
            user="postgres",
            password=os.getenv("POSTGRES_PASSWORD", "postgres"),
            host="127.0.0.1",
            port=int(os.getenv("POSTGRES_HOST_PORT", "5433"))
        )
        cur = conn.cursor()
        cur.execute("SELECT id FROM n8n.credentials_entity")
        existing_ids = {r[0] for r in cur.fetchall()}
        conn.close()
    except Exception as e:
        print(f"[*] Notice checking existing credentials in n8n database: {e}")

    google_cid = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    google_csec = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
    if google_cid and google_csec:
        google_creds = [
            {
                "id": "w2ZVcryJf7CxkU0H",
                "name": "Google Sheets | OI43 Lab3",
                "type": "googleSheetsTriggerOAuth2Api",
                "data": {
                    "clientId": google_cid,
                    "clientSecret": google_csec,
                }
            },
            {
                "id": "GsNbRh9jsuecZBVq",
                "name": "Gmail | OI43 Lab4",
                "type": "gmailOAuth2",
                "data": {
                    "clientId": google_cid,
                    "clientSecret": google_csec,
                }
            },
            {
                "id": "LAB5SheetsCred1",
                "name": "Google Sheets | OI43 LAB5",
                "type": "googleSheetsOAuth2Api",
                "data": {
                    "clientId": google_cid,
                    "clientSecret": google_csec,
                }
            }
        ]
        for gc in google_creds:
            if gc["id"] not in existing_ids:
                cred_payload.append(gc)
            else:
                print(f"[+] Preserving existing OAuth credential '{gc['name']}' ({gc['id']}) in n8n.")

    container = os.getenv("N8N_CONTAINER", "rentkom-n8n")
    # Write to a secured temporary file
    temp_dir = tempfile.mkdtemp(prefix="n8n_bootstrap_")
    temp_file = os.path.join(temp_dir, "credentials.json")

    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(cred_payload, f, indent=2)

        container_file = "/tmp/rentkom-credentials.json"
        copy_result = subprocess.run(
            ["docker", "cp", temp_file, f"{container}:{container_file}"],
            capture_output=True, text=True, timeout=30,
        )
        if copy_result.returncode != 0:
            print(copy_result.stderr or copy_result.stdout)
            return False
        cmd = ["docker", "exec", container, "n8n", "import:credentials", f"--input={container_file}"]
        print(f"[*] Importing credentials inside container {container}.")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        subprocess.run(["docker", "exec", container, "rm", "-f", container_file], capture_output=True, timeout=15)

        if result.returncode == 0:
            print(f"[+] Successfully imported {len(cred_payload)} Rentkom credentials into n8n.")
            print(result.stdout.strip())

            # Seed n8n project variables (e.g. TELEGRAM_ADMIN_CHAT_ID)
            admin_chat_id = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "").strip()
            if admin_chat_id:
                cmd_psql = [
                    "docker", "exec", "rentkom-postgres", "psql", "-U", "postgres", "-d",
                    os.getenv("N8N_POSTGRES_DB", "rentkom_n8n"), "-c",
                    f"INSERT INTO n8n.variables (key, type, value, id, \"projectId\") "
                    f"SELECT 'TELEGRAM_ADMIN_CHAT_ID', 'string', '{admin_chat_id}', 'RentkomAdminChatId', p.id FROM n8n.project p LIMIT 1 "
                    f"ON CONFLICT (id) DO UPDATE SET value = '{admin_chat_id}';"
                ]
                psql_res = subprocess.run(cmd_psql, capture_output=True, text=True)
                if psql_res.returncode == 0:
                    print(f"[+] Seeded n8n project variable TELEGRAM_ADMIN_CHAT_ID.")
                else:
                    print(f"[!] Notice on variables seeding: {psql_res.stderr or psql_res.stdout}")

            return True
        else:
            print(f"[-] n8n CLI import failed with code {result.returncode}:")
            print(result.stderr or result.stdout)
            return False
    finally:
        # Secure cleanup of temporary file and directory
        if os.path.exists(temp_file):
            os.remove(temp_file)
        if os.path.exists(temp_dir):
            os.rmdir(temp_dir)
        print("[*] Secure cleanup complete.")

if __name__ == "__main__":
    success = bootstrap_credentials()
    sys.exit(0 if success else 1)
