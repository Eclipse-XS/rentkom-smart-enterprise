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

# Load settings from core.config if available
internal_key = os.getenv("INTERNAL_API_KEY", "")
try:
    from core.config import settings
    if not internal_key:
        internal_key = settings.INTERNAL_API_KEY
except Exception:
    pass

if not internal_key:
    internal_key = os.getenv("INTERNAL_API_KEY", "")

def bootstrap_credentials():
    print(f"[*] Bootstrapping n8n credentials via official n8n CLI...")
    test_secret = os.getenv("TEST_WEBHOOK_SECRET", "")
    telegram_secret = os.getenv("TELEGRAM_SECRET_TOKEN", "")
    if not internal_key or not test_secret or not telegram_secret:
        print("[!] Error: INTERNAL_API_KEY, TEST_WEBHOOK_SECRET, and TELEGRAM_SECRET_TOKEN must all be set.")
        sys.exit(1)

    # Credential definition conforming to n8n httpHeaderAuth schema
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
    ]

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
            print("[+] Successfully imported the three Rentkom header credentials into n8n.")
            print(result.stdout.strip())
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
