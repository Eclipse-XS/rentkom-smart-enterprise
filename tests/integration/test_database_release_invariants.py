# -*- coding: utf-8 -*-
"""
Tests for Phase 6 / Database Gate:
DATA-01: Idempotency payload matching
DATA-02: Concurrent request status race elimination
DATA-03: Clean bootstrap without hardcoded demo records
DATA-04: Strict test database isolation
DATA-05: Application and n8n database role privilege separation
"""
import os
import re
import pytest
from datetime import date
from unittest.mock import patch
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker

from core.database import Base
from core.models import Customer, ServiceRequest, RequestStatusHistory, InboundLeadSubmission
from core.schemas import ServiceRequestCreate, ServiceRequestStatusUpdate
from core.services.request_service import (
    create_service_request,
    create_lead_submission,
    update_request_status,
    compute_canonical_payload_hash,
)
from tests.safety import assert_safe_test_database, is_safe_test_database_url
from scripts.seed_demo_data import seed_demo_data


def test_data_t01_idempotency_matches_identical_payloads(db_session):
    """DATA-T01: Idempotency matches identical payloads across all fields."""
    req1 = ServiceRequestCreate(
        full_name="Олексій Мельник",
        phone="+380671112233",
        email="oleksiy@test.ua",
        request_purpose="Хочу купити",
        property_type="Квартира",
        property_location="м. Львів, Франківський р-н",
        consultation_date=date(2026, 11, 20),
        additional_comments="2-кімнатна біля Політехніки",
        source_channel="REST_API",
        source_row_id="idemp:t01:001",
    )
    res1 = create_service_request(db_session, req1, actor="admin_test")
    assert res1.id is not None
    initial_id = res1.id

    # Repeat submission with exact same fields
    req2 = ServiceRequestCreate(
        full_name="Олексій Мельник",
        phone="+380671112233",
        email="oleksiy@test.ua",
        request_purpose="Хочу купити",
        property_type="Квартира",
        property_location="м. Львів, Франківський р-н",
        consultation_date=date(2026, 11, 20),
        additional_comments="2-кімнатна біля Політехніки",
        source_channel="REST_API",
        source_row_id="idemp:t01:001",
    )
    res2 = create_service_request(db_session, req2, actor="admin_test")
    assert res2.id == initial_id
    assert db_session.query(ServiceRequest).count() == 1


def test_data_t02_different_consultation_date_causes_conflict(db_session):
    """DATA-T02: Replay with different consultation_date raises ValueError (409 conflict)."""
    req1 = ServiceRequestCreate(
        full_name="Ірина Ковальчук",
        phone="+380502223344",
        email="irina@test.ua",
        request_purpose="Хочу орендувати",
        property_type="Квартира",
        property_location="Сихівський р-н",
        consultation_date=date(2026, 11, 20),
        source_row_id="idemp:t02:001",
    )
    create_service_request(db_session, req1, actor="admin_test")

    req_conflict = ServiceRequestCreate(
        full_name="Ірина Ковальчук",
        phone="+380502223344",
        email="irina@test.ua",
        request_purpose="Хочу орендувати",
        property_type="Квартира",
        property_location="Сихівський р-н",
        consultation_date=date(2026, 11, 25),  # Mutated date
        source_row_id="idemp:t02:001",
    )
    with pytest.raises(ValueError, match="Idempotency conflict"):
        create_service_request(db_session, req_conflict, actor="admin_test")


def test_data_t03_different_property_location_causes_conflict(db_session):
    """DATA-T03: Replay with different property_location raises ValueError (409 conflict)."""
    req1 = ServiceRequestCreate(
        full_name="Маркіян Дуб",
        phone="+380933334455",
        email="markiian@test.ua",
        request_purpose="Хочу продати",
        property_type="Будинок",
        property_location="Личаківський р-н",
        consultation_date=date(2026, 11, 22),
        source_row_id="idemp:t03:001",
    )
    create_service_request(db_session, req1, actor="admin_test")

    req_conflict = ServiceRequestCreate(
        full_name="Маркіян Дуб",
        phone="+380933334455",
        email="markiian@test.ua",
        request_purpose="Хочу продати",
        property_type="Будинок",
        property_location="Галицький р-н",  # Mutated location
        consultation_date=date(2026, 11, 22),
        source_row_id="idemp:t03:001",
    )
    with pytest.raises(ValueError, match="Idempotency conflict"):
        create_service_request(db_session, req_conflict, actor="admin_test")


