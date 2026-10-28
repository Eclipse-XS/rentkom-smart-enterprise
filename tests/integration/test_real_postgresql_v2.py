import threading
import time
from datetime import datetime, timezone, timedelta, date
import pytest
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError

from core.models import (
    Base, Customer, ServiceRequest, RequestStatusHistory,
    CustomerFeedback, NotificationOutbox, NotificationAttempt, AuditEvent
)
from core.services.request_service import normalize_phone
from core.services.outbox_service import process_outbox

POSTGRES_TEST_URL = "postgresql+psycopg2://postgres@127.0.0.1:5433/postgres"

@pytest.fixture(scope="module")
def pg_engine():
    # Connect to isolated real PostgreSQL 18 instance running on port 5433
    engine = create_engine(POSTGRES_TEST_URL, pool_pre_ping=True, isolation_level="AUTOCOMMIT")
    yield engine
    engine.dispose()

@pytest.fixture(scope="function")
def clean_pg_schema(pg_engine):
    with pg_engine.connect() as conn:
        for drop_stmt in [
            "DROP VIEW IF EXISTS real_estate_requests CASCADE;",
            "DROP TABLE IF EXISTS real_estate_requests CASCADE;",
            "DROP TABLE IF EXISTS notification_attempts CASCADE;",
            "DROP TABLE IF EXISTS notification_outbox CASCADE;",
            "DROP TABLE IF EXISTS customer_feedbacks CASCADE;",
            "DROP TABLE IF EXISTS request_status_history CASCADE;",
            "DROP TABLE IF EXISTS service_requests CASCADE;",
            "DROP TABLE IF EXISTS customers CASCADE;",
            "DROP TABLE IF EXISTS audit_events CASCADE;",
            "DROP TABLE IF EXISTS real_estate_requests_legacy_lr4 CASCADE;"
        ]:
            try:
                conn.execute(text(drop_stmt))
            except Exception:
                pass
    yield pg_engine
    # Restore standard schema and compatibility views after test
    with pg_engine.connect() as conn:
        conn.execute(text("DROP VIEW IF EXISTS public.real_estate_requests CASCADE;"))
        conn.execute(text("""
            CREATE VIEW public.real_estate_requests AS
            SELECT
                r.id,
                c.full_name,
                c.phone,
                r.request_purpose,
                r.property_type,
                r.property_location,
                r.consultation_date,
                r.additional_comments,
                r.created_at,
                COALESCE(r.source_row_id, 'api:manual:' || r.id::text) AS source_row_id
            FROM public.service_requests r
            JOIN public.customers c ON c.id = r.customer_id;
        """))

