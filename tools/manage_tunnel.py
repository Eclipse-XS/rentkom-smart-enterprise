#!/usr/bin/env python3
"""
Rentkom Smart Enterprise — Cloudflare Tunnel & Telegram Webhook Lifecycle Manager.
Provides idempotent management of the cloudflared development tunnel,
discovers public HTTPS endpoint, prevents duplicate tunnel processes,
and automatically registers/verifies the Telegram Bot API webhook.
"""

import os
import sys
import json
import time
import re
import base64
import urllib.request
import urllib.error
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except Exception:
    pass

RUNTIME_DIR = PROJECT_ROOT / "runtime"
STATE_FILE = RUNTIME_DIR / "tunnel_state.json"
LOG_FILE = RUNTIME_DIR / "cloudflared.log"

CLOUDFLARED_SEARCH_PATHS = [
    r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
    r"C:\Program Files\cloudflared\cloudflared.exe",
    "cloudflared.exe",
    "cloudflared",
]


def find_cloudflared_binary():
    for p in CLOUDFLARED_SEARCH_PATHS:
        if os.path.isfile(p):
            return p
        import shutil
        found = shutil.which(p)
        if found:
            return found
    return None


def get_running_cloudflared_pids():
    pids = []
    if sys.platform == "win32":
        try:
            res = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq cloudflared.exe", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, check=False
            )
            for line in res.stdout.splitlines():
                if "cloudflared" in line.lower():
                    parts = line.split(",")
                    if len(parts) >= 2:
                        pid_str = parts[1].strip('"').strip()
                        if pid_str.isdigit():
                            pids.append(int(pid_str))
        except Exception:
            pass
    return pids


def is_pid_running(pid):
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, check=False
            )
            return f'"{pid}"' in result.stdout
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def kill_pids(pids):
    for pid in pids:
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, check=False)
            else:
                os.kill(pid, 9)
        except Exception:
            pass