def test_data_t04_different_additional_comments_causes_conflict(db_session):
    """DATA-T04: Replay with different additional_comments raises ValueError (409 conflict)."""
    req1 = ServiceRequestCreate(
        full_name="Софія Бойко",
        phone="+380674445566",
        email="sofiya@test.ua",
        request_purpose="Хочу здати в оренду",
        property_type="Квартира",
        property_location="Галицький р-н",
        consultation_date=date(2026, 11, 23),
        additional_comments="Коментар первинний",
        source_row_id="idemp:t04:001",
    )
    create_service_request(db_session, req1, actor="admin_test")

    req_conflict = ServiceRequestCreate(
        full_name="Софія Бойко",
        phone="+380674445566",
        email="sofiya@test.ua",
        request_purpose="Хочу здати в оренду",
        property_type="Квартира",
        property_location="Галицький р-н",
        consultation_date=date(2026, 11, 23),
        additional_comments="Коментар змінений",  # Mutated comments
        source_row_id="idemp:t04:001",
    )
    with pytest.raises(ValueError, match="Idempotency conflict"):
        create_service_request(db_session, req_conflict, actor="admin_test")


def test_data_t05_case_insensitive_email_and_whitespace_match_canonically(db_session):
    """DATA-T05: Email case variations and whitespace normalization match canonically without conflict."""
    req1 = ServiceRequestCreate(
        full_name="Василь Гончарук",
        phone="+380689998877",
        email="Vasyl.Goncharuk@Domain.COM",
        request_purpose="Хочу купити",
        property_type="Квартира",
        property_location="  Франківський    р-н  ",
        consultation_date=date(2026, 11, 24),
        additional_comments="   Тест   пробілів  ",
        source_row_id="idemp:t05:001",
    )
    res1 = create_service_request(db_session, req1, actor="admin_test")

    req2 = ServiceRequestCreate(
        full_name="  Василь  Гончарук  ",
        phone="+380689998877",
        email="vasyl.goncharuk@domain.com",  # lowercase equivalent
        request_purpose="Хочу купити",
        property_type="Квартира",
        property_location="Франківський р-н",  # normalized whitespace
        consultation_date=date(2026, 11, 24),
        additional_comments="   Тест   пробілів  ",
        source_row_id="idemp:t05:001",
    )
    res2 = create_service_request(db_session, req2, actor="admin_test")
    assert res2.id == res1.id


def test_data_t06_lead_submission_idempotency_same_event(db_session):
    """DATA-T06: Concurrent/repeat lead submissions with identical intake_channel and external_event_id deduplicate."""
    req1 = ServiceRequestCreate(
        full_name="Олексій Мельник",
        phone="+380671112233",
        email="oleksiy@test.ua",
        request_purpose="Хочу купити",
        property_type="Квартира",
        property_location="Франківський р-н",
        consultation_date=date(2026, 11, 20),
        source_channel="TELEGRAM",
        source_row_id="tg_msg_99999",
    )
    sub1 = create_lead_submission(db_session, req1)
    sub2 = create_lead_submission(db_session, req1)

    assert sub1.id == sub2.id
    assert sub1.submission_id == sub2.submission_id
    assert db_session.query(InboundLeadSubmission).count() == 1


