import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Optional
from urllib.parse import urlparse

from tests.safety import assert_safe_test_database


def _find_pg_binary(binary_name: str) -> str:
    """Locates pg_dump or psql on PATH or inside standard Windows PostgreSQL installation directories."""
    on_path = shutil.which(binary_name)
    if on_path:
        return on_path

    pg_roots = [
        r"C:\Program Files\PostgreSQL\18\bin",
        r"C:\Program Files\PostgreSQL\17\bin",
        r"C:\Program Files\PostgreSQL\16\bin",
        r"C:\Program Files\PostgreSQL\15\bin",
    ]
    exe_name = f"{binary_name}.exe" if sys.platform == "win32" and not binary_name.endswith(".exe") else binary_name
    for root in pg_roots:
        candidate = os.path.join(root, exe_name)
        if os.path.isfile(candidate):
            return candidate

    raise FileNotFoundError(
        f"PostgreSQL binary '{binary_name}' not found on PATH or standard PostgreSQL installation directories."
    )


def _parse_pg_url(db_url: str) -> dict:
    parsed = urlparse(db_url)
    if not parsed.scheme.startswith("postgresql"):
        raise ValueError(f"Expected a PostgreSQL URL, got scheme '{parsed.scheme}'.")
    return {
        "host": parsed.hostname or "127.0.0.1",
        "port": str(parsed.port or 5432),
        "user": parsed.username or "postgres",
        "password": parsed.password or "",
        "dbname": (parsed.path or "").lstrip("/"),
    }


def _sanitize_dump_for_compatibility(sql_path: str) -> None:
    """
    Comment out parameters unknown to older PostgreSQL server versions (e.g. transaction_timeout introduced in PG 17+).
    """
    try:
        with open(sql_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        if "transaction_timeout" in content:
            sanitized = re.sub(r"(?m)^(\s*SET\s+transaction_timeout\s*=.*)$", r"-- \1", content)
            with open(sql_path, "w", encoding="utf-8") as f:
                f.write(sanitized)
    except Exception:
        pass


def backup_postgres_database(db_url: str, output_file: Optional[str] = None) -> str:
    """
    Executes a logical PostgreSQL backup using pg_dump (--no-owner --no-privileges --clean --if-exists).
    Verifies that pg_dump exits with code 0 and produces a non-empty SQL dump file.
    """
    pg_dump_bin = _find_pg_binary("pg_dump")
    conn_info = _parse_pg_url(db_url)
    if not conn_info["dbname"]:
        raise ValueError("Database URL is missing a database name.")

    if not output_file:
        backup_dir = os.path.join("backups", f"pg_backup_{int(time.time())}")
        os.makedirs(backup_dir, exist_ok=True)
        output_file = os.path.join(backup_dir, f"{conn_info['dbname']}_dump.sql")
    else:
        os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)

    env = os.environ.copy()
    if conn_info["password"]:
        env["PGPASSWORD"] = conn_info["password"]

    cmd = [
        pg_dump_bin,
        "-h", conn_info["host"],
        "-p", conn_info["port"],
        "-U", conn_info["user"],
        "-d", conn_info["dbname"],
        "--no-owner",
        "--no-privileges",
        "--clean",
        "--if-exists",
        "-f", output_file,
    ]

    res = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=120)
    if res.returncode != 0:
        raise RuntimeError(f"pg_dump failed (exit={res.returncode}): {res.stderr.strip()}")

    if not os.path.isfile(output_file) or os.path.getsize(output_file) == 0:
        raise RuntimeError(f"pg_dump did not produce a valid non-empty backup file at '{output_file}'.")

    _sanitize_dump_for_compatibility(output_file)
    return output_file


def restore_postgres_database(target_db_url: str, backup_file: str) -> None:
    """
    Restores a logical SQL backup into a verified disposable test PostgreSQL database using psql.
    Enforces assert_safe_test_database(target_db_url) so a live/production database is NEVER overwritten.
    """
    assert_safe_test_database(target_db_url)

    if not os.path.isfile(backup_file) or os.path.getsize(backup_file) == 0:
        raise FileNotFoundError(f"Backup file '{backup_file}' does not exist or is empty.")

    _sanitize_dump_for_compatibility(backup_file)

    psql_bin = _find_pg_binary("psql")
    conn_info = _parse_pg_url(target_db_url)

    env = os.environ.copy()
    if conn_info["password"]:
        env["PGPASSWORD"] = conn_info["password"]

    cmd = [
        psql_bin,
        "-h", conn_info["host"],
        "-p", conn_info["port"],
        "-U", conn_info["user"],
        "-d", conn_info["dbname"],
        "-v", "ON_ERROR_STOP=1",
        "-f", backup_file,
    ]

    res = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=120)
    if res.returncode != 0:
        raise RuntimeError(f"psql restore failed (exit={res.returncode}): {res.stderr.strip()}")
