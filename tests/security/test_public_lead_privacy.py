import os
import tempfile
import time
import threading
from datetime import date
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from core.main import app
from core.database import Base, get_db
from core.models import Customer, ServiceRequest, NotificationOutbox, AuditEvent, InboundLeadSubmission
from core.config import settings


def test_sec_t01_anonymous_request_pii_elimination(client, db_session):
    """SEC-T01: Existing customer has private email, name, phone and request history.
    An anonymous caller submits that phone. Public response contains none of the private data."""
    secret_customer = Customer(
        full_name="Іван Франко (Секретний Клієнт)",
        phone="+380671234567",
        email="ivan.franko.private@rentkom.lviv.ua",
        telegram_chat_id=999888777
    )
    db_session.add(secret_customer)
    db_session.commit()
    db_session.refresh(secret_customer)

    prior_req = ServiceRequest(
        customer_id=secret_customer.id,
        request_purpose="Хочу купити",
        property_type="Будинок",
        property_location="вул. Винниченка, 10",
        consultation_date=date(2026, 11, 10),
        status="COMPLETED",
        source_channel="REST_API"
    )
    db_session.add(prior_req)
    db_session.commit()
    db_session.close()

    payload = {
        "full_name": "Анонімний Відвідувач",
        "phone": "+380671234567",
        "email": "attacker@evil.com",
        "request_purpose": "Хочу орендувати",
        "property_type": "Квартира",
        "property_location": "Сихівський р-н",
        "consultation_date": "2026-11-25"
    }
    resp = client.post("/api/v1/requests/", json=payload)
    assert resp.status_code == 202, f"Expected 202 Accepted, got {resp.status_code}: {resp.text}"
    data = resp.json()

    assert "submission_id" in data
    assert data["status"] == "RECEIVED"
    assert data["message"] == "Request accepted for processing"

    resp_text = resp.text
    assert "Іван Франко" not in resp_text
    assert "ivan.franko.private@rentkom.lviv.ua" not in resp_text
    assert "999888777" not in resp_text
    assert "customer_id" not in data
    assert "customer" not in data
    assert "id" not in data or data.get("id") is None


def test_sec_t02_contact_hijack_prevention_email(client, db_session):
    """SEC-T02: Anonymous caller submits another customer's phone with an attacker-controlled email.
    Existing Customer.email remains unchanged."""
    existing = Customer(
        full_name="Віра Коваль",
        phone="+380501112233",
        email="vira.koval@verified.ua",
        telegram_chat_id=111222333
    )
    db_session.add(existing)
    db_session.commit()
    db_session.close()

    payload = {
        "full_name": "Зловмисник",
        "phone": "+380501112233",
        "email": "attacker@evil-domain.org",
        "request_purpose": "Хочу купити",
        "property_type": "Квартира",
        "property_location": "Личаківський р-н",
        "consultation_date": "2026-12-01"
    }
    resp = client.post("/api/v1/requests/", json=payload)
    assert resp.status_code == 202

    db_session.expire_all()
    cust = db_session.query(Customer).filter(Customer.phone == "+380501112233").first()
    assert cust.email == "vira.koval@verified.ua"
    assert cust.email != "attacker@evil-domain.org"


def test_sec_t03_contact_hijack_prevention_telegram(client, db_session):
    """SEC-T03: Anonymous caller submits another customer's phone with an attacker-controlled Telegram chat ID.
    Existing Customer.telegram_chat_id remains unchanged."""
    existing = Customer(
        full_name="Андрій Садовий",
        phone="+380502223344",
        email="andriy@city.lviv.ua",
        telegram_chat_id=444555666
    )
    db_session.add(existing)
    db_session.commit()
    db_session.close()

    payload = {
        "full_name": "Зловмисник Telegram",
        "phone": "+380502223344",
        "telegram_chat_id": 999999999,
        "request_purpose": "Хочу купити",
        "property_type": "Будинок",
        "property_location": "вул. Франка",
        "consultation_date": "2026-12-05"
    }
    resp = client.post("/api/v1/requests/", json=payload)
    assert resp.status_code == 202

    db_session.expire_all()
    cust = db_session.query(Customer).filter(Customer.phone == "+380502223344").first()
    assert cust.telegram_chat_id == 444555666
    assert cust.telegram_chat_id != 999999999


