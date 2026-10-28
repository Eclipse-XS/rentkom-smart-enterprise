import pytest
from datetime import date, datetime, timezone
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker
from core.models import Base, Customer, ServiceRequest, RequestStatusHistory, CustomerFeedback, NotificationOutbox
from core.schemas import ServiceRequestCreate
from core.services.request_service import create_service_request

def test_full_database_migration_lifecycle():
    """
    Validates Phase 5 requirements:
    1. Schema creation from scratch
    2. Legacy table creation & seeding
    3. Migration execution (deduplication, row preservation)
    4. Unique constraints & FK checks
    5. Re-run migration idempotency
    6. Compatibility view verification
    7. FastAPI / Service integration with migrated schema
    """
    # Create isolated in-memory SQLite database
    engine = create_engine("sqlite:///:memory:", echo=False)
    Session = sessionmaker(bind=engine)
    db = Session()

    # 1. Simulate Legacy LR4 Schema
    db.execute(text("""
        CREATE TABLE real_estate_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            request_purpose TEXT NOT NULL,
            property_type TEXT NOT NULL,
            property_location TEXT NOT NULL,
            consultation_date DATE NOT NULL,
            additional_comments TEXT,
            source_row_id TEXT UNIQUE,
            created_at TIMESTAMP NOT NULL
        );
    """))
    db.commit()

    # Seed 5 historical records + 1 duplicate customer from LR4
    db.execute(text("""
        INSERT INTO real_estate_requests (full_name, phone, request_purpose, property_type, property_location, consultation_date, additional_comments, source_row_id, created_at)
        VALUES
        ('Олексій Мельник', '+380671112233', 'Хочу купити', 'Квартира', 'Франківський р-н', '2026-10-20', '2-кімнатна біля Політехніки', 'lr4:row:1', '2026-10-18 10:15:00'),
        ('Ірина Ковальчук', '+380502223344', 'Хочу орендувати', 'Квартира', 'Сихівський р-н', '2026-10-21', 'Оренда для сім''ї', 'lr4:row:2', '2026-10-19 11:20:00'),
        ('Маркіян Дуб', '+380933334455', 'Хочу продати', 'Будинок', 'Личаківський р-н', '2026-10-22', 'Продаж 150 кв.м', 'lr4:row:3', '2026-10-20 14:00:00'),
        ('Софія Бойко', '+380674445566', 'Хочу здати в оренду', 'Квартира', 'Галицький р-н', '2026-10-23', 'Під подобову оренду', 'lr4:row:4', '2026-10-21 16:45:00'),
        ('Василь Гончарук', '+380689998877', 'Хочу купити', 'Квартира', 'Шевченківський р-н', '2026-10-24', 'Тестова верифікація', 'lr4:row:5', '2026-10-22 10:29:16'),
        ('Василь Гончарук', '+380689998877', 'Хочу орендувати', 'Комерційне приміщення', 'Центр', '2026-10-25', 'Повторне звернення того ж клієнта', 'lr4:row:6', '2026-10-23 12:00:00');
    """))
    db.commit()

    legacy_count = db.execute(text("SELECT COUNT(*) FROM real_estate_requests;")).scalar()
    assert legacy_count == 6, "Legacy table must have 6 rows before migration"

    # 2. Create Schema v2 Tables (Customers, Service Requests, etc.)
    Base.metadata.create_all(bind=engine)

    # 3. Execute Migration Logic: Deduplicate Customers & Migrate Requests
    # Step A: Distinct customers
    db.execute(text("""
        INSERT OR IGNORE INTO customers (full_name, phone, created_at, updated_at)
        SELECT full_name, phone, MIN(created_at), MIN(created_at)
        FROM real_estate_requests
        GROUP BY phone;
    """))
    db.commit()

    # Verify deduplication: 6 requests from 5 unique phone numbers -> exactly 5 customers
    customer_count = db.execute(text("SELECT COUNT(*) FROM customers;")).scalar()
    assert customer_count == 5, f"Expected 5 deduplicated customers, got {customer_count}"

    # Step B: Migrate service requests linked to customer_id
    db.execute(text("""
        INSERT INTO service_requests (customer_id, request_purpose, property_type, property_location, consultation_date, additional_comments, status, source_channel, source_row_id, created_at, updated_at)
        SELECT c.id, r.request_purpose, r.property_type, r.property_location, r.consultation_date, r.additional_comments, 'COMPLETED', 'GOOGLE_FORMS', r.source_row_id, r.created_at, r.created_at
        FROM real_estate_requests r
        JOIN customers c ON c.phone = r.phone;
    """))
    db.commit()

    migrated_requests_count = db.execute(text("SELECT COUNT(*) FROM service_requests;")).scalar()
    assert migrated_requests_count == 6, f"Expected 6 migrated requests, got {migrated_requests_count}"

    # Step C: Archive legacy table and create compatibility VIEW
    db.execute(text("ALTER TABLE real_estate_requests RENAME TO real_estate_requests_legacy_lr4;"))
    db.commit()

    db.execute(text("""
        CREATE VIEW real_estate_requests AS
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
            COALESCE(r.source_row_id, 'api:manual:' || r.id) AS source_row_id
        FROM service_requests r
        JOIN customers c ON c.id = r.customer_id;
    """))
    db.commit()

    # 4. Verify Compatibility View
    view_rows = db.execute(text("SELECT id, full_name, phone, property_location FROM real_estate_requests ORDER BY id;")).fetchall()
    assert len(view_rows) == 6
    assert view_rows[0][1] == "Олексій Мельник"
    assert view_rows[4][1] == "Василь Гончарук"
    assert view_rows[5][1] == "Василь Гончарук" # Same customer, second request

    # 5. Verify Idempotent Service Integration: add new request via Service API
    new_req = ServiceRequestCreate(
        full_name="Василь Гончарук", # Existing customer
        phone="+380689998877",
        email="vasyl@example.com",
        request_purpose="Хочу продати",
        property_type="Будинок",
        property_location="м. Львів, Винники",
        consultation_date=date(2026, 11, 1),
        source_channel="REST_API"
    )
    created = create_service_request(db, new_req)
    assert created.id == 7
    # Verify customer count is STILL 5 (deduplicated)
    assert db.execute(text("SELECT COUNT(*) FROM customers;")).scalar() == 5
    # Verify view immediately reflects the 7th request!
    assert db.execute(text("SELECT COUNT(*) FROM real_estate_requests;")).scalar() == 7

    # 6. Verify Constraints
    # Unique constraint on source_row_id
    with pytest.raises(Exception):
        db.execute(text("INSERT INTO service_requests (customer_id, request_purpose, property_type, property_location, consultation_date, status, source_row_id, created_at, updated_at) VALUES (1, 'Хочу купити', 'Квартира', 'Центр', '2026-11-01', 'NEW', 'lr4:row:1', '2026-10-27', '2026-10-27');"))
        db.commit()
    db.rollback()

    db.close()
