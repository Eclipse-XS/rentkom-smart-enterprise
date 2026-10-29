#!/usr/bin/env python3
import os
import sys
import subprocess
import argparse
import shutil
import time

# Ensure project root in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

def run_cmd(cmd_list, check=True):
    print(f"--> Executing: {' '.join(cmd_list)}")
    return subprocess.run(cmd_list, check=check)

def is_docker_running():
    try:
        res = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=2.5)
        return res.returncode == 0
    except Exception:
        return False

def cmd_up(args):
    profile = args.profile or "demo"
    print(f"=== Starting Rentkom Smart Enterprise (Profile: {profile}) ===")

    if is_docker_running():
        cmd = ["docker", "compose", "--profile", profile, "up", "-d"]
        if args.build:
            cmd.append("--build")
        run_cmd(cmd)
        print("\n[OK] Containers started! Access endpoints:")
        print(" - Business API: http://localhost:8000/docs")
        print(" - Grafana:      http://localhost:3000 (admin / admin)")
        print(" - Qdrant:       http://localhost:6333/dashboard")
        if profile in ["connected", "full-ai"]:
            print(" - n8n:          http://localhost:5678")
    else:
        print("[NOTICE] Docker daemon is not active.")
        print("[NOTICE] Running FastAPI Business Core in local standalone mode.")
        print("[NOTICE] External services (PostgreSQL, Qdrant, Ollama, Grafana) must be running independently if used.")
        if profile == "demo":
            print("[*] Checking PostgreSQL connectivity and seeding demo data...")
            run_cmd([sys.executable, "tools/seed_demo_data.py"], check=False)
        print("\n[*] Starting FastAPI Business Core on http://127.0.0.1:8000...")
        run_cmd([sys.executable, "-m", "uvicorn", "core.main:app", "--host", "127.0.0.1", "--port", "8000"])

def cmd_down(args):
    print("=== Stopping Rentkom Smart Enterprise ===")
    if is_docker_running():
        run_cmd(["docker", "compose", "--profile", "full-ai", "--profile", "connected", "--profile", "demo", "down"])
        print("[OK] Containers stopped.")
    else:
        print("[NOTICE] Docker is not running. Local processes terminate on Ctrl+C.")

def cmd_status(args):
    print("=== Rentkom Smart Enterprise System Status ===")
    if is_docker_running():
        print("\n--- Docker Containers ---")
        run_cmd(["docker", "compose", "ps", "-a"], check=False)
    else:
        print("\n--- Docker Status: INACTIVE ---")

    print("\n--- Local Diagnostic Checks ---")
    run_cmd([sys.executable, "-c", """
import httpx
try:
    r = httpx.get('http://127.0.0.1:8000/api/v1/health', timeout=2.0)
    print('[OK] Business API (8000):', r.json())
except Exception as e:
    print('[--] Business API (8000): Offline or degraded')

try:
    r = httpx.get('http://127.0.0.1:11434/api/tags', timeout=2.0)
    print('[OK] Ollama (11434): Online')
except Exception:
    print('[--] Ollama (11434): Offline')

try:
    r = httpx.get('http://127.0.0.1:6333/healthz', timeout=2.0)
    print('[OK] Qdrant (6333): Online')
except Exception:
    print('[--] Qdrant (6333): Offline')
"""], check=False)

def cmd_doctor(args):
    print("=== Rentkom Smart Enterprise Doctor ===")
    print(f"Python:       {sys.version.split()[0]} ({sys.executable})")

    docker_ok = is_docker_running()
    print(f"Docker:       {'RUNNING' if docker_ok else 'NOT RUNNING / NOT STARTED'}")

    env_exists = os.path.exists(".env")
    print(f".env file:    {'FOUND' if env_exists else 'NOT FOUND (using defaults / .env.example)'}")

    # Check ports
    import socket
    for port, name in [(5432, "PostgreSQL"), (8000, "Business API"), (3000, "Grafana"), (6333, "Qdrant"), (11434, "Ollama"), (5678, "n8n")]:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.5)
        res = sock.connect_ex(("127.0.0.1", port))
        sock.close()
        status_str = "LISTENING / IN USE" if res == 0 else "AVAILABLE"
        print(f"Port {port:<5} ({name}): {status_str}")

def cmd_test(args):
    print("=== Running Automated Test Suite ===")
    pytest_args = [sys.executable, "-m", "pytest", "tests/", "-v"]
    if args.filter:
        pytest_args.extend(["-k", args.filter])
    run_cmd(pytest_args, check=False)

def cmd_seed_demo(args):
    print("=== Seeding Synthetic Demo Data ===")
    run_cmd([sys.executable, "tools/seed_demo_data.py"])

def cmd_backup(args):
    print("=== Running System Backup ===")
    backup_dir = f"backups/backup_{int(time.time())}"
    os.makedirs(backup_dir, exist_ok=True)
    if os.path.exists("rentkom_demo.db"):
        shutil.copy2("rentkom_demo.db", os.path.join(backup_dir, "rentkom_demo.db"))
    print(f"[OK] Backup saved to {backup_dir}")

def main():
    parser = argparse.ArgumentParser(description="Rentkom Smart Enterprise Management CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # up
    p_up = subparsers.add_parser("up", help="Start system services")
    p_up.add_argument("--profile", choices=["demo", "connected", "full-ai"], default="demo", help="Execution profile")
    p_up.add_argument("--build", action="store_true", help="Rebuild container images")

    # down
    subparsers.add_parser("down", help="Stop system services")

    # status
    subparsers.add_parser("status", help="Show system status")

    # doctor
    subparsers.add_parser("doctor", help="Inspect prerequisites and health")

    # test
    p_test = subparsers.add_parser("test", help="Run automated test suite")
    p_test.add_argument("-k", "--filter", help="Test filter expression")

    # seed-demo
    subparsers.add_parser("seed-demo", help="Seed synthetic demo datasets")

    # backup
    subparsers.add_parser("backup", help="Create baseline backup")

    args = parser.parse_args()

    commands = {
        "up": cmd_up,
        "down": cmd_down,
        "status": cmd_status,
        "doctor": cmd_doctor,
        "test": cmd_test,
        "seed-demo": cmd_seed_demo,
        "backup": cmd_backup
    }

    commands[args.command](args)

if __name__ == "__main__":
    main()
