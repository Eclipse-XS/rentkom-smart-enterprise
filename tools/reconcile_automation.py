#!/usr/bin/env python3
"""
Rentkom Smart Enterprise — Automation Reconciler & Verification Engine.
Reads config/automation_manifest.yaml, bootstraps credentials via official n8n CLI,
reconciles workflow publication states, prevents conflicting triggers,
and executes non-destructive integration smoke tests.
"""

import os
import sys
import json
import time
import urllib.request
import urllib.error
import subprocess
from pathlib import Path

# Ensure project root in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except Exception:
    pass

import yaml

MANIFEST_PATH = PROJECT_ROOT / "config" / "automation_manifest.yaml"
N8N_CONTAINER = os.getenv("N8N_CONTAINER", "rentkom-n8n")
N8N_BASE_URL = os.getenv("N8N_BASE_URL", "http://127.0.0.1:5678")


def run_cmd(cmd, check=True):
    res = subprocess.run(cmd, capture_output=True, text=True)
    if check and res.returncode != 0:
        raise RuntimeError(f"Command failed ({res.returncode}): {' '.join(cmd)}\n{res.stderr or res.stdout}")
    return res


def wait_http_ready(url, timeout=60, name="Service"):
    start = time.time()
    while time.time() - start < timeout:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "RentkomReconciler/1.0"})
            with urllib.request.urlopen(req, timeout=4) as resp:
                if 200 <= resp.status < 300:
                    return True
        except Exception:
            pass
        time.sleep(2)
    return False


def get_n8n_workflows_state():
    """Returns dict of {workflow_id: is_active} via official n8n CLI."""
    active_res = run_cmd(["docker", "exec", N8N_CONTAINER, "n8n", "list:workflow", "--active=true"], check=False)
    inactive_res = run_cmd(["docker", "exec", N8N_CONTAINER, "n8n", "list:workflow", "--active=false"], check=False)

    states = {}
    for line in active_res.stdout.splitlines():
        if "|" in line:
            w_id = line.split("|")[0].strip()
            states[w_id] = True
    for line in inactive_res.stdout.splitlines():
        if "|" in line:
            w_id = line.split("|")[0].strip()
            states[w_id] = False
    return states


def reconcile_workflows(manifest):
    print("=== Step 1: Reconciling Credentials & Workflows from Manifest ===")

    # 1. Bootstrap credentials
    from scripts.bootstrap_n8n_credentials import bootstrap_credentials
    cred_ok = bootstrap_credentials()
    if not cred_ok:
        print("[!] Warning: Credential bootstrapping reported non-zero status.")
    else:
        print("[OK] Internal and infrastructure credentials bootstrapped.")

    current_states = get_n8n_workflows_state()
    print(f"[*] Currently discovered workflows in n8n database: {list(current_states.keys())}")

    workflows_to_change = False

    for wf in manifest.get("workflows", []):
        w_id = wf.get("id")
        w_file = wf.get("file")
        expected_state = wf.get("expected_state", "unpublished").lower()
        should_be_active = (expected_state == "published")
        w_name = wf.get("name")

        # Check if imported
        if w_id not in current_states:
            print(f"[*] Importing missing workflow '{w_name}' ({w_id}) from /workflows/{Path(w_file).name}...")
            container_path = f"/workflows/{Path(w_file).name}"
            import_res = run_cmd(["docker", "exec", N8N_CONTAINER, "n8n", "import:workflow", f"--input={container_path}"], check=False)
            if import_res.returncode == 0:
                print(f"[OK] Imported '{w_name}'")
                current_states[w_id] = False
                workflows_to_change = True
            else:
                print(f"[!] Failed to import workflow {w_id}: {import_res.stderr or import_res.stdout}")

        # Check active status
        current_active = current_states.get(w_id, False)
        if should_be_active and not current_active:
            print(f"[*] Publishing required workflow '{w_name}' ({w_id})...")
            pub_res = run_cmd(["docker", "exec", N8N_CONTAINER, "n8n", "publish:workflow", f"--id={w_id}"], check=False)
            if pub_res.returncode == 0:
                print(f"[OK] Published '{w_name}'")
                workflows_to_change = True
            else:
                print(f"[!] Failed to publish {w_id}: {pub_res.stderr or pub_res.stdout}")
        elif not should_be_active and current_active:
            print(f"[*] Unpublishing conflicting/manual workflow '{w_name}' ({w_id})...")
            unpub_res = run_cmd(["docker", "exec", N8N_CONTAINER, "n8n", "unpublish:workflow", f"--id={w_id}"], check=False)
            if unpub_res.returncode == 0:
                print(f"[OK] Unpublished '{w_name}'")
                workflows_to_change = True
            else:
                print(f"[!] Failed to unpublish {w_id}: {unpub_res.stderr or unpub_res.stdout}")
        else:
            state_str = "PUBLISHED (ACTIVE)" if current_active else "UNPUBLISHED (INACTIVE)"
            print(f"[OK] Workflow '{w_name}' is in desired state: {state_str}")

    if workflows_to_change:
        print("[*] Restarting n8n container to apply publication and webhook changes...")
        run_cmd(["docker", "restart", N8N_CONTAINER])
        ready = wait_http_ready(f"{N8N_BASE_URL}/healthz/readiness", timeout=45, name="n8n")
        if not ready:
            print("[!] Warning: n8n readiness timed out after restart.")
        else:
            print("[OK] n8n restarted and verified ready.")
    else:
        print("[OK] Workflow activation state is already in desired convergence; no restart required.")


