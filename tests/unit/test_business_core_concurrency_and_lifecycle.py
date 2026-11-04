import os
import tempfile
import threading
import time
from datetime import date
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from core.schemas import ServiceRequestCreate, ServiceRequestStatusUpdate
from core.services.request_service import (
    normalize_phone, create_service_request, update_request_status, VALID_STATUS_TRANSITIONS
)
from core.models import Base, Customer, ServiceRequest, NotificationOutbox, AuditEvent


def test_normalize_phone_canonical_formats():
    """Verify phone normalization across standard Ukrainian and international formats."""
    assert normalize_phone("0689998877") == "+380689998877"
    assert normalize_phone("+380689998877") == "+380689998877"
    assert normalize_phone("380689998877") == "+380689998877"
    assert normalize_phone("80689998877") == "+380689998877"
    assert normalize_phone("+38 (068) 999-88-77") == "+380689998877"
    assert normalize_phone("  +380-50-123-45-67  ") == "+380501234567"


def test_normalize_phone_invalid_lengths():
    """Verify that numbers with invalid lengths are rejected."""
    with pytest.raises(ValueError):
        normalize_phone("12345")
    with pytest.raises(ValueError):
        normalize_phone("+38012345678901234567")


def test_request_state_machine_valid_lifecycle(db_session):
    """Verify valid progression: NEW -> IN_REVIEW -> SCHEDULED -> COMPLETED."""
    req_in = ServiceRequestCreate(
        full_name="Тест Переходів",
        phone="+380981112233",
        request_purpose="Хочу купити",
        property_type="Квартира",
        property_location="вул. Стрийська",
        consultation_date=date(2026, 12, 1)
    )
    req = create_service_request(db_session, req_in)
    assert req.status == "NEW"

    # 1. NEW -> IN_REVIEW
    req = update_request_status(
        db_session, req.id,
        ServiceRequestStatusUpdate(new_status="IN_REVIEW", comment="Взято в роботу"),
        actor="operator_1"
    )
    assert req.status == "IN_REVIEW"

    # 2. IN_REVIEW -> SCHEDULED
    req = update_request_status(
        db_session, req.id,
        ServiceRequestStatusUpdate(new_status="SCHEDULED", comment="Призначено зустріч"),
        actor="operator_1"
    )
    assert req.status == "SCHEDULED"

    # 3. SCHEDULED -> COMPLETED
    req = update_request_status(
        db_session, req.id,
        ServiceRequestStatusUpdate(new_status="COMPLETED", comment="Консультацію проведено"),
        actor="operator_1"
    )
    assert req.status == "COMPLETED"


def test_request_state_machine_rejects_illegal_transitions(db_session):
    """Verify that illegal transitions (e.g. COMPLETED -> NEW, NEW -> COMPLETED) are rejected without mutating DB."""
    req_in = ServiceRequestCreate(
        full_name="Тест Відхилень",
        phone="+380982223344",
        request_purpose="Хочу орендувати",
        property_type="Будинок",
        property_location="вул. Зелена",
        consultation_date=date(2026, 12, 2)
    )
    req = create_service_request(db_session, req_in)
    assert req.status == "NEW"

    # Illegal jump: NEW -> COMPLETED (cannot skip review & scheduling)
    with pytest.raises(ValueError) as exc:
        update_request_status(
            db_session, req.id,
            ServiceRequestStatusUpdate(new_status="COMPLETED"),
            actor="admin"
        )
    assert "Invalid status transition" in str(exc.value)
    db_session.refresh(req)
    assert req.status == "NEW"

    # Illegal jump: NEW -> SCHEDULED
    with pytest.raises(ValueError):
        update_request_status(
            db_session, req.id,
            ServiceRequestStatusUpdate(new_status="SCHEDULED"),
            actor="admin"
        )
    db_session.refresh(req)
    assert req.status == "NEW"

    # Cancel the request (NEW -> CANCELLED is allowed)
    req = update_request_status(
        db_session, req.id,
        ServiceRequestStatusUpdate(new_status="CANCELLED", comment="Клієнт передумав"),
        actor="admin"
    )
    assert req.status == "CANCELLED"

    # CANCELLED is terminal: CANCELLED -> SCHEDULED or CANCELLED -> IN_REVIEW must be rejected
    for disallowed_target in ("NEW", "IN_REVIEW", "SCHEDULED", "COMPLETED"):
        with pytest.raises(ValueError) as exc2:
            update_request_status(
                db_session, req.id,
                ServiceRequestStatusUpdate(new_status=disallowed_target),
                actor="admin"
            )
        assert "terminal state" in str(exc2.value)
        db_session.refresh(req)
        assert req.status == "CANCELLED"


