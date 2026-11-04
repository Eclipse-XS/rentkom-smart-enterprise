# -*- coding: utf-8 -*-
"""
Tests for Phase 7 / Operations & Deployment Gate:
OPS-01: Full AI profile startup validation & Ollama container connectivity
OPS-02: Backup/restore scripts and observability configuration validation
"""
import os
import re
import json
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from core.main import app
from core.config import settings
from tests.safety import assert_safe_test_database, is_safe_test_database_url
from scripts.ensure_ollama_models import ensure_models


def test_ops_t01_full_ai_profile_and_ollama_configuration():
    """
    OPS-T01: Full AI profile startup validation:
    1. compose.yaml defines ollama service with full-ai profile and healthcheck.
    2. business-api routes OLLAMA_BASE_URL to containerized ollama on rentkom-net by default.
    3. scripts/ensure_ollama_models.py is present and functional.
    """
    compose_path = "compose.yaml"
    assert os.path.exists(compose_path), "compose.yaml must exist"

    with open(compose_path, "r", encoding="utf-8") as f:
        compose_content = f.read()

    # 1. Ollama service profile and healthcheck
    assert "ollama:" in compose_content
    assert 'profiles: ["full-ai"]' in compose_content or '"full-ai"' in compose_content
    assert "healthcheck:" in compose_content

    # 2. business-api OLLAMA_BASE_URL defaults to ollama container inside network
    assert "OLLAMA_BASE_URL:" in compose_content
    assert "ollama:11434" in compose_content or "OLLAMA_HOST:-ollama" in compose_content

    # 3. Model check script exists
    script_path = "scripts/ensure_ollama_models.py"
    assert os.path.exists(script_path), f"Script {script_path} must exist"

    # Test ensure_models handles missing/available models cleanly with mock
    mock_tags = {
        "models": [
            {"name": "qwen3:4b-instruct"},
            {"name": "mxbai-embed-large:latest"},
        ]
    }
    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_tags
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        res = ensure_models("http://127.0.0.1:11434")
        assert res is True


def test_ops_t01_health_endpoint_reports_ollama_status(client):
    """
    OPS-T01: Health check endpoint accurately reports Ollama connectivity state.
    """
    # When Ollama is offline (default in test without running daemon)
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "ollama" in data
    assert data["ollama"] in ("healthy", "cold/offline", "degraded")


def test_ops_t02_backup_and_restore_scripts_exist():
    """
    OPS-T02: Dedicated backup and restore scripts exist and enforce safety.
    """
    backup_sh = "scripts/backup.sh"
    restore_sh = "scripts/restore.sh"

    assert os.path.exists(backup_sh), f"{backup_sh} must exist"
    assert os.path.exists(restore_sh), f"{restore_sh} must exist"

    with open(backup_sh, "r", encoding="utf-8") as f:
        backup_content = f.read()
    assert "pg_dump" in backup_content
    assert "--clean" in backup_content
    assert "--if-exists" in backup_content

    with open(restore_sh, "r", encoding="utf-8") as f:
        restore_content = f.read()
    assert "psql" in restore_content
    assert "DATA SAFETY ABORT" in restore_content


def test_ops_t02_restore_safety_guard_rejects_production():
    """
    OPS-T02: Restore script and tool strictly refuse to restore into protected databases.
    """
    from tools.backup_restore import restore_postgres_database

    with pytest.raises(RuntimeError, match="DATA SAFETY ABORT"):
        restore_postgres_database(
            "postgresql+psycopg2://postgres:postgres@127.0.0.1:5432/lab4_real_estate",
            "dummy.sql",
        )

    with pytest.raises(RuntimeError, match="DATA SAFETY ABORT"):
        restore_postgres_database(
            "postgresql+psycopg2://postgres:postgres@127.0.0.1:5432/postgres",
            "dummy.sql",
        )


def test_ops_t02_grafana_observability_provisioning():
    """
    OPS-T02: Grafana datasources and dashboards are properly provisioned and authenticated.
    """
    ds_path = "SMART_ENTERPRISE/monitoring/provisioning/datasources/datasources.yml"
    assert os.path.exists(ds_path), f"Datasource config {ds_path} must exist"

    with open(ds_path, "r", encoding="utf-8") as f:
        ds_content = f.read()

    # Verify API datasource carries X-Admin-Token and targets API_HOST
    assert "X-Admin-Token" in ds_content
    assert "${API_HOST}" in ds_content or "business-api" in ds_content

    # Verify dashboards directory contains valid JSON dashboards
    dashboards_dir = "SMART_ENTERPRISE/monitoring/dashboards"
    assert os.path.isdir(dashboards_dir), f"Dashboards directory {dashboards_dir} must exist"

    dashboard_files = [f for f in os.listdir(dashboards_dir) if f.endswith(".json")]
    assert len(dashboard_files) >= 1, "At least one Grafana dashboard must be provisioned"

    for df in dashboard_files:
        full_df_path = os.path.join(dashboards_dir, df)
        with open(full_df_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            assert "title" in data or "panels" in data, f"Dashboard {df} is missing title or panels"
