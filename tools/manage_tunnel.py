#!/usr/bin/env python3
"""Safely own one Rentkom quick tunnel and reconcile its Telegram webhook."""

import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
import uuid
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
LOCK_FILE = RUNTIME_DIR / "tunnel_supervisor.lock"
LOG_FILE = RUNTIME_DIR / "cloudflared.log"
WEBHOOK_PATH = "/webhook/rentkom-telegram-ingress-webhook"
REQUIRED_ALLOWED_UPDATES = {"message", "callback_query"}
DEFAULT_LOCAL_TARGET = "http://127.0.0.1:8765"
INGRESS_SCRIPT = PROJECT_ROOT / "tools" / "restricted_ingress.py"
URL_PATTERN = re.compile(
    r"https://(?:[a-zA-Z0-9-]+\.trycloudflare\.com|[a-zA-Z0-9-]+\.lhr\.life)"
)
CLOUDFLARED_SEARCH_PATHS = [
    r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
    r"C:\Program Files\cloudflared\cloudflared.exe",
    "cloudflared.exe", "cloudflared",
]


class SupervisorLock:
    """OS lock released automatically after a process crash or Windows restart."""
    def __init__(self, path=LOCK_FILE):
        self.path, self.handle = Path(path), None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = open(self.path, "a+b")
        self.handle.seek(0, os.SEEK_END)
        if self.handle.tell() == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (OSError, IOError):
            self.handle.close()
            self.handle = None
            return False

    def release(self):
        if not self.handle:
            return
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


def find_cloudflared_binary():
    import shutil
    for candidate in CLOUDFLARED_SEARCH_PATHS:
        if os.path.isfile(candidate):
            return candidate
        found = shutil.which(candidate)
        if found:
            return found
    return None


def find_ssh_binary():
    import shutil
    return shutil.which("ssh.exe") or shutil.which("ssh")


