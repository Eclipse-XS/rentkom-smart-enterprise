# -*- coding: utf-8 -*-
"""
Deployment & Infrastructure Security Regression Suite.
Covers:
- SEC-T11: compose.yaml binds postgres to 127.0.0.1 by default.
- SEC-T12: compose.yaml binds qdrant to 127.0.0.1 by default.
- SEC-T13: compose.yaml binds ollama to 127.0.0.1 by default.
- SEC-T14: compose.yaml specifies n8n image >= 1.76.1.
- SEC-T15: compose.yaml sets N8N_BLOCK_ENV_ACCESS_IN_NODE=true.
- SEC-T16: Unauthenticated request to /api/v1/analytics/summary returns 401/403.
- SEC-T17: Authenticated administrator can access /api/v1/analytics/summary.
- SEC-T18: /api/v1/telegram/webhook-mock returns 403 or 404 when ENVIRONMENT=production or ALLOW_TEST_WEBHOOK=false.
- SEC-T19: Dockerfile defines non-root user.
"""
import re
from pathlib import Path
import pytest
from core.config import settings

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
COMPOSE_FILE = ROOT_DIR / "compose.yaml"
DOCKERFILE = ROOT_DIR / "Dockerfile"


def _read_compose_text() -> str:
    assert COMPOSE_FILE.exists(), f"compose.yaml not found at {COMPOSE_FILE}"
    return COMPOSE_FILE.read_text(encoding="utf-8")


def _read_dockerfile_text() -> str:
    assert DOCKERFILE.exists(), f"Dockerfile not found at {DOCKERFILE}"
    return DOCKERFILE.read_text(encoding="utf-8")


def test_sec_t11_compose_binds_postgres_to_127_0_0_1_by_default():
    """SEC-T11: compose.yaml binds postgres to 127.0.0.1 by default."""
    text = _read_compose_text()
    # Find postgres service block
    pg_match = re.search(r"postgres:\s*\n(.*?)(?=\n\s\s[a-zA-Z0-9_-]+:|$)", text, re.DOTALL)
    assert pg_match, "postgres service definition not found in compose.yaml"
    pg_block = pg_match.group(1)

    # Verify ports binding specifies 127.0.0.1 default
    # Matches ${POSTGRES_BIND_IP:-127.0.0.1} or 127.0.0.1:
    assert "127.0.0.1" in pg_block, "postgres service does not default to 127.0.0.1 binding"
    port_line = re.search(r'-\s*"([^"]*5432[^"]*)"', pg_block)
    assert port_line, "postgres port mapping not found"
    mapping = port_line.group(1)
    assert "127.0.0.1" in mapping, f"postgres port mapping '{mapping}' does not bind to 127.0.0.1 by default"
    assert "0.0.0.0" not in mapping, f"postgres port mapping exposes 0.0.0.0: {mapping}"


def test_sec_t12_compose_binds_qdrant_to_127_0_0_1_by_default():
    """SEC-T12: compose.yaml binds qdrant to 127.0.0.1 by default."""
    text = _read_compose_text()
    qdrant_match = re.search(r"qdrant:\s*\n(.*?)(?=\n\s\s[a-zA-Z0-9_-]+:|$)", text, re.DOTALL)
    assert qdrant_match, "qdrant service definition not found in compose.yaml"
    qdrant_block = qdrant_match.group(1)

    # Verify ports binding specifies 127.0.0.1
    port_lines = re.findall(r'-\s*"([^"]*(?:6333|6334)[^"]*)"', qdrant_block)
    assert len(port_lines) >= 2, f"Expected at least 2 qdrant port lines, found {port_lines}"
    for line in port_lines:
        assert "127.0.0.1" in line, f"qdrant port line '{line}' does not bind to 127.0.0.1 by default"
        assert "0.0.0.0" not in line, f"qdrant port line '{line}' exposes 0.0.0.0"


def test_sec_t13_compose_binds_ollama_to_127_0_0_1_by_default():
    """SEC-T13: compose.yaml binds ollama to 127.0.0.1 by default."""
    text = _read_compose_text()
    ollama_match = re.search(r"ollama:\s*\n(.*?)(?=\n\s\s[a-zA-Z0-9_-]+:|$)", text, re.DOTALL)
    assert ollama_match, "ollama service definition not found in compose.yaml"
    ollama_block = ollama_match.group(1)

    port_lines = re.findall(r'-\s*"([^"]*11434[^"]*)"', ollama_block)
    assert len(port_lines) >= 1, f"ollama port mapping not found in block: {ollama_block}"
    for line in port_lines:
        assert "127.0.0.1" in line, f"ollama port line '{line}' does not bind to 127.0.0.1 by default"
        assert "0.0.0.0" not in line, f"ollama port line '{line}' exposes 0.0.0.0"