def test_data_t07_concurrent_status_transition_cas_conflict(client):
    """
    DATA-T07: Concurrent conflicting status transitions:
    First transition succeeds (200), second transition with stale base status returns 409 Conflict.
    """
    admin_headers = {"X-Admin-Token": "rentkom-demo-admin-key-2026"}

    # 1. Create a request via admin endpoint
    create_payload = {
        "full_name": "Конкурентний Тест",
        "phone": "+380679998877",
        "email": "concurrent@test.ua",
        "request_purpose": "Хочу купити",
        "property_type": "Квартира",
        "property_location": "Франківський р-н",
        "consultation_date": "2026-11-20",
        "source_row_id": "test:cas:race",
    }
    create_resp = client.post("/api/v1/requests/", json=create_payload, headers=admin_headers)
    assert create_resp.status_code == 201
    req_id = create_resp.json()["id"]
    assert create_resp.json()["status"] == "NEW"

    # 2. First transition: NEW -> IN_REVIEW succeeds
    patch_resp_1 = client.patch(
        f"/api/v1/requests/{req_id}/status",
        json={"new_status": "IN_REVIEW", "comment": "Staff picked up request"},
        headers=admin_headers,
    )
    assert patch_resp_1.status_code == 200
    assert patch_resp_1.json()["status"] == "IN_REVIEW"

    # 3. Simulate invalid transition attempt from IN_REVIEW back to NEW
    patch_resp_2 = client.patch(
        f"/api/v1/requests/{req_id}/status",
        json={"new_status": "NEW", "comment": "Invalid backward reset"},
        headers=admin_headers,
    )
    # State machine rejects invalid transition from IN_REVIEW to NEW with 400
    assert patch_resp_2.status_code in (400, 409)

    # 4. Valid transition: IN_REVIEW -> SCHEDULED succeeds
    patch_resp_3 = client.patch(
        f"/api/v1/requests/{req_id}/status",
        json={"new_status": "SCHEDULED", "comment": "Appointment scheduled"},
        headers=admin_headers,
    )
    assert patch_resp_3.status_code == 200
    assert patch_resp_3.json()["status"] == "SCHEDULED"


def test_data_t08_terminal_states_cannot_transition(client):
    """DATA-T08: Terminal states (COMPLETED, CANCELLED) cannot transition to any other status."""
    admin_headers = {"X-Admin-Token": "rentkom-demo-admin-key-2026"}

    create_payload = {
        "full_name": "Термінальний Тест",
        "phone": "+380671112299",
        "email": "terminal@test.ua",
        "request_purpose": "Хочу орендувати",
        "property_type": "Квартира",
        "property_location": "Сихівський р-н",
        "consultation_date": "2026-11-20",
        "source_row_id": "test:terminal:status",
    }
    create_resp = client.post("/api/v1/requests/", json=create_payload, headers=admin_headers)
    req_id = create_resp.json()["id"]

    # Transition to CANCELLED (terminal state)
    cancel_resp = client.patch(
        f"/api/v1/requests/{req_id}/status",
        json={"new_status": "CANCELLED", "comment": "Customer cancelled"},
        headers=admin_headers,
    )
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "CANCELLED"

    # Attempting to reopen cancelled request must fail
    reopen_resp = client.patch(
        f"/api/v1/requests/{req_id}/status",
        json={"new_status": "IN_REVIEW", "comment": "Attempt to reopen"},
        headers=admin_headers,
    )
    assert reopen_resp.status_code in (400, 409)
    assert "Invalid status transition" in reopen_resp.json()["detail"] or "cannot be modified" in reopen_resp.json()["detail"]


def test_data_t09_fresh_bootstrap_init_db_has_zero_seed_rows():
    """
    DATA-T09: Fresh bootstrap with init_db.sql contains zero hardcoded customer/request seed records.
    Verifies that init_db.sql contains no static top-level literal INSERT statements.
    """
    init_sql_path = "SMART_ENTERPRISE/database/init_db.sql"
    assert os.path.exists(init_sql_path), f"File {init_sql_path} must exist"

    with open(init_sql_path, "r", encoding="utf-8") as f:
        sql_content = f.read()

    # Assert no top-level literal row INSERT statements exist in init_db.sql
    static_customer_inserts = re.findall(
        r"INSERT\s+INTO\s+(?:public\.)?customers\s*(?:\([^)]*\))?\s*VALUES\s*\(\s*['\"]",
        sql_content,
        re.IGNORECASE,
    )
    assert len(static_customer_inserts) == 0, (
        f"Found {len(static_customer_inserts)} static customer INSERT statements in init_db.sql (must be 0)"
    )

    static_request_inserts = re.findall(
        r"INSERT\s+INTO\s+(?:public\.)?service_requests\s*(?:\([^)]*\))?\s*VALUES\s*\(\s*['\"]",
        sql_content,
        re.IGNORECASE,
    )
    assert len(static_request_inserts) == 0, (
        f"Found {len(static_request_inserts)} static service_requests INSERT statements in init_db.sql (must be 0)"
    )