def tcp_endpoint_reachable(host, port, timeout=5):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def get_process_info(pid):
    """Return command identity so PID reuse cannot grant process ownership."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if os.name == "nt":
        script = (f'$p=Get-CimInstance Win32_Process -Filter "ProcessId={pid}";'
                  'if($p){$p|Select ProcessId,Name,CommandLine,CreationDate|ConvertTo-Json -Compress}')
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, check=False,
        )
        if result.returncode or not result.stdout.strip():
            return None
        try:
            data = json.loads(result.stdout)
            return {"pid": int(data["ProcessId"]), "name": data.get("Name") or "",
                    "command_line": data.get("CommandLine") or "", "created": data.get("CreationDate") or ""}
        except (ValueError, KeyError, TypeError):
            return None
    path = Path(f"/proc/{pid}")
    try:
        command = (path / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        return {"pid": pid, "name": (path / "comm").read_text().strip(),
                "command_line": command, "created": ""}
    except OSError:
        return None


def is_pid_running(pid):
    return get_process_info(pid) is not None


def is_owned_supervisor(state, process_info=None):
    if not state or not state.get("supervisor_pid"):
        return False
    info = process_info or get_process_info(state["supervisor_pid"])
    command = (info or {}).get("command_line", "").lower()
    created_matches = not state.get("supervisor_process_created") or (
        state["supervisor_process_created"] == (info or {}).get("created")
    )
    return bool(info and created_matches and "manage_tunnel.py" in command and "--action daemon" in command)


def is_owned_tunnel(state, process_info=None):
    if not state or not state.get("pid") or not state.get("owner_id"):
        return False
    info = process_info or get_process_info(state["pid"])
    if not info:
        return False
    command = info.get("command_line", "").lower()
    created_matches = not state.get("process_created") or state["process_created"] == info.get("created")
    provider = state.get("provider", "cloudflare")
    if provider == "cloudflare":
        target = str(state.get("local_target") or DEFAULT_LOCAL_TARGET).lower()
        signature_matches = (
            "cloudflared" in info.get("name", "").lower()
            and "tunnel" in command and "--url" in command and target in command
        )
    elif provider == "localhost_run":
        parsed = urllib.parse.urlparse(str(state.get("local_target") or DEFAULT_LOCAL_TARGET))
        forward = f"80:{parsed.hostname}:{parsed.port}"
        signature_matches = (
            info.get("name", "").lower() in {"ssh", "ssh.exe"}
            and "nokey@localhost.run" in command
            and forward in command
        )
    else:
        signature_matches = False
    return created_matches and signature_matches


def is_owned_cloudflared(state, process_info=None):
    """Compatibility alias retained for existing operational imports and tests."""
    return is_owned_tunnel(state, process_info)


def terminate_pid(pid, force=False):
    if os.name == "nt":
        args = ["taskkill", "/PID", str(pid)]
        if force:
            args.insert(1, "/F")
        subprocess.run(args, capture_output=True, check=False)
    else:
        os.kill(int(pid), signal.SIGKILL if force else signal.SIGTERM)


def check_tunnel_health(base_url, timeout=8):
    try:
        request = urllib.request.Request(
            f"{base_url.rstrip('/')}{WEBHOOK_PATH}", data=b"{}", method="POST",
            headers={"Content-Type": "application/json", "User-Agent": "RentkomTunnelCheck/1.0"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status == 200
    except urllib.error.HTTPError as exc:
        return exc.code == 403
    except Exception:
        return False


def start_restricted_ingress():
    if tcp_endpoint_reachable("127.0.0.1", 8765, timeout=1):
        return None
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(
        [sys.executable, str(INGRESS_SCRIPT)], stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, creationflags=flags,
    )
    deadline = time.time() + 10
    while time.time() < deadline:
        if process.poll() is not None:
            break
        if tcp_endpoint_reachable("127.0.0.1", 8765, timeout=1):
            return process.pid
        time.sleep(0.2)
    if process.poll() is None:
        terminate_pid(process.pid, force=True)
    raise RuntimeError("restricted ingress failed to start")


def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.is_file() else None
    except (OSError, ValueError):
        return None


def save_state(state):
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(temporary, STATE_FILE)


def detect_existing_tunnel():
    state = load_state()
    if state and state.get("local_target") == DEFAULT_LOCAL_TARGET and is_owned_tunnel(state):
        url = state.get("url")
        if url and check_tunnel_health(url):
            return int(state["pid"]), url
    return None, None


def _log_failure_reason(text):
    markers = ("failed to request quick Tunnel", "context deadline exceeded", "no such host", "Unauthorized")
    lines = [line.strip() for line in text.splitlines()
             if any(marker.lower() in line.lower() for marker in markers)]
    return lines[-1] if lines else "public URL was not issued or did not pass /healthz"


def start_cloudflare_tunnel(local_target=DEFAULT_LOCAL_TARGET, timeout=90, max_retries=3, owner_id=None):
    """Use the supported cloudflared CLI; never kill an unowned process."""
    binary = find_cloudflared_binary()
    if not binary:
        raise RuntimeError("cloudflared binary not found")
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    owner_id = owner_id or uuid.uuid4().hex
    last_reason = "unknown failure"
    for attempt in range(1, max_retries + 1):
        print(f"[*] Starting standard Quick Tunnel (attempt {attempt}/{max_retries})", flush=True)
        with open(LOG_FILE, "a", encoding="utf-8") as log_file:
            log_file.write(f"\n--- attempt {attempt} at {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} ---\n")
            log_file.flush()
            flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            process = subprocess.Popen([binary, "tunnel", "--url", local_target], stdout=log_file,
                                       stderr=subprocess.STDOUT, creationflags=flags)
            process_info = get_process_info(process.pid) or {}
            state = load_state() or {}
            state.update({"owner_id": owner_id, "provider": "cloudflare",
                          "pid": process.pid, "url": None,
                          "local_target": local_target, "started_at": time.time(),
                          "updated_at": time.time(),
                          "process_created": process_info.get("created", "")})
            save_state(state)  # allows exact child recovery if the supervisor crashes now
            started, discovered, text = time.time(), None, ""
            while time.time() - started < timeout:
                if process.poll() is not None:
                    break
                try:
                    text = LOG_FILE.read_text(encoding="utf-8", errors="replace")
                    matches = URL_PATTERN.findall(text)
                    if matches:
                        discovered = matches[-1]
                except OSError:
                    text = ""
                if discovered and check_tunnel_health(discovered):
                    state = load_state() or {}
                    process_info = get_process_info(process.pid) or {}
                    state.update({"owner_id": owner_id, "provider": "cloudflare",
                                  "pid": process.pid, "url": discovered,
                                  "local_target": local_target, "started_at": time.time(),
                                  "updated_at": time.time(),
                                  "process_created": process_info.get("created", "")})
                    save_state(state)
                    print(f"[SUCCESS] Tunnel established: {discovered} (PID: {process.pid})", flush=True)
                    return process.pid, discovered
                time.sleep(2)
            try:
                text = LOG_FILE.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            last_reason = _log_failure_reason(text)
            if process.poll() is None:
                terminate_pid(process.pid, force=True)
            print(f"[!] Attempt {attempt} failed: {last_reason}", flush=True)
        if attempt < max_retries:
            time.sleep(min(5 * attempt, 15))
    raise RuntimeError(f"Quick Tunnel unavailable after {max_retries} attempts: {last_reason}")


def start_localhost_run_tunnel(local_target=DEFAULT_LOCAL_TARGET, timeout=45, max_retries=2, owner_id=None):
    """Start an account-free localhost.run SSH reverse tunnel."""
    ssh = find_ssh_binary()
    if not ssh:
        raise RuntimeError("OpenSSH client not found; localhost.run fallback is unavailable")
    parsed = urllib.parse.urlparse(local_target)
    if parsed.hostname != "127.0.0.1" or not parsed.port:
        raise RuntimeError("localhost.run fallback only permits an explicit 127.0.0.1 target")
    remote_forward = f"80:{parsed.hostname}:{parsed.port}"
    owner_id = owner_id or uuid.uuid4().hex
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    last_reason = "public URL was not issued"
    for attempt in range(1, max_retries + 1):
        print(f"[*] Starting localhost.run HTTPS fallback (attempt {attempt}/{max_retries})", flush=True)
        command = [
            ssh, "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ExitOnForwardFailure=yes", "-o", "ConnectTimeout=10",
            "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3",
            "-R", remote_forward, "nokey@localhost.run",
        ]
        with open(LOG_FILE, "a", encoding="utf-8") as log_file:
            log_file.write(f"\n--- localhost.run attempt {attempt} ---\n")
            log_file.flush()
            flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            process = subprocess.Popen(command, stdout=log_file, stderr=subprocess.STDOUT,
                                       creationflags=flags)
            process_info = get_process_info(process.pid) or {}
            state = load_state() or {}
            state.update({"owner_id": owner_id, "provider": "localhost_run", "pid": process.pid,
                          "url": None, "local_target": local_target, "started_at": time.time(),
                          "updated_at": time.time(), "process_created": process_info.get("created", "")})
            save_state(state)
            started, discovered, text = time.time(), None, ""
            while time.time() - started < timeout:
                if process.poll() is not None:
                    break
                try:
                    text = LOG_FILE.read_text(encoding="utf-8", errors="replace")
                    matches = URL_PATTERN.findall(text)
                    if matches:
                        discovered = matches[-1]
                except OSError:
                    text = ""
                if discovered and check_tunnel_health(discovered):
                    state = load_state() or {}
                    state.update({"url": discovered, "updated_at": time.time()})
                    save_state(state)
                    print(f"[SUCCESS] localhost.run tunnel established: {discovered} (PID: {process.pid})",
                          flush=True)
                    return process.pid, discovered
                time.sleep(2)
            if process.poll() is None:
                terminate_pid(process.pid, force=True)
            last_reason = _log_failure_reason(text) if text else last_reason
        if attempt < max_retries:
            time.sleep(5 * attempt)
    raise RuntimeError(f"localhost.run tunnel unavailable after {max_retries} attempts: {last_reason}")


def start_tunnel(local_target=DEFAULT_LOCAL_TARGET, timeout=90, max_retries=3, owner_id=None):
    """Prefer Cloudflare and fall back only when its API is unreachable."""
    if tcp_endpoint_reachable("api.trycloudflare.com", 443):
        try:
            # One bounded CLI attempt is enough after the TCP probe; leave time for fallback.
            return start_cloudflare_tunnel(local_target, min(timeout, 45), 1, owner_id)
        except RuntimeError as exc:
            print(f"[!] Cloudflare tunnel failed; trying localhost.run: {exc}", flush=True)
    else:
        print("[!] Cloudflare API TCP/443 is unreachable; using localhost.run fallback", flush=True)
    return start_localhost_run_tunnel(local_target, min(timeout, 45), 2, owner_id)


def get_telegram_webhook_info():
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return {}
    request = urllib.request.Request(f"https://api.telegram.org/bot{token}/getWebhookInfo",
                                     headers={"User-Agent": "RentkomWebhookSync/1.0"})
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload.get("result", {}) if payload.get("ok") else {}


def webhook_status(tunnel_url):
    expected = f"{tunnel_url.rstrip('/')}{WEBHOOK_PATH}"
    try:
        info = get_telegram_webhook_info()
    except Exception as exc:
        return False, {}, f"getWebhookInfo failed: {exc}"
    if info.get("url") != expected:
        return False, info, "registered URL does not match the active tunnel"
    missing_updates = REQUIRED_ALLOWED_UPDATES - set(info.get("allowed_updates") or [])
    if missing_updates:
        return False, info, f"missing allowed_updates: {', '.join(sorted(missing_updates))}"
    if info.get("last_error_date"):
        return False, info, info.get("last_error_message") or "Telegram reports a delivery error"
    return True, info, "URL matches and Telegram reports no delivery error"


def sync_telegram_webhook(tunnel_url):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    secret = os.getenv("TELEGRAM_SECRET_TOKEN", "").strip()
    if not token:
        return False, "TELEGRAM_BOT_TOKEN missing"
    desired = f"{tunnel_url.rstrip('/')}{WEBHOOK_PATH}"
    try:
        current = get_telegram_webhook_info()
    except Exception as exc:
        return False, f"getWebhookInfo failed: {exc}"
    missing_updates = REQUIRED_ALLOWED_UPDATES - set(current.get("allowed_updates") or [])
    if current.get("url") != desired or current.get("last_error_date") or missing_updates:
        payload = {"url": desired,
                   "allowed_updates": sorted(REQUIRED_ALLOWED_UPDATES)}
        if secret:
            payload["secret_token"] = secret
        request = urllib.request.Request(f"https://api.telegram.org/bot{token}/setWebhook",
                                         data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json",
                                                  "User-Agent": "RentkomWebhookSync/1.0"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                result = json.loads(response.read().decode())
            if not result.get("ok"):
                return False, f"setWebhook rejected: {result.get('description', 'unknown error')}"
        except Exception as exc:
            return False, f"setWebhook failed: {exc}"
    ok, verified, reason = webhook_status(tunnel_url)
    if ok:
        state = load_state() or {}
        state.update({"webhook_url": desired, "webhook_synced_at": time.time(),
                      "pending_update_count": verified.get("pending_update_count", 0)})
        save_state(state)
    return ok, reason


def verify_webhook(tunnel_url):
    return webhook_status(tunnel_url)[0]


def _cleanup_owned_child():
    state = load_state() or {}
    if is_owned_tunnel(state):
        terminate_pid(state["pid"], force=True)


def stop_tunnel():
    state = load_state() or {}
    if is_owned_supervisor(state) and int(state["supervisor_pid"]) != os.getpid():
        terminate_pid(state["supervisor_pid"], force=True)
    if is_owned_tunnel(state):
        terminate_pid(state["pid"], force=True)
    if state.get("ingress_pid"):
        info = get_process_info(state["ingress_pid"])
        if info and "restricted_ingress.py" in info.get("command_line", "").lower():
            terminate_pid(state["ingress_pid"], force=True)
    try:
        STATE_FILE.unlink(missing_ok=True)
    except OSError:
        pass
    print("[OK] Rentkom-owned tunnel processes stopped; unrelated processes untouched")


def run_supervisor(local_target=DEFAULT_LOCAL_TARGET):
    lock = SupervisorLock()
    if not lock.acquire():
        print("[OK] Another Rentkom supervisor owns the cross-process lock", flush=True)
        return
    owner_id, stopping = uuid.uuid4().hex, False
    def request_stop(_signum, _frame):
        nonlocal stopping
        stopping = True
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, request_stop)
    try:
        ingress_pid = start_restricted_ingress()
        state = load_state() or {}
        supervisor_info = get_process_info(os.getpid()) or {}
        state.update({"owner_id": owner_id, "supervisor_pid": os.getpid(),
                      "supervisor_started_at": time.time(), "local_target": local_target,
                      "supervisor_process_created": supervisor_info.get("created", ""),
                      "ingress_pid": ingress_pid})
        save_state(state)  # ownership is recorded before network activity
        print(f"[*] Supervisor owns lock (PID: {os.getpid()})", flush=True)
        pid, url = detect_existing_tunnel()
        if not pid:
            _cleanup_owned_child()
            pid, url = start_tunnel(local_target=local_target, owner_id=owner_id)
        ok, reason = sync_telegram_webhook(url)
        print(f"[{'OK' if ok else '!'}] Telegram webhook: {reason}", flush=True)
        while not stopping:
            time.sleep(15)
            state = load_state() or {}
            if not is_owned_tunnel(state) or not check_tunnel_health(url, timeout=10):
                if is_owned_tunnel(state):
                    terminate_pid(state["pid"], force=True)
                pid, url = start_tunnel(local_target=local_target, owner_id=owner_id)
                ok, reason = sync_telegram_webhook(url)
                print(f"[{'OK' if ok else '!'}] Telegram webhook: {reason}", flush=True)
    finally:
        _cleanup_owned_child()
        if "ingress_pid" in locals() and ingress_pid:
            terminate_pid(ingress_pid, force=True)
        current = load_state() or {}
        if current.get("owner_id") == owner_id:
            try:
                STATE_FILE.unlink(missing_ok=True)
            except OSError:
                pass
        lock.release()


def ensure_tunnel():
    pid, url = detect_existing_tunnel()
    if not pid:
        pid, url = start_tunnel()
    ok, reason = sync_telegram_webhook(url)
    return {"pid": pid, "tunnel_url": url, "telegram_webhook_synced": ok, "telegram_info": reason}


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Manage the Rentkom Cloudflare Tunnel")
    parser.add_argument("--action", choices=["ensure", "start", "stop", "status", "daemon"], default="ensure")
    args = parser.parse_args()
    if args.action == "ensure":
        result = ensure_tunnel(); print(json.dumps(result, indent=2))
        raise SystemExit(0 if result["telegram_webhook_synced"] else 1)
    if args.action == "daemon":
        run_supervisor(); return
    if args.action == "start":
        _pid, url = start_tunnel(); ok, reason = sync_telegram_webhook(url); print(reason)
        raise SystemExit(0 if ok else 1)
    if args.action == "stop":
        stop_tunnel(); return
    pid, url = detect_existing_tunnel()
    if pid and url:
        ok, info, reason = webhook_status(url)
        if ok:
            print(f"HEALTHY: tunnel={url} pid={pid} pending_updates={info.get('pending_update_count', 0)}")
            raise SystemExit(0)
        print(f"DEGRADED: tunnel={url}; Telegram webhook: {reason}")
    else:
        print("INACTIVE: no healthy Rentkom-owned Cloudflare Tunnel")
    raise SystemExit(1)


if __name__ == "__main__":
    main()
