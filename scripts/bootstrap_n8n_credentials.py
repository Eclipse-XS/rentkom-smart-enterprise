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
internal_key = None
try:
    from core.config import settings
    internal_key = settings.INTERNAL_API_KEY
except Exception:
    pass

if not internal_key:
    internal_key = os.getenv("INTERNAL_API_KEY", "")

def bootstrap_credentials():
    print(f"[*] Bootstrapping n8n credentials via official n8n CLI...")
    if not internal_key:
        print("[!] Error: INTERNAL_API_KEY environment variable is not set. Please set it in .env or pass it via environment.")
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
        }
    ]

    # Write to a secured temporary file
    temp_dir = tempfile.mkdtemp(prefix="n8n_bootstrap_")
    temp_file = os.path.join(temp_dir, "credentials.json")

    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(cred_payload, f, indent=2)

        # Execute official n8n CLI command
        # On Windows shell=True allows npx resolution
        cmd = ["npx", "--no-install", "n8n", "import:credentials", f"--input={temp_file}"]
        print(f"[*] Running CLI: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, shell=True, timeout=60)

        if result.returncode == 0:
            print("[+] Successfully imported RentkomInternalAuth into n8n!")
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