def test_sec_t04_no_sensitive_notification_to_attacker_destination(client, db_session):
    """SEC-T04: No sensitive notification is queued to the attacker-controlled channel."""
    existing = Customer(
        full_name="Таємний Клієнт",
        phone="+380931234567",
        email="vip@rentkom.lviv.ua",
        telegram_chat_id=123123
    )
    db_session.add(existing)
    db_session.commit()
    db_session.close()

    attacker_email = "trap@attacker.xyz"
    attacker_tg = 7777777

    payload = {
        "full_name": "Фейковий лід",
        "phone": "+380931234567",
        "email": attacker_email,
        "telegram_chat_id": attacker_tg,
        "request_purpose": "Хочу орендувати",
        "property_type": "Квартира",
        "property_location": "Франківський р-н",
        "consultation_date": "2026-11-28"
    }
    resp = client.post("/api/v1/requests/", json=payload)
    assert resp.status_code == 202

    outbox_entries = db_session.query(NotificationOutbox).all()
    for item in outbox_entries:
        assert item.recipient != attacker_email, f"Found outbox notification sent to unverified attacker email: {item.recipient}"
        assert item.recipient != str(attacker_tg), f"Found outbox notification sent to unverified attacker Telegram: {item.recipient}"
        if item.recipient == settings.ADMIN_EMAIL:
            assert "Фейковий лід" in item.subject or "Новий вхідний лід" in item.subject


def test_sec_t05_valid_lead_persisted_and_retrieved_by_admin(client, db_session):
    """SEC-T05: A valid new anonymous lead is persisted and can be retrieved by an authorized administrator."""
    payload = {
        "full_name": "Ольга Добровольська",
        "phone": "+380987654321",
        "email": "olga.d@example.com",
        "request_purpose": "Хочу купити",
        "property_type": "Квартира",
        "property_location": "вул. Городоцька, 50",
        "consultation_date": "2026-12-10",
        "additional_comments": "Шукаю біля вокзалу",
        "source_channel": "REST_API",
        "source_row_id": "lead_test_row_5"
    }
    resp = client.post("/api/v1/requests/", json=payload)
    assert resp.status_code == 202
    sub_id = resp.json()["submission_id"]

    db_session.close()

    # Authorized admin can retrieve the lead
    admin_headers = {"X-Admin-Token": settings.ADMIN_API_KEY}
    get_resp = client.get(f"/api/v1/leads/{sub_id}", headers=admin_headers)
    assert get_resp.status_code == 200
    lead_data = get_resp.json()
    assert lead_data["submission_id"] == sub_id
    assert lead_data["full_name"] == "Ольга Добровольська"
    assert lead_data["status"] == "UNVERIFIED"

    # Authorized admin can list leads
    list_resp = client.get("/api/v1/leads/?status=UNVERIFIED", headers=admin_headers)
    assert list_resp.status_code == 200
    assert any(item["submission_id"] == sub_id for item in list_resp.json())


def test_sec_t06_unauthorized_user_cannot_promote_lead(client, db_session):
    """SEC-T06: An unauthorized user cannot promote a lead."""
    lead = InboundLeadSubmission(
        submission_id="test-sub-uuid-06",
        intake_channel="REST_API",
        payload_hash="testhash06",
        full_name="Тестовий Лід 06",
        phone="+380981110066",
        normalized_phone="+380981110066",
        request_purpose="Хочу купити",
        property_type="Квартира",
        property_location="вул. Сахарова",
        consultation_date=date(2026, 12, 12),
        status="UNVERIFIED"
    )
    db_session.add(lead)
    db_session.commit()
    sub_id = lead.submission_id
    db_session.close()

    # 1. Anonymous promotion attempt -> 401
    anon_resp = client.post(f"/api/v1/leads/{sub_id}/promote")
    assert anon_resp.status_code == 401, f"Expected 401, got {anon_resp.status_code}"

    # 2. Forged/bad token promotion attempt -> 403
    bad_resp = client.post(
        f"/api/v1/leads/{sub_id}/promote",
        headers={"X-Admin-Token": "invalid-forged-token"}
    )
    assert bad_resp.status_code == 403, f"Expected 403, got {bad_resp.status_code}"

    check_lead = db_session.query(InboundLeadSubmission).filter(
        InboundLeadSubmission.submission_id == sub_id
    ).first()
    assert check_lead.status == "UNVERIFIED"