def test_sec_t14_compose_pins_advisory_patched_n8n_image():
    """SEC-T14: n8n is patched for CVE-2025-68613/CVE-2026-27577 and digest-pinned."""
    text = _read_compose_text()
    n8n_match = re.search(r"n8n:\s*\n(.*?)(?=\n\s\s[a-zA-Z0-9_-]+:|$)", text, re.DOTALL)
    assert n8n_match, "n8n service definition not found in compose.yaml"
    n8n_block = n8n_match.group(1)

    image_match = re.search(
        r"image:\s*n8nio/n8n:([0-9]+\.[0-9]+\.[0-9]+)@sha256:([0-9a-f]{64})",
        n8n_block,
    )
    assert image_match, f"n8n image version pattern not found in {n8n_block}"
    ver_str = image_match.group(1)
    parts = [int(p) for p in ver_str.split(".")]
    assert len(parts) == 3
    version = tuple(parts)
    patched = version >= (2, 10, 1) or ((1, 123, 22) <= version < (2, 0, 0))
    assert patched, f"n8n version {ver_str} is affected by CVE-2026-27577"


def test_sec_t15_compose_sets_n8n_block_env_access_in_node_true():
    """SEC-T15: compose.yaml sets N8N_BLOCK_ENV_ACCESS_IN_NODE=true."""
    text = _read_compose_text()
    n8n_match = re.search(r"n8n:\s*\n(.*?)(?=\n\s\s[a-zA-Z0-9_-]+:|$)", text, re.DOTALL)
    assert n8n_match, "n8n service definition not found in compose.yaml"
    n8n_block = n8n_match.group(1)

    env_match = re.search(r'N8N_BLOCK_ENV_ACCESS_IN_NODE:\s*["\']?(true|1)["\']?', n8n_block, re.IGNORECASE)
    assert env_match, f"N8N_BLOCK_ENV_ACCESS_IN_NODE: true not set in n8n environment: {n8n_block}"


def test_sec_t16_unauthenticated_request_to_analytics_returns_401_or_403(client):
    """SEC-T16: Unauthenticated request to /api/v1/analytics/summary returns 401/403."""
    resp = client.get("/api/v1/analytics/summary")
    assert resp.status_code in (401, 403), f"Expected 401/403, got {resp.status_code}: {resp.text}"


def test_sec_t17_authenticated_administrator_can_access_analytics(client):
    """SEC-T17: Authenticated administrator can access /api/v1/analytics/summary."""
    admin_key = settings.ADMIN_API_KEY or "rentkom-demo-admin-key-2026"
    resp = client.get("/api/v1/analytics/summary", headers={"X-Admin-Token": admin_key})
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "total_requests" in body
    assert "requests_by_status" in body
    assert "total_feedbacks" in body


def test_sec_t18_telegram_webhook_mock_fail_closed_in_production(client, monkeypatch):
    """SEC-T18: /api/v1/telegram/webhook-mock returns 403 or 404 when ENVIRONMENT=production or ALLOW_TEST_WEBHOOK=false."""
    update_payload = {
        "update_id": 999123,
        "message": {
            "chat": {"id": 12345},
            "text": "test mock ingress"
        }
    }

    # Case 1: ENVIRONMENT="production"
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "ALLOW_TEST_WEBHOOK", True)
    resp_prod = client.post("/api/v1/telegram/webhook-mock", json=update_payload)
    assert resp_prod.status_code in (403, 404), f"Expected 403/404 in production, got {resp_prod.status_code}"

    # Case 2: ALLOW_TEST_WEBHOOK=False
    monkeypatch.setattr(settings, "ENVIRONMENT", "demo")
    monkeypatch.setattr(settings, "ALLOW_TEST_WEBHOOK", False)
    resp_disallowed = client.post("/api/v1/telegram/webhook-mock", json=update_payload)
    assert resp_disallowed.status_code in (403, 404), f"Expected 403/404 when test webhook disabled, got {resp_disallowed.status_code}"


def test_sec_t19_dockerfile_defines_non_root_user():
    """SEC-T19: Dockerfile defines non-root user."""
    dockerfile_text = _read_dockerfile_text()
    user_match = re.search(r"^\s*USER\s+([a-zA-Z0-9_-]+)", dockerfile_text, re.MULTILINE)
    assert user_match, "Dockerfile does not contain a USER instruction"
    username = user_match.group(1).strip()
    assert username.lower() not in ("root", "0"), f"Dockerfile specifies root user: {username}"

    # Verify user creation line exists
    useradd_match = re.search(r"useradd.*" + re.escape(username), dockerfile_text)
    assert useradd_match, f"User '{username}' is not explicitly created with useradd in Dockerfile"