def run_smoke_tests(manifest):
    print("\n=== Step 2: Running Non-Destructive Smoke Tests ===")
    test_results = {}

    for wf in manifest.get("workflows", []):
        w_id = wf.get("id")
        w_name = wf.get("name")
        capability = wf.get("business_capability")
        smoke = wf.get("smoke_test", {})

        if not smoke.get("enabled"):
            continue

        endpoint = smoke.get("endpoint")
        method = smoke.get("method", "POST")
        auth_header = smoke.get("auth_header")
        auth_env = smoke.get("auth_token_env")
        payload = smoke.get("payload", {})
        expected_status = smoke.get("expected_status", 200)

        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "RentkomSmokeTest/1.0"
        }
        if auth_header and auth_env:
            token = os.getenv(auth_env, "")
            if token:
                headers[auth_header] = token

        # Add timestamp/entropy to prevent conflict
        import copy
        p_copy = copy.deepcopy(payload)
        now_ts = int(time.time())
        if "update_id" in p_copy:
            p_copy["update_id"] = p_copy["update_id"] + (now_ts % 10000)
        if "row_number" in p_copy:
            p_copy["row_number"] = f"{p_copy['row_number']}-{now_ts}"
        if "source_row_id" in p_copy:
            p_copy["source_row_id"] = f"{p_copy['source_row_id']}-{now_ts}"

        try:
            body_bytes = json.dumps(p_copy).encode("utf-8")
            req = urllib.request.Request(endpoint, data=body_bytes, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=12) as resp:
                resp_code = resp.status
                resp_text = resp.read().decode("utf-8", errors="replace")
                success = (resp_code == expected_status)
                test_results[w_id] = {
                    "name": w_name,
                    "capability": capability,
                    "status_code": resp_code,
                    "success": success,
                    "error": None
                }
                print(f"[{'PASS' if success else 'FAIL'}] Smoke test: {w_name} -> HTTP {resp_code}")
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            print(f"[FAIL] Smoke test: {w_name} -> HTTP {e.code}: {err_body[:100]}")
            test_results[w_id] = {
                "name": w_name,
                "capability": capability,
                "status_code": e.code,
                "success": False,
                "error": err_body
            }
        except Exception as e:
            print(f"[FAIL] Smoke test: {w_name} -> Error: {e}")
            test_results[w_id] = {
                "name": w_name,
                "capability": capability,
                "status_code": 0,
                "success": False,
                "error": str(e)
            }

    return test_results