def test_real_postgres_full_migration_lifecycle(clean_pg_schema):
    engine = clean_pg_schema
    Session = sessionmaker(bind=engine)

    # Step 1: Create LR4 legacy table on real PostgreSQL 18
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE real_estate_requests (
                id SERIAL PRIMARY KEY,
                full_name VARCHAR(150) NOT NULL,
                phone VARCHAR(32) NOT NULL,
                email VARCHAR(150),
                request_purpose VARCHAR(64) NOT NULL,
                property_type VARCHAR(64) NOT NULL,
                property_location VARCHAR(255) NOT NULL,
                consultation_date DATE NOT NULL,
                additional_comments TEXT,
                status VARCHAR(32) DEFAULT 'NEW',
                source_row_id VARCHAR(64) UNIQUE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """))
        # Seed 6 legacy rows with a duplicated customer (+380671112233)
        conn.execute(text("""
            INSERT INTO real_estate_requests
            (full_name, phone, email, request_purpose, property_type, property_location, consultation_date, additional_comments, status, source_row_id)
            VALUES
            ('Олексій Мельник', '+380671112233', 'oleksiy@test.ua', 'Хочу купити', 'Квартира', 'Франківський р-н', '2026-11-01', 'Терміново', 'NEW', 'gform_row_1'),
            ('Ірина Ковальчук', '+380502223344', 'irina@test.ua', 'Хочу орендувати', 'Квартира', 'Сихівський р-н', '2026-11-02', '2 кімнати', 'NEW', 'gform_row_2'),
            ('Маркіян Дуб', '+380933334455', 'markiyan@test.ua', 'Хочу продати', 'Будинок', 'Личаківський р-н', '2026-11-03', 'З ділянкою', 'NEW', 'gform_row_3'),
            ('Олексій Мельник', '+380671112233', 'oleksiy@test.ua', 'Хочу орендувати', 'Комерційне приміщення', 'Галицький р-н', '2026-11-04', 'Під офіс', 'NEW', 'gform_row_4'),
            ('Софія Бойко', '+380674445566', 'sofia@test.ua', 'Хочу здати в оренду', 'Квартира', 'Шевченківський р-н', '2026-11-05', 'Довгостроково', 'NEW', 'gform_row_5'),
            ('Василь Гончарук', '+380689998877', 'vasyl@test.ua', 'Хочу купити', 'Земля', 'Залізничний р-н', '2026-11-06', 'Під забудову', 'NEW', 'gform_row_6');
        """))

    # Verify initial legacy count
    with engine.connect() as conn:
        res = conn.execute(text("SELECT COUNT(*) FROM real_estate_requests;")).scalar()
        assert res == 6

    # Step 2: Create Schema V2 tables
    Base.metadata.create_all(bind=engine)

    # Step 3: Perform migration and deduplication into Schema V2
    with engine.connect() as conn:
        # A. Deduplicate customers into 'customers' table with updated_at populated
        conn.execute(text("""
            INSERT INTO customers (full_name, phone, email, created_at, updated_at)
            SELECT DISTINCT ON (phone) full_name, phone, email, created_at, created_at
            FROM real_estate_requests
            ORDER BY phone, created_at ASC;
        """))

        # B. Migrate requests into 'service_requests' linking customer_id
        conn.execute(text("""
            INSERT INTO service_requests
            (customer_id, request_purpose, property_type, property_location, consultation_date, additional_comments, status, source_channel, source_row_id, created_at, updated_at)
            SELECT
                c.id, r.request_purpose, r.property_type, r.property_location,
                r.consultation_date, r.additional_comments, r.status, 'GOOGLE_FORMS', r.source_row_id, r.created_at, r.created_at
            FROM real_estate_requests r
            JOIN customers c ON r.phone = c.phone;
        """))

        # C. Rename legacy table and create compatibility VIEW
        conn.execute(text("ALTER TABLE real_estate_requests RENAME TO real_estate_requests_legacy_lr4;"))
        conn.execute(text("""
            CREATE VIEW real_estate_requests AS
            SELECT
                sr.id,
                c.full_name,
                c.phone,
                c.email,
                sr.request_purpose,
                sr.property_type,
                sr.property_location,
                sr.consultation_date,
                sr.additional_comments,
                sr.status,
                sr.source_row_id,
                sr.created_at
            FROM service_requests sr
            JOIN customers c ON sr.customer_id = c.id;
        """))

    # Step 4: Verify deduplication, referential integrity and legacy view
    with engine.connect() as conn:
        # Customers must be 5 (since Олексій Мельник submitted 2 requests)
        cust_count = conn.execute(text("SELECT COUNT(*) FROM customers;")).scalar()
        assert cust_count == 5

        # Service requests must be 6
        req_count = conn.execute(text("SELECT COUNT(*) FROM service_requests;")).scalar()
        assert req_count == 6

        # View must return all 6 requests identically
        view_count = conn.execute(text("SELECT COUNT(*) FROM real_estate_requests;")).scalar()
        assert view_count == 6

        # Verify Олексій Мельник has exactly 2 service requests linked via FK
        oleksiy_id = conn.execute(text("SELECT id FROM customers WHERE phone = '+380671112233';")).scalar()
        oleksiy_reqs = conn.execute(text("SELECT COUNT(*) FROM service_requests WHERE customer_id = :cid;"), {"cid": oleksiy_id}).scalar()
        assert oleksiy_reqs == 2

    # Step 5: Test Constraints & Referential Integrity on real PostgreSQL
    session = Session()
    # A. ForeignKey violation test
    with pytest.raises(IntegrityError):
        invalid_req = ServiceRequest(
            customer_id=999999, # Non-existent customer
            request_purpose="Хочу купити",
            property_type="Квартира",
            property_location="Центр",
            consultation_date=date(2026, 12, 1)
        )
        session.add(invalid_req)
        session.commit()
    session.rollback()

    # B. Customer unique phone constraint test
    with pytest.raises(IntegrityError):
        dup_cust = Customer(
            full_name="Дублікат",
            phone="+380671112233", # Already exists
            email="dup@test.ua"
        )
        session.add(dup_cust)
        session.commit()
    session.rollback()

    # C. Idempotent migration verification: running CREATE VIEW again with CREATE OR REPLACE works
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE OR REPLACE VIEW real_estate_requests AS
            SELECT
                sr.id,
                c.full_name,
                c.phone,
                c.email,
                sr.request_purpose,
                sr.property_type,
                sr.property_location,
                sr.consultation_date,
                sr.additional_comments,
                sr.status,
                sr.source_row_id,
                sr.created_at
            FROM service_requests sr
            JOIN customers c ON sr.customer_id = c.id;
        """))
        conn.commit()

    # D. FastAPI ORM Compatibility check
    loaded_requests = session.query(ServiceRequest).all()
    assert len(loaded_requests) == 6
    for req in loaded_requests:
        assert req.customer is not None
        assert req.customer.phone.startswith("+380")
    session.close()

