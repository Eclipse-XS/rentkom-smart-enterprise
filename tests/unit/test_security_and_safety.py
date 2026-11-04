import pytest
from sqlalchemy import create_engine, text
from tests.safety import (
    assert_safe_test_database,
    ensure_test_schema_marker,
    is_safe_test_database_url,
    SAFETY_MARKER_TABLE,
)
from core.config import settings


def test_safety_guard_rejects_application_and_default_databases():
    """Verify that assert_safe_test_database refuses application, production, and default 'postgres' databases."""
    for forbidden_url in [
        "postgresql+psycopg2://postgres:postgres@127.0.0.1:5432/lab4_real_estate",
        "postgresql://user:pass@localhost:5432/lab4_real_estate",
        "postgresql://user:pass@localhost:5432/rentkom_prod",
        "postgresql://user:pass@localhost:5432/rentkom_production",
        "postgresql://user:pass@localhost:5432/rentkom",
        "postgresql://postgres:postgres@127.0.0.1:5432/postgres",
        "postgresql+psycopg2://postgres@127.0.0.1:5433/postgres",
        "postgresql://localhost:5432/template1",
    ]:
        assert not is_safe_test_database_url(forbidden_url), f"Should reject {forbidden_url}"
        with pytest.raises(RuntimeError) as exc_info:
            assert_safe_test_database(forbidden_url)
        assert "DATA SAFETY ABORT" in str(exc_info.value)


def test_safety_guard_rejects_unmarked_database():
    """Verify that databases without 'test', 'disposable', or 'temp' in the name are rejected."""
    with pytest.raises(RuntimeError):
        assert_safe_test_database("postgresql+psycopg2://postgres:postgres@127.0.0.1:5432/my_database")
    assert not is_safe_test_database_url("postgresql://localhost:5432/my_database")
    assert not is_safe_test_database_url("")


def test_safety_guard_accepts_test_database():
    """Verify that databases with 'test', 'disposable', or 'temp' in the name or sqlite are accepted."""
    assert is_safe_test_database_url("sqlite:///:memory:")
    assert is_safe_test_database_url("sqlite:///tmp/test.db")
    assert is_safe_test_database_url("postgresql+psycopg2://postgres:pass@localhost:5432/rentkom_test")
    assert is_safe_test_database_url("postgresql://localhost:5439/disposable_db")
    assert is_safe_test_database_url("postgresql://localhost:5439/temp_ci_db")
    assert_safe_test_database("postgresql://localhost:5439/rentkom_test")


def test_unauthorized_endpoints_rejected_401_missing_credentials(client):
    """Case 1: Protected administrative and internal endpoints return 401 when credentials are missing."""
    assert client.get("/api/v1/requests/").status_code == 401
    assert client.get("/api/v1/requests/1").status_code == 401
    assert client.patch("/api/v1/requests/1/status", json={"new_status": "IN_REVIEW"}).status_code == 401
    assert client.get("/api/v1/feedbacks/").status_code == 401
    assert client.get("/api/v1/notifications/outbox").status_code == 401
    assert client.post("/api/v1/notifications/process").status_code == 401
    assert client.post("/api/v1/telegram/send-message", json={"chat_id": 123, "text": "hi"}).status_code == 401
    assert client.post("/api/v1/telegram/inbox/guard", json={"update_id": 1}).status_code == 401
    assert client.post("/api/v1/telegram/inbox/complete", json={"update_id": 1, "route": "Start", "response_text": "ok"}).status_code == 401