def test_repeated_source_row_id_idempotency_and_conflict(db_session, client):
    """
    Verify:
      1. Repeated identical source_row_id returns the existing request without creating duplicates.
      2. Replay of the same source_row_id with a conflicting payload raises ValueError (HTTP 409 Conflict).
    """
    payload = {
        "full_name": "Ідемпотентний Клієнт",
        "phone": "0671234567",
        "email": "idem@rentkom.lviv.ua",
        "request_purpose": "Хочу купити",
        "property_type": "Квартира",
        "property_location": "м. Львів, вул. Дорошенка",
        "consultation_date": "2026-12-10",
        "source_channel": "GOOGLE_FORMS",
        "source_row_id": "gform_row_idem_901",
    }
    r1 = client.post("/api/v1/requests/", json=payload)
    assert r1.status_code == 202
    sub_id_1 = r1.json()["submission_id"]

    # Same source_row_id with normalized phone variant and identical payload -> idempotent return
    payload_same = dict(payload, phone="+38 (067) 123-45-67")
    r2 = client.post("/api/v1/requests/", json=payload_same)
    assert r2.status_code == 202
    assert r2.json()["submission_id"] == sub_id_1

    # Conflicting payload with same source_row_id -> HTTP 409 Conflict
    payload_conflict = dict(payload, property_type="Будинок")
    r_conflict = client.post("/api/v1/requests/", json=payload_conflict)
    assert r_conflict.status_code == 409
    assert "Idempotency conflict" in r_conflict.json()["detail"]


def test_transaction_rollback_on_failure_leaves_zero_partial_records(db_session, monkeypatch):
    """
    Verify that if an error occurs before final commit during request creation,
    no orphan customer, request, or outbox records remain persisted.
    """
    orig_commit = db_session.commit

    def FailingCommit():
        db_session.rollback()
        raise RuntimeError("Simulated database failure before commit")

    monkeypatch.setattr(db_session, "commit", FailingCommit)

    req_in = ServiceRequestCreate(
        full_name="Транзакційний Відкат",
        phone="+380637778899",
        email="rollback@rentkom.lviv.ua",
        request_purpose="Хочу купити",
        property_type="Квартира",
        property_location="м. Львів, Центр",
        consultation_date=date(2026, 12, 12),
    )
    with pytest.raises(RuntimeError, match="Simulated database failure"):
        create_service_request(db_session, req_in)

    monkeypatch.setattr(db_session, "commit", orig_commit)
    assert db_session.query(Customer).filter(Customer.phone == "+380637778899").count() == 0
    assert db_session.query(NotificationOutbox).filter(NotificationOutbox.recipient == "rollback@rentkom.lviv.ua").count() == 0


@pytest.mark.parametrize("concurrency_level", [2, 10])
def test_concurrent_customer_deduplication(concurrency_level):
    """
    Concurrency test: Simulates 2 and 10 simultaneous requests from the same customer
    using different raw phone formats that normalize to the same canonical E.164 phone.
    Verifies that race conditions in customer creation resolve gracefully without IntegrityError.
    """
    db_file = os.path.join(tempfile.gettempdir(), f"test_cust_dedup_{concurrency_level}_{int(time.time()*1000)}.db")
    if os.path.exists(db_file):
        try:
            os.remove(db_file)
        except OSError:
            pass

    engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"timeout": 30},
        echo=False
    )
    with engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL;"))
        conn.commit()

    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    raw_variants = [
        "0998887766",
        "+380998887766",
        "380998887766",
        "80998887766",
        "+38 (099) 888-77-66",
    ]
    canonical_phone = "+380998887766"
    errors = []
    created_requests = []
    barrier = threading.Barrier(concurrency_level)

    def submit_lead(thread_id: int):
        session = Session()
        try:
            barrier.wait(timeout=5)
            req_in = ServiceRequestCreate(
                full_name=f"Паралельний Клієнт {thread_id}",
                phone=raw_variants[thread_id % len(raw_variants)],
                email=f"client{thread_id}@example.com",
                request_purpose="Хочу купити",
                property_type="Квартира",
                property_location=f"Локація {thread_id}",
                consultation_date=date(2026, 12, 10),
                source_channel="REST_API"
            )
            res = create_service_request(session, req_in)
            created_requests.append(res.id)
        except Exception as e:
            errors.append(e)
        finally:
            session.close()

    threads = [threading.Thread(target=submit_lead, args=(i,)) for i in range(concurrency_level)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0, f"Concurrent submissions produced errors: {errors}"
    assert len(created_requests) == concurrency_level

    # Verify that exactly 1 Customer was created for this phone
    verify_session = Session()
    customers = verify_session.query(Customer).filter(Customer.phone == canonical_phone).all()
    assert len(customers) == 1
    assert customers[0].phone == canonical_phone

    # All service requests must link to the same customer ID
    requests = verify_session.query(ServiceRequest).filter(ServiceRequest.customer_id == customers[0].id).all()
    assert len(requests) == concurrency_level
    verify_session.close()
    engine.dispose()
    try:
        os.remove(db_file)
    except OSError:
        pass
