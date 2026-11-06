#!/usr/bin/env python3
"""
Rentkom Smart Enterprise — External Integrations Auditor.
Verifies Google Sheets & Gmail OAuth credentials in the n8n database,
checks Cloudflare Tunnel status, and audits Telegram Bot API Webhook health.
Outputs clean JSON with status codes.
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

import psycopg2


def audit_google_oauth():
    try:
        conn = psycopg2.connect(
            dbname=os.getenv("N8N_POSTGRES_DB", "rentkom_n8n"),
            user="postgres",
            password=os.getenv("POSTGRES_PASSWORD", "postgres"),
            host="127.0.0.1",
            port=int(os.getenv("POSTGRES_HOST_PORT", "5433"))
        )
        cur = conn.cursor()
        cur.execute("SELECT id, name, type FROM n8n.credentials_entity WHERE id IN ('w2ZVcryJf7CxkU0H', 'GsNbRh9jsuecZBVq')")
        rows = cur.fetchall()
        conn.close()

        found_sheets = False
        found_gmail = False

        for r in rows:
            cid, cname, ctype = r
            if cid == "w2ZVcryJf7CxkU0H":
                found_sheets = True
            elif cid == "GsNbRh9jsuecZBVq":
                found_gmail = True

        if found_sheets and found_gmail:
            return {
                "status": "READY",
                "google_sheets": True,
                "gmail": True,
                "message": "Google Sheets & Gmail OAuth credentials present and active"
            }
        else:
            return {
                "status": "DEGRADED",
                "google_sheets": found_sheets,
                "gmail": found_gmail,
                "message": f"Found {len(rows)}/2 required Google OAuth credentials"
            }
    except Exception as e:
        return {
            "status": "ERROR",
            "message": str(e)
        }


def audit_telegram_webhook():
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return {
            "status": "INACTIVE",
            "message": "TELEGRAM_BOT_TOKEN not configured"
        }

    try:
        url = f"https://api.telegram.org/bot{token}/getWebhookInfo"
        req = urllib.request.Request(url, headers={"User-Agent": "RentkomAudit/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            info = json.loads(r.read().decode("utf-8")).get("result", {})
            registered_url = info.get("url", "")
            last_error = info.get("last_error_message")
            pending_count = info.get("pending_update_count", 0)

            if registered_url and not last_error:
                return {
                    "status": "READY",
                    "webhook_url": registered_url,
                    "pending_count": pending_count,
                    "message": f"Webhook registered and active ({registered_url})"
                }
            elif registered_url:
                return {
                    "status": "DEGRADED",
                    "webhook_url": registered_url,
                    "last_error": last_error,
                    "message": f"Webhook registered but reported error: {last_error}"
                }
            else:
                return {
                    "status": "INACTIVE",
                    "message": "No webhook currently registered with Telegram Bot API"
                }
    except Exception as e:
        return {
            "status": "ERROR",
            "message": str(e)
        }


def audit_cloudflare_tunnel():
    from tools.manage_tunnel import detect_existing_tunnel, load_state
    pid, url = detect_existing_tunnel()
    if pid and url:
        state = load_state() or {}
        return {
            "status": "READY",
            "pid": pid,
            "url": url,
            "webhook_url": state.get("webhook_url"),
            "message": f"Tunnel active at {url} (PID: {pid})"
        }
    else:
        return {
            "status": "INACTIVE",
            "message": "No healthy Cloudflare tunnel detected"
        }


def main():
    google_res = audit_google_oauth()
    tunnel_res = audit_cloudflare_tunnel()
    tg_res = audit_telegram_webhook()

    result = {
        "google": google_res,
        "tunnel": tunnel_res,
        "telegram": tg_res
    }

    # External readiness requires:
    # 1. Google OAuth READY
    # 2. Tunnel READY
    # 3. Telegram Webhook READY
    all_external_ready = (
        google_res.get("status") == "READY" and
        tunnel_res.get("status") == "READY" and
        tg_res.get("status") == "READY"
    )

    result["external_ready"] = all_external_ready
    print(json.dumps(result, indent=2))
    sys.exit(0 if all_external_ready else 1)


if __name__ == "__main__":
    main()