def test_invalid_credentials_rejected_403(client):
    """Case 2: Protected endpoints return 403 Forbidden when an invalid token is supplied."""
    bad_headers = {"X-Admin-Token": "wrong-admin-token-000", "X-Internal-Token": "wrong-internal-token-000"}
    assert client.get("/api/v1/requests/", headers=bad_headers).status_code == 403
    assert client.get("/api/v1/requests/1", headers=bad_headers).status_code == 403
    assert client.patch("/api/v1/requests/1/status", json={"new_status": "IN_REVIEW"}, headers=bad_headers).status_code == 403
    assert client.get("/api/v1/feedbacks/", headers=bad_headers).status_code == 403
    assert client.get("/api/v1/notifications/outbox", headers=bad_headers).status_code == 403
    assert client.post("/api/v1/notifications/process", headers=bad_headers).status_code == 403
    assert client.post("/api/v1/telegram/send-message", json={"chat_id": 123, "text": "hi"}, headers=bad_headers).status_code == 403
    assert client.post("/api/v1/telegram/inbox/guard", json={"update_id": 1}, headers=bad_headers).status_code == 403
    assert client.post("/api/v1/telegram/inbox/complete", json={"update_id": 1, "route": "Start", "response_text": "ok"}, headers=bad_headers).status_code == 403


def test_authorized_endpoints_succeed_with_valid_credentials(client):
    """Case 3: Administrative and internal endpoints succeed when valid tokens are provided."""
    admin_headers = {"X-Admin-Token": settings.ADMIN_API_KEY}
    internal_headers = {"X-Internal-Token": settings.INTERNAL_API_KEY}

    assert client.get("/api/v1/requests/", headers=admin_headers).status_code == 200
    assert client.get("/api/v1/feedbacks/", headers=admin_headers).status_code == 200
    assert client.get("/api/v1/notifications/outbox", headers=admin_headers).status_code == 200
    assert client.post("/api/v1/notifications/process", headers=admin_headers).status_code == 200
    assert client.post("/api/v1/notifications/process", headers=internal_headers).status_code == 200


def test_missing_configured_server_key_returns_500_no_bypass(client, monkeypatch):
    """Case 4: Missing configured server key in non-demo mode returns 500 and never bypasses auth."""
    monkeypatch.setattr(settings, "ENVIRONMENT", "connected")
    monkeypatch.setattr(settings, "ADMIN_API_KEY", "")
    monkeypatch.setattr(settings, "INTERNAL_API_KEY", "")

    resp_admin = client.get("/api/v1/requests/", headers={"X-Admin-Token": "any-token"})
    assert resp_admin.status_code == 500
    assert "ADMIN_API_KEY" in resp_admin.json()["detail"]

    resp_internal = client.post("/api/v1/notifications/process", headers={"X-Internal-Token": "any-token"})
    assert resp_internal.status_code == 500
    assert "INTERNAL_API_KEY" in resp_internal.json()["detail"]


def test_environment_modes_demo_connected_production(client, monkeypatch):
    """Cases 5, 6, 7: Verify security boundaries across demo, connected, and production environments."""
    # Case 5: Demo environment allows webhook-mock and mock-dispatcher with test secret
    monkeypatch.setattr(settings, "ENVIRONMENT", "demo")
    monkeypatch.setattr(settings, "ALLOW_TEST_WEBHOOK", True)
    mock_wh = client.post("/api/v1/telegram/webhook-mock", json={"message": {"text": "/start", "chat": {"id": 1}}})
    assert mock_wh.status_code == 200

    # Case 6: Connected environment disables webhook-mock and requires secret on /telegram/webhook
    monkeypatch.setattr(settings, "ENVIRONMENT", "connected")
    monkeypatch.setattr(settings, "ALLOW_TEST_WEBHOOK", False)
    monkeypatch.setattr(settings, "TELEGRAM_SECRET_TOKEN", "live-tg-secret-token-2026")

    mock_wh_connected = client.post("/api/v1/telegram/webhook-mock", json={"message": {"text": "/start", "chat": {"id": 1}}})
    assert mock_wh_connected.status_code == 403

    wh_no_secret = client.post("/api/v1/telegram/webhook", json={"update_id": 5001, "message": {"text": "/start", "chat": {"id": 1}}})
    assert wh_no_secret.status_code == 401

    wh_bad_secret = client.post(
        "/api/v1/telegram/webhook",
        json={"update_id": 5001, "message": {"text": "/start", "chat": {"id": 1}}},
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"},
    )
    assert wh_bad_secret.status_code == 403

    wh_good_secret = client.post(
        "/api/v1/telegram/webhook",
        json={"update_id": 5001, "message": {"text": "/start", "chat": {"id": 1}}},
        headers={"X-Telegram-Bot-Api-Secret-Token": "live-tg-secret-token-2026"},
    )
    assert wh_good_secret.status_code == 200

    # Case 7: Production configuration validation rejects insecure defaults
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "ADMIN_API_KEY", "rentkom-demo-admin-key-2026")
    with pytest.raises(RuntimeError, match="ADMIN_API_KEY"):
        settings.validate_runtime_security()

    monkeypatch.setattr(settings, "ADMIN_API_KEY", "a" * 32)
    monkeypatch.setattr(settings, "INTERNAL_API_KEY", "b" * 32)
    monkeypatch.setattr(settings, "ALLOW_TEST_WEBHOOK", False)
    monkeypatch.setattr(settings, "NOTIFICATION_PROVIDER", "mock")
    with pytest.raises(RuntimeError, match="NOTIFICATION_PROVIDER"):
        settings.validate_runtime_security()