def check_tunnel_health(base_url, timeout=6):
    try:
        url = f"{base_url.rstrip('/')}/healthz"
        req = urllib.request.Request(url, headers={"User-Agent": "RentkomTunnelCheck/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                return True
    except Exception:
        pass

    return False


def load_state():
    if STATE_FILE.is_file():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def save_state(state):
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def detect_existing_tunnel():
    state = load_state()
    running_pids = get_running_cloudflared_pids()

    if state and state.get("pid") in running_pids:
        url = state.get("url")
        if url and check_tunnel_health(url):
            return state["pid"], url

    # If state file was missing but PID is running, try to read url from log
    if running_pids and LOG_FILE.is_file():
        try:
            content = LOG_FILE.read_text(encoding="utf-8", errors="replace")
            matches = re.findall(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", content)
            if matches:
                url = matches[-1]
                if check_tunnel_health(url):
                    save_state({"pid": running_pids[0], "url": url, "updated_at": time.time()})
                    return running_pids[0], url
        except Exception:
            pass

    return None, None


def request_quick_tunnel_token(timeout=25):
    try:
        req = urllib.request.Request(
            "https://api.trycloudflare.com/tunnel",
            data=b"",
            headers={"User-Agent": "cloudflared/2026.10.3"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("success"):
                res = data["result"]
                token_dict = {
                    "a": res["account_tag"],
                    "t": res["id"],
                    "s": res["secret"]
                }
                token_b64 = base64.b64encode(json.dumps(token_dict).encode("utf-8")).decode("utf-8")
                url = f"https://{res['hostname']}"
                return token_b64, url
    except Exception as e:
        print(f"[!] Warning requesting quick tunnel token directly: {e}")
    return None, None


def start_tunnel(local_target="http://127.0.0.1:5678", timeout=30, max_retries=3):
    bin_path = find_cloudflared_binary()
    if not bin_path:
        raise RuntimeError("cloudflared binary not found in standard paths or PATH.")

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    url_pattern = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")

    for attempt in range(1, max_retries + 1):
        stale_pids = get_running_cloudflared_pids()
        if stale_pids:
            print(f"[*] Terminating {len(stale_pids)} stale cloudflared process(es): {stale_pids}")
            kill_pids(stale_pids)
            time.sleep(1)

        token_b64, pre_url = request_quick_tunnel_token(timeout=20)
        log_fp = open(LOG_FILE, "w", encoding="utf-8")
        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

        if token_b64 and pre_url:
            print(f"[*] Spawning token-authenticated quick tunnel pointing to {local_target} (attempt {attempt}/{max_retries})...")
            cmd = [bin_path, "tunnel", "run", "--token", token_b64, "--url", local_target]
            discovered_url = pre_url
        else:
            print(f"[*] Spawning standard quick tunnel pointing to {local_target} (attempt {attempt}/{max_retries})...")
            cmd = [bin_path, "tunnel", "--url", local_target]
            discovered_url = None

        proc = subprocess.Popen(
            cmd,
            stdout=log_fp,
            stderr=subprocess.STDOUT,
            creationflags=creation_flags
        )

        start_time = time.time()

        while time.time() - start_time < timeout:
            if proc.poll() is not None:
                print(f"[!] cloudflared attempt {attempt} exited prematurely with code {proc.returncode}.")
                break

            if LOG_FILE.is_file():
                try:
                    log_text = LOG_FILE.read_text(encoding="utf-8", errors="replace")
                    if not discovered_url:
                        matches = url_pattern.findall(log_text)
                        if matches:
                            discovered_url = matches[-1]
                    if "Registered tunnel connection" in log_text or "connIndex=0" in log_text:
                        break
                except Exception:
                    pass
            time.sleep(1)

        if discovered_url and is_pid_running(proc.pid):
            time.sleep(2)
            print(f"[*] Verifying tunnel accessibility: {discovered_url}/healthz...")
            healthy = False
            for _ in range(12):
                if check_tunnel_health(discovered_url, timeout=5):
                    healthy = True
                    break
                time.sleep(1.5)

            if healthy:
                save_state({
                    "pid": proc.pid,
                    "url": discovered_url,
                    "local_target": local_target,
                    "started_at": time.time(),
                    "updated_at": time.time()
                })
                print(f"[SUCCESS] Cloudflare Tunnel established: {discovered_url} (PID: {proc.pid})")
                return proc.pid, discovered_url

        print(f"[!] Tunnel attempt {attempt} failed. Backing off before retry...")
        kill_pids([proc.pid])
        time.sleep(3)

    raise RuntimeError(f"Failed to establish Cloudflare Tunnel after {max_retries} attempts.")


def sync_telegram_webhook(tunnel_url):
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    secret_token = os.getenv("TELEGRAM_SECRET_TOKEN", "").strip()

    if not bot_token:
        print("[!] Warning: TELEGRAM_BOT_TOKEN not configured in .env. Skipping webhook sync.")
        return False, "TELEGRAM_BOT_TOKEN missing"

    webhook_path = "/webhook/rentkom-telegram-ingress-webhook"
    target_webhook_url = f"{tunnel_url.rstrip('/')}{webhook_path}"

    print(f"[*] Synchronizing Telegram Webhook -> {target_webhook_url}")

    # Inspect current info
    info_url = f"https://api.telegram.org/bot{bot_token}/getWebhookInfo"
    curr_info = {}
    try:
        req_info = urllib.request.Request(info_url, headers={"User-Agent": "RentkomWebhookSync/1.0"})
        with urllib.request.urlopen(req_info, timeout=10) as resp:
            curr_info = json.loads(resp.read().decode("utf-8")).get("result", {})
    except Exception as e:
        print(f"[!] Warning reading current webhook info: {e}")

    current_registered_url = curr_info.get("url", "")
    has_error = bool(curr_info.get("last_error_date"))

    if current_registered_url == target_webhook_url and not has_error:
        print(f"[OK] Telegram Webhook is already correctly set to {target_webhook_url}")
        return True, target_webhook_url

    # Register new webhook
    set_url = f"https://api.telegram.org/bot{bot_token}/setWebhook"
    allowed_updates = curr_info.get("allowed_updates") or ["message", "callback_query"]
    payload = {
        "url": target_webhook_url,
        "allowed_updates": allowed_updates,
    }
    if secret_token:
        payload["secret_token"] = secret_token

    req_set = urllib.request.Request(
        set_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "RentkomWebhookSync/1.0"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(req_set, timeout=15) as resp:
            res_data = json.loads(resp.read().decode("utf-8"))
            if res_data.get("ok"):
                verified = get_telegram_webhook_info()
                if verified.get("url") != target_webhook_url:
                    print("[!] Telegram webhook read-back did not match requested URL.")
                    return False, "Webhook read-back mismatch"
                print(f"[SUCCESS] Telegram Webhook registered: {target_webhook_url}")
                # Update state
                state = load_state() or {}
                state["webhook_url"] = target_webhook_url
                state["webhook_synced_at"] = time.time()
                save_state(state)
                return True, target_webhook_url
            else:
                print(f"[!] Telegram API error: {res_data}")
                return False, str(res_data)
    except Exception as e:
        print(f"[!] Error registering Telegram Webhook: {e}")
        return False, str(e)


def get_telegram_webhook_info():
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not bot_token:
        return {}
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{bot_token}/getWebhookInfo",
        headers={"User-Agent": "RentkomWebhookSync/1.0"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload.get("result", {}) if payload.get("ok") else {}


def verify_webhook(tunnel_url):
    expected = f"{tunnel_url.rstrip('/')}/webhook/rentkom-telegram-ingress-webhook"
    try:
        info = get_telegram_webhook_info()
    except Exception:
        return False
    return info.get("url") == expected


def stop_tunnel():
    state = load_state()
    supervisor_pid = state.get("supervisor_pid") if state else None
    if supervisor_pid and supervisor_pid != os.getpid():
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/PID", str(supervisor_pid)], capture_output=True, check=False)
            else:
                os.kill(supervisor_pid, 9)
        except Exception:
            pass

    pids = get_running_cloudflared_pids()
    if pids:
        print(f"[*] Stopping {len(pids)} cloudflared process(es)...")
        kill_pids(pids)
    if STATE_FILE.is_file():
        try:
            STATE_FILE.unlink()
        except Exception:
            pass
    print("[OK] Cloudflare Tunnel stopped.")


def run_supervisor(local_target="http://127.0.0.1:5678"):
    existing_state = load_state() or {}
    existing_supervisor = existing_state.get("supervisor_pid")
    if existing_supervisor and existing_supervisor != os.getpid() and is_pid_running(existing_supervisor):
        print(f"[OK] Tunnel supervisor is already running (PID: {existing_supervisor}).")
        return
    print("[*] Starting Cloudflare Tunnel supervisor daemon...")
    # Record supervisor pid
    pid, url = start_tunnel(local_target=local_target)
    state = load_state() or {}
    state["supervisor_pid"] = os.getpid()
    save_state(state)

    sync_telegram_webhook(url)

    try:
        while True:
            time.sleep(15)
            # Check if cloudflared pid is still running
            if not is_pid_running(pid):
                print("[!] cloudflared exited unexpectedly. Restarting...")
                pid, url = start_tunnel(local_target=local_target)
                state = load_state() or {}
                state["supervisor_pid"] = os.getpid()
                save_state(state)
                sync_telegram_webhook(url)
            elif not check_tunnel_health(url, timeout=10):
                print(f"[!] Tunnel at {url} health check failed. Re-verifying...")
                if not check_tunnel_health(url, timeout=10):
                    print("[!] Tunnel unhealthy. Restarting...")
                    pid, url = start_tunnel(local_target=local_target)
                    state = load_state() or {}
                    state["supervisor_pid"] = os.getpid()
                    save_state(state)
                    sync_telegram_webhook(url)
    except (KeyboardInterrupt, SystemExit):
        print("[*] Supervisor shutting down...")
    finally:
        stop_tunnel()


def ensure_tunnel():
    pid, url = detect_existing_tunnel()
    if pid and url:
        print(f"[OK] Reusing healthy Cloudflare Tunnel: {url} (PID: {pid})")
    else:
        pid, url = start_tunnel()

    tg_ok, tg_info = sync_telegram_webhook(url)
    return {
        "pid": pid,
        "tunnel_url": url,
        "telegram_webhook_synced": tg_ok,
        "telegram_info": tg_info
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Manage Cloudflare Tunnel for Rentkom")
    parser.add_argument("--action", choices=["ensure", "start", "stop", "status", "daemon"], default="ensure")
    args = parser.parse_args()

    if args.action == "ensure":
        res = ensure_tunnel()
        print(json.dumps(res, indent=2))
        sys.exit(0 if res["telegram_webhook_synced"] else 1)
    elif args.action == "daemon":
        run_supervisor()
    elif args.action == "start":
        pid, url = start_tunnel()
        sync_telegram_webhook(url)
    elif args.action == "stop":
        stop_tunnel()
    elif args.action == "status":
        pid, url = detect_existing_tunnel()
        if pid and url and verify_webhook(url):
            state = load_state() or {}
            print(f"HEALTHY: Tunnel active at {url} (PID: {pid})")
            if state.get("webhook_url"):
                print(f"WEBHOOK: {state.get('webhook_url')}")
            sys.exit(0)
        else:
            print("INACTIVE: Tunnel or Telegram webhook is not healthy.")
            sys.exit(1)


if __name__ == "__main__":
    main()