def test_sec_t07_authorized_promotion_creates_request_and_audit(client, db_session):
    """SEC-T07: Authorized promotion creates the correct business request and audit entry without duplicate effects."""
    lead = InboundLeadSubmission(
        submission_id="test-sub-uuid-07",
        intake_channel="REST_API",
        payload_hash="testhash07",
        full_name="Марія Промоція",
        phone="+380677778899",
        normalized_phone="+380677778899",
        email="maria.promo@example.com",
        request_purpose="Хочу купити",
        property_type="Квартира",
        property_location="вул. Стрийська, 100",
        consultation_date=date(2026, 12, 20),
        additional_comments="Терміновий розгляд",
        status="UNVERIFIED"
    )
    db_session.add(lead)
    db_session.commit()
    sub_id = lead.submission_id
    db_session.close()

    admin_headers = {"X-Admin-Token": settings.ADMIN_API_KEY}

    # First promotion
    resp = client.post(
        f"/api/v1/leads/{sub_id}/promote",
        headers=admin_headers,
        json={"review_notes": "Телефон верифіковано оператором"}
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    req_data = resp.json()
    assert req_data["id"] is not None
    assert req_data["status"] == "NEW"
    assert req_data["request_purpose"] == "Хочу купити"
    created_req_id = req_data["id"]

    # Verify lead state is now PROMOTED
    db_session.expire_all()
    updated_lead = db_session.query(InboundLeadSubmission).filter(
        InboundLeadSubmission.submission_id == sub_id
    ).first()
    assert updated_lead.status == "PROMOTED"
    assert updated_lead.promoted_request_id == created_req_id
    assert updated_lead.reviewed_by == "admin_user"
    assert "Телефон верифіковано" in updated_lead.review_notes

    # Verify audit event was generated
    audit = db_session.query(AuditEvent).filter(
        AuditEvent.event_type == "LEAD_PROMOTED",
        AuditEvent.entity_type == "inbound_lead_submissions"
    ).first()
    assert audit is not None
    assert audit.details_json.get("promoted_request_id") == created_req_id

    db_session.close()

    # Second promotion (idempotent replay) must NOT duplicate service requests
    resp2 = client.post(
        f"/api/v1/leads/{sub_id}/promote",
        headers=admin_headers,
        json={"review_notes": "Повторна спроба"}
    )
    assert resp2.status_code == 200
    assert resp2.json()["id"] == created_req_id

    total_requests = db_session.query(ServiceRequest).filter(
        ServiceRequest.property_location == "вул. Стрийська, 100"
    ).count()
    assert total_requests == 1, "Duplicate promotion must not create duplicate ServiceRequest records."


def test_sec_t08_concurrent_anonymous_requests_idempotency():
    """SEC-T08: Ten concurrent anonymous requests cannot bypass ownership boundaries
    or create duplicate logical events for the same idempotency key."""
    fd, db_path = tempfile.mkstemp(suffix="_sec_t08.db")
    os.close(fd)

    wal_engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"timeout": 30.0},
        echo=False
    )
    with wal_engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL;"))
        conn.commit()

    Base.metadata.create_all(bind=wal_engine)
    WalSession = sessionmaker(bind=wal_engine, expire_on_commit=False)

    def override_wal_db():
        s = WalSession()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_wal_db

    shared_row_id = "concurrent_sec_test_row_88"
    payload = {
        "full_name": "Паралельний Тест",
        "phone": "+380671119988",
        "email": "concurrent@rentkom.ua",
        "request_purpose": "Хочу купити",
        "property_type": "Квартира",
        "property_location": "вул. Шевченка",
        "consultation_date": "2026-11-30",
        "source_channel": "REST_API",
        "source_row_id": shared_row_id
    }

    results = []
    barrier = threading.Barrier(10)

    def worker():
        tc = TestClient(app)
        barrier.wait()
        r = tc.post("/api/v1/requests/", json=payload)
        results.append((r.status_code, r.json()))

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    app.dependency_overrides.clear()

    # Verify all responses succeeded with 202 Accepted
    for status_code, body in results:
        assert status_code == 202
        assert body["status"] == "RECEIVED"

    # All must reference the identical submission ID
    submission_ids = {body["submission_id"] for _, body in results}
    assert len(submission_ids) == 1, f"Expected 1 unique submission_id, got {len(submission_ids)}: {submission_ids}"

    # Exactly 1 record in database
    verify_session = WalSession()
    count = verify_session.query(InboundLeadSubmission).filter(
        InboundLeadSubmission.external_event_id == shared_row_id
    ).count()
    assert count == 1, f"Expected exactly 1 InboundLeadSubmission row, got {count}"
    verify_session.close()
    wal_engine.dispose()
    try:
        os.remove(db_path)
    except OSError:
        pass