def test_public_customer_ingestion_remains_open(client):
    """Customer-facing submission endpoints must remain accessible without administrative tokens."""
    req_payload = {
        "full_name": "Тестовий Клієнт",
        "phone": "+380501234567",
        "email": "test.client@example.com",
        "request_purpose": "Хочу купити",
        "property_type": "Квартира",
        "property_location": "Личаківський р-н",
        "consultation_date": "2026-11-15",
        "source_channel": "REST_API",
    }
    r = client.post("/api/v1/requests/", json=req_payload)
    assert r.status_code == 202
    assert "submission_id" in r.json()

    fb_payload = {
        "client_identifier": "+380501234567",
        "service_rating": 5,
        "realtor_rating": 5,
        "feedback_text": "Чудовий сервіс!",
        "source_channel": "REST_API",
    }
    fb_r = client.post("/api/v1/feedbacks/", json=fb_payload)
    assert fb_r.status_code == 201


def test_zero_plaintext_secrets_in_provisioning():
    """Verify that committed provisioning configurations contain zero plaintext passwords."""
    with open("SMART_ENTERPRISE/monitoring/provisioning/datasources.yml", "r", encoding="utf-8") as f:
        content = f.read()
    assert "VadymGone123" not in content
    assert "${POSTGRES_PASSWORD" in content



def test_public_endpoint_rate_limiting(client, monkeypatch):
    """Verify that public customer ingestion endpoints are protected by rate limiting (HTTP 429)."""
    from core.security import public_rate_limiter
    public_rate_limiter.reset()
    monkeypatch.setattr(public_rate_limiter, "rpm", 3)

    req_payload = {
        "full_name": "Тестовий Клієнт",
        "phone": "+380501234567",
        "email": "test.client@example.com",
        "request_purpose": "Хочу купити",
        "property_type": "Квартира",
        "property_location": "Личаківський р-н",
        "consultation_date": "2026-11-15",
        "source_channel": "REST_API",
    }

    # First 3 requests succeed
    for _ in range(3):
        r = client.post("/api/v1/requests/", json=req_payload)
        assert r.status_code == 202

    # 4th request exceeds limit
    r_exceeded = client.post("/api/v1/requests/", json=req_payload)
    assert r_exceeded.status_code == 429
    assert "Rate limit exceeded" in r_exceeded.json()["detail"]
    assert r_exceeded.headers.get("retry-after") == "60"

    # Reset limiter for subsequent test isolation
    public_rate_limiter.reset()