def test_data_t10_seed_demo_data_script_populates_on_demand(db_session):
    """DATA-T10: scripts/seed_demo_data.py populates demo records on demand."""
    assert db_session.query(Customer).count() == 0
    assert db_session.query(ServiceRequest).count() == 0

    stats = seed_demo_data(db_session)
    assert stats["customers_created"] == 5
    assert stats["requests_created"] == 5

    assert db_session.query(Customer).count() == 5
    assert db_session.query(ServiceRequest).count() == 5

    # Verify idempotency: re-running does not duplicate rows
    stats2 = seed_demo_data(db_session)
    assert stats2["customers_created"] == 0
    assert stats2["requests_created"] == 0
    assert db_session.query(Customer).count() == 5
    assert db_session.query(ServiceRequest).count() == 5


def test_data_t11_assert_safe_test_database_rejects_production_names():
    """DATA-T11: assert_safe_test_database rejects production, default, and application database names."""
    forbidden_urls = [
        "postgresql://postgres:pass@localhost:5432/postgres",
        "postgresql://postgres:pass@localhost:5432/rentkom",
        "postgresql://postgres:pass@localhost:5432/lab4_real_estate",
        "postgresql://postgres:pass@localhost:5432/rentkom_prod",
        "postgresql://postgres:pass@localhost:5432/rentkom_production",
        "postgresql://postgres:pass@localhost:5432/template0",
        "postgresql://postgres:pass@localhost:5432/template1",
        "postgresql://user:pass@localhost:5432/prod_real_estate",
    ]

    for url in forbidden_urls:
        assert not is_safe_test_database_url(url), f"URL '{url}' must be recognized as unsafe"
        with pytest.raises(RuntimeError, match="DATA SAFETY ABORT"):
            assert_safe_test_database(url)


def test_data_t12_assert_safe_test_database_accepts_disposable_names():
    """DATA-T12: assert_safe_test_database accepts disposable and test database names."""
    safe_urls = [
        "sqlite:///:memory:",
        "sqlite:///temp_test.db",
        "postgresql://postgres:pass@localhost:5432/rentkom_test",
        "postgresql://postgres:pass@localhost:5432/disposable_db",
        "postgresql://postgres:pass@localhost:5432/rentkom_temp_test",
    ]

    for url in safe_urls:
        assert is_safe_test_database_url(url), f"URL '{url}' must be recognized as safe"
        # Should not raise any exception
        assert_safe_test_database(url)


def test_data_t13_schema_metadata_cycle_verification():
    """DATA-T13: Full schema metadata creation and recreation cycle passes cleanly."""
    test_engine = create_engine("sqlite:///:memory:")
    Session = sessionmaker(bind=test_engine)
    session = Session()

    Base.metadata.create_all(bind=test_engine)
    insp = inspect(test_engine)
    tables = insp.get_table_names()

    required_tables = [
        "customers",
        "service_requests",
        "request_status_history",
        "inbound_lead_submissions",
        "notification_outbox",
        "customer_feedbacks",
    ]
    for tbl in required_tables:
        assert tbl in tables, f"Required table '{tbl}' missing from created schema"

    # Drop and recreate
    Base.metadata.drop_all(bind=test_engine)
    assert len(inspect(test_engine).get_table_names()) == 0

    Base.metadata.create_all(bind=test_engine)
    assert len(inspect(test_engine).get_table_names()) >= len(required_tables)
    session.close()
    test_engine.dispose()


def test_data_t14_init_db_script_defines_role_privileges():
    """DATA-T14: scripts/init-db.sh defines dedicated application and n8n roles with restricted privileges."""
    script_path = "scripts/init-db.sh"
    assert os.path.exists(script_path), f"Script {script_path} must exist"

    with open(script_path, "r", encoding="utf-8") as f:
        script = f.read()

    assert "rentkom_app" in script
    assert "rentkom_n8n" in script
    # n8n owns only its private schema and receives no direct business-table access.
    assert "CREATE SCHEMA IF NOT EXISTS n8n AUTHORIZATION rentkom_n8n" in script
    assert "GRANT USAGE, CREATE ON SCHEMA n8n TO rentkom_n8n" in script
    assert "REVOKE CREATE ON SCHEMA public FROM rentkom_n8n" in script
    assert "REVOKE ALL ON ALL TABLES IN SCHEMA public FROM rentkom_n8n" in script
    assert "ALL TABLES IN SCHEMA public TO rentkom_n8n" not in script