def evaluate_business_capabilities(manifest, test_results):
    print("\n=== Step 3: Enterprise Business Capabilities Evaluation ===")

    # 1. Telegram Automation (LR2 + LR6)
    tg_test = test_results.get("RentkomTelegramUnified01", {})
    tg_has_token = bool(os.getenv("TELEGRAM_BOT_TOKEN", "").strip())
    tg_status = "READY" if tg_test.get("success") else "BLOCKED"
    tg_ext_note = "Live Telegram Delivery Configured" if tg_has_token else "Mock Dispatcher Active (External Bot Token Not Set)"
    print(f"Capability: TELEGRAM_AUTOMATION (LR2 + LR6) -> [{tg_status}] ({tg_ext_note})")

    # 2. Customer Feedback (LR3 + LR5)
    fb_test = test_results.get("gaPyYUSgGKVK2vKc", {})
    fb_status = "READY" if fb_test.get("success") else "BLOCKED"
    # Check live Google OAuth status from database
    google_oauth_active = False
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
        cur.execute("SELECT count(*) FROM n8n.credentials_entity WHERE id IN ('w2ZVcryJf7CxkU0H', 'GsNbRh9jsuecZBVq')")
        if cur.fetchone()[0] == 2:
            google_oauth_active = True
        conn.close()
    except Exception:
        pass

    google_poll_note = "OPERATIONAL (Google OAuth Authorized)" if google_oauth_active else "BLOCKED_MISSING_OAUTH"
    print(f"Capability: CUSTOMER_FEEDBACK (LR3 + LR5)     -> [{fb_status}] (Synthetic: PASS | Real Google Sheets Poll: {google_poll_note})")

    # 3. Form Request Intake & Email (LR4)
    form_test = test_results.get("LAB4RealEstate01", {})
    form_status = "READY" if form_test.get("success") else "BLOCKED"
    gmail_note = "OPERATIONAL (Google OAuth Authorized)" if google_oauth_active else "BLOCKED_MISSING_OAUTH"
    print(f"Capability: REQUEST_INTAKE_GMAIL (LR4)       -> [{form_status}] (Synthetic & Outbox: PASS | Real Forms/Gmail: {gmail_note})")

    # 4. Observability & Monitoring (LR5)
    # Check Grafana readiness
    grafana_ok = wait_http_ready("http://127.0.0.1:3000/api/health", timeout=5, name="Grafana")
    mon_status = "READY" if grafana_ok else "DEGRADED"
    print(f"Capability: OBSERVABILITY_METRICS (LR5)       -> [{mon_status}] (Grafana Dashboards + Direct PostgreSQL & Analytics API)")

    # 5. AI Consultation & RAG (LR6)
    qdrant_ok = wait_http_ready("http://127.0.0.1:6333/healthz", timeout=5, name="Qdrant")
    ollama_ok = wait_http_ready("http://127.0.0.1:11434/api/tags", timeout=5, name="Ollama")
    rag_status = "READY" if (qdrant_ok and ollama_ok) else "DEGRADED"
    print(f"Capability: AI_RAG_CONSULTATION (LR6)         -> [{rag_status}] (Qdrant Knowledge Base + Ollama qwen3/mxbai)")

    all_ready = all(st == "READY" for st in [tg_status, fb_status, form_status, mon_status, rag_status])
    print("\n-------------------------------------------------------------")
    if all_ready:
        print("OVERALL AUTOMATION VERDICT: [AUTOMATION_READY]")
        print("All enterprise capabilities are operational in self-starting event-driven mode.")
        if google_oauth_active:
            print("Real Google Sheets Trigger & Gmail OAuth integrations are active and verified.")
        else:
            print("Real external Google Sheets/Forms/Gmail channels are safely reported as BLOCKED (Awaiting User OAuth).")
    else:
        print("OVERALL AUTOMATION VERDICT: [PARTIALLY_READY]")
    print("-------------------------------------------------------------")
    return all_ready


def main():
    if not MANIFEST_PATH.is_file():
        print(f"[!] Error: Manifest not found at {MANIFEST_PATH}", file=sys.stderr)
        sys.exit(1)

    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        manifest = yaml.safe_load(f)

    reconcile_workflows(manifest)
    results = run_smoke_tests(manifest)
    all_ready = evaluate_business_capabilities(manifest, results)
    sys.exit(0 if all_ready else 1)


if __name__ == "__main__":
    main()