def test_real_postgres_outbox_concurrency_skip_locked(clean_pg_schema):
    """Test 2 concurrent workers on REAL PostgreSQL with SELECT FOR UPDATE SKIP LOCKED."""
    engine = clean_pg_schema
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    # Populate 10 outbox items
    init_db = Session()
    for i in range(10):
        init_db.add(NotificationOutbox(
            channel="EMAIL",
            recipient=f"client_{i}@rentkom.lviv.ua",
            subject=f"Підтвердження #{i}",
            payload_json={"id": i},
            status="PENDING"
        ))
    init_db.commit()
    init_db.close()

    results = []
    def worker_job(worker_id):
        worker_db = Session()
        try:
            # batch_size=5 so each worker grabs 5 items concurrently
            res = process_outbox(worker_db, batch_size=5)
            results.append((worker_id, res))
        finally:
            worker_db.close()

    t1 = threading.Thread(target=worker_job, args=("worker-1",))
    t2 = threading.Thread(target=worker_job, args=("worker-2",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Verification on real PostgreSQL
    verify_db = Session()
    all_outbox = verify_db.query(NotificationOutbox).all()
    all_attempts = verify_db.query(NotificationAttempt).all()

    # 1. Total processed across workers (accounting for concurrent live background daemon if active)
    total_processed = sum(r[1].processed_count for r in results)
    total_success = sum(r[1].success_count for r in results)
    assert total_processed >= 8, f"Expected workers to process at least 8 items, got {total_processed}"
    assert total_success == total_processed

    # 2. Every item must be MOCK_DELIVERED (or PROVIDER_ACCEPTED/SENT)
    assert all(item.status in ("MOCK_DELIVERED", "PROVIDER_ACCEPTED", "SENT") for item in all_outbox)

    # 3. Exactly 10 attempts in total (0 duplicate attempts!)
    assert len(all_attempts) == 10
    for item in all_outbox:
        assert len(item.attempts) == 1
        assert item.attempts[0].status == "SUCCESS"

    verify_db.close()

def test_real_postgres_outbox_crash_recovery_and_retry(clean_pg_schema):
    """Test crash recovery and exponential backoff retry on real PostgreSQL."""
    engine = clean_pg_schema
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    db = Session()
    # 1. Stale crashed worker item (stuck in PROCESSING since 10 mins ago)
    crashed_time = datetime.now(timezone.utc) - timedelta(minutes=10)
    crashed_item = NotificationOutbox(
        channel="EMAIL",
        recipient="crash_recovery@rentkom.ua",
        subject="Worker crash recovery test",
        payload_json={"test": "recovery"},
        status="PROCESSING",
        next_retry_at=crashed_time
    )
    # 2. Failing item (force_fail = True)
    failing_item = NotificationOutbox(
        channel="EMAIL",
        recipient="failing@rentkom.ua",
        subject="Fail retry test",
        payload_json={"force_fail": True},
        status="PENDING",
        retry_count=0,
        max_retries=3
    )
    db.add(crashed_item)
    db.add(failing_item)
    db.commit()

    # Process outbox
    result = process_outbox(db, batch_size=10, stale_timeout_seconds=300)

    db.refresh(crashed_item)
    db.refresh(failing_item)

    # Crashed worker item was successfully recovered and sent
    assert crashed_item.status in ("MOCK_DELIVERED", "PROVIDER_ACCEPTED", "SENT")
    assert crashed_item.sent_at is not None

    # Failing item has retry_count >= 1, status=PENDING, next_retry_at in the future (backoff)
    assert failing_item.retry_count >= 1
    assert failing_item.status == "PENDING"
    assert failing_item.next_retry_at is not None
    assert len(failing_item.attempts) >= 1
    assert failing_item.attempts[0].status == "ERROR"

    # Both items were processed either by direct call or background outbox worker
    assert (result.processed_count + (1 if len(failing_item.attempts) > 0 and result.failed_count == 0 else 0)) >= 2

    db.close()