def test_sec_t09_administrative_request_apis_work_with_authorization(client, db_session):
    """SEC-T09: Existing administrative request APIs continue to work with valid authorization."""
    admin_headers = {"X-Admin-Token": settings.ADMIN_API_KEY}

    # 1. Admin creates request directly
    direct_payload = {
        "full_name": "Адміністративний Клієнт",
        "phone": "+380509990011",
        "email": "admin.direct@rentkom.ua",
        "request_purpose": "Хочу продати",
        "property_type": "Будинок",
        "property_location": "Личаківський р-н",
        "consultation_date": "2026-12-15"
    }
    create_resp = client.post("/api/v1/requests/", json=direct_payload, headers=admin_headers)
    assert create_resp.status_code == 201, f"Expected 201 Created for admin, got {create_resp.status_code}: {create_resp.text}"
    req_id = create_resp.json()["id"]

    db_session.close()

    # 2. Admin retrieves single request
    get_resp = client.get(f"/api/v1/requests/{req_id}", headers=admin_headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["property_type"] == "Будинок"
    assert get_resp.json()["customer"]["phone"] == "+380509990011"

    # 3. Admin lists requests
    list_resp = client.get("/api/v1/requests/", headers=admin_headers)
    assert list_resp.status_code == 200
    assert any(r["id"] == req_id for r in list_resp.json())

    # 4. Admin updates status
    patch_resp = client.patch(
        f"/api/v1/requests/{req_id}/status",
        headers=admin_headers,
        json={"new_status": "IN_REVIEW", "comment": "Оператор перевірив дані"}
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["status"] == "IN_REVIEW"


def test_sec_t10_public_errors_do_not_disclose_customer_data(client, db_session):
    """SEC-T10: Public errors do not disclose existing customer data."""
    existing = Customer(
        full_name="Секретна Особа",
        phone="+380678887766",
        email="confidential@rentkom.ua"
    )
    db_session.add(existing)
    db_session.commit()

    lead1 = InboundLeadSubmission(
        submission_id="sub-conf-1",
        intake_channel="REST_API",
        external_event_id="conflict_row_id_99",
        payload_hash="hash1",
        full_name="Перший Лід",
        phone="+380678887766",
        normalized_phone="+380678887766",
        request_purpose="Хочу купити",
        property_type="Квартира",
        property_location="вул. Наукова",
        consultation_date=date(2026, 12, 1),
        status="UNVERIFIED"
    )
    db_session.add(lead1)
    db_session.commit()
    db_session.close()

    # Conflicting replay
    conflict_payload = {
        "full_name": "Змінений Лід",
        "phone": "+380678887766",
        "email": "another@evil.com",
        "request_purpose": "Хочу орендувати",  # changed
        "property_type": "Будинок",           # changed
        "property_location": "вул. Зелена",   # changed
        "consultation_date": "2026-12-25",
        "source_channel": "REST_API",
        "source_row_id": "conflict_row_id_99"
    }
    resp = client.post("/api/v1/requests/", json=conflict_payload)
    assert resp.status_code == 409
    err_text = resp.text

    assert "Секретна Особа" not in err_text
    assert "confidential@rentkom.ua" not in err_text
    assert "Перший Лід" not in err_text