def test_x_forwarded_for_spoofing_prevention(client, monkeypatch):
    """
    Verify that spoofing X-Forwarded-For headers from untrusted clients DOES NOT bypass rate limits.
    Forwarded headers are ONLY trusted when the direct peer is explicitly registered in TRUSTED_PROXIES.
    """
    from core.security import public_rate_limiter
    public_rate_limiter.reset()
    monkeypatch.setattr(public_rate_limiter, "rpm", 2)
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", [])  # No trusted proxies by default

    req_payload = {
        "full_name": "Тестовий Клієнт",
        "phone": "+380501234567",
        "email": "test.client@example.com",
        "request_purpose": "Хочу купити",
        "property_type": "Квартира",
        "property_location": "Личаківський р-н",
        "consultation_date": "2026-11-15",
        "source_channel": "REST_API",
    }

    # Attacker attempts to rotate X-Forwarded-For to bypass rate limiter
    r1 = client.post("/api/v1/requests/", json=req_payload, headers={"X-Forwarded-For": "1.1.1.1"})
    assert r1.status_code == 202

    r2 = client.post("/api/v1/requests/", json=req_payload, headers={"X-Forwarded-For": "2.2.2.2"})
    assert r2.status_code == 202

    # 3rd request from same client with different spoofed IP must still be blocked
    r3 = client.post("/api/v1/requests/", json=req_payload, headers={"X-Forwarded-For": "3.3.3.3"})
    assert r3.status_code == 429
    assert "Rate limit exceeded" in r3.json()["detail"]

    # When client is behind an explicitly trusted proxy, X-Forwarded-For is respected
    public_rate_limiter.reset()
    # TestClient direct peer host is "testclient"
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", ["testclient", "127.0.0.1"])
    r_proxy1 = client.post("/api/v1/requests/", json=req_payload, headers={"X-Forwarded-For": "203.0.113.10"})
    r_proxy2 = client.post("/api/v1/requests/", json=req_payload, headers={"X-Forwarded-For": "203.0.113.20"})
    assert r_proxy1.status_code == 202
    assert r_proxy2.status_code == 202

    public_rate_limiter.reset()


def test_rag_endpoint_rate_limiting(client, monkeypatch):
    """
    Verify that POST /api/v1/rag/ask is protected by independent, strict rate limiting (HTTP 429).
    Also checks Retry-After header and spoofing resistance.
    """
    from core.security import rag_rate_limiter
    from core.schemas import RAGQueryResponse
    rag_rate_limiter.reset()
    monkeypatch.setattr(rag_rate_limiter, "rpm", 2)
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", [])

    async def fake_query_rag(q, history=None):
        return RAGQueryResponse(
            mode="grounded",
            confidence_score=0.85,
            answer="Відповідь для тестування",
            sources=["Тестовий_документ.pdf"],
            citations=[],
            latency_seconds=0.1,
        )

    from core.api.v1.endpoints import rag as rag_endpoint
    monkeypatch.setattr(rag_endpoint, "query_rag", fake_query_rag)

    rag_payload = {"question": "Які послуги надає агентство?"}

    # 2 requests succeed
    r1 = client.post("/api/v1/rag/ask", json=rag_payload)
    assert r1.status_code == 200
    r2 = client.post("/api/v1/rag/ask", json=rag_payload)
    assert r2.status_code == 200

    # 3rd request is blocked with 429 and Retry-After
    r3 = client.post("/api/v1/rag/ask", json=rag_payload)
    assert r3.status_code == 429
    assert "RAG rate limit exceeded" in r3.json()["detail"]
    assert "retry-after" in r3.headers

    # Spoofing X-Forwarded-For does not bypass RAG limiter
    r_spoof = client.post("/api/v1/rag/ask", json=rag_payload, headers={"X-Forwarded-For": "8.8.8.8"})
    assert r_spoof.status_code == 429

    rag_rate_limiter.reset()


def test_rag_configurable_contact_details(monkeypatch):
    """Verify that fallback/abstention contact details in RAG service are dynamically configurable."""
    from core.services.rag_service import get_abstention_response, get_fallback_response

    custom_phone = "+38 (044) 999-88-77"
    monkeypatch.setattr(settings, "AGENCY_CONTACT_PHONE", custom_phone)

    abstention = get_abstention_response()
    fallback = get_fallback_response()

    assert custom_phone in abstention
    assert custom_phone in fallback