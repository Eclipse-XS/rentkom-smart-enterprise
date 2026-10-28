import pytest
from sqlalchemy import create_engine, text

POSTGRES_URL = "postgresql+psycopg2://postgres@127.0.0.1:5433/postgres"

SQL_LEGACY_FUNCTION = """
DROP FUNCTION IF EXISTS public.insert_real_estate_request_legacy;

CREATE OR REPLACE FUNCTION public.insert_real_estate_request_legacy(
    p_full_name TEXT,
    p_phone TEXT,
    p_request_purpose TEXT,
    p_property_type TEXT,
    p_property_location TEXT,
    p_consultation_date DATE,
    p_additional_comments TEXT,
    p_source_row_id TEXT
)
RETURNS TABLE (
    id BIGINT,
    res_full_name TEXT,
    res_phone TEXT,
    res_purpose TEXT,
    res_type TEXT,
    res_location TEXT,
    res_date DATE,
    res_comments TEXT,
    res_created_at TIMESTAMPTZ,
    res_source_row_id TEXT
) AS $$
DECLARE
    v_customer_id BIGINT;
    v_request_id BIGINT;
    v_created_at TIMESTAMPTZ;
BEGIN
    -- 1. Idempotency guard: return empty result if source_row_id already processed
    SELECT r.id, r.created_at INTO v_request_id, v_created_at
    FROM public.service_requests r
    WHERE r.source_row_id = p_source_row_id;

    IF FOUND THEN
        RETURN;
    END IF;

    -- 2. Customer deduplication by phone
    INSERT INTO public.customers (full_name, phone, created_at, updated_at)
    VALUES (p_full_name, p_phone, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    ON CONFLICT (phone) DO UPDATE
        SET full_name = EXCLUDED.full_name, updated_at = CURRENT_TIMESTAMP
    RETURNING customers.id INTO v_customer_id;

    -- 3. Insert service request
    BEGIN
        INSERT INTO public.service_requests (
            customer_id, request_purpose, property_type, property_location,
            consultation_date, additional_comments, status, source_channel,
            source_row_id, created_at, updated_at
        )
        VALUES (
            v_customer_id, p_request_purpose, p_property_type, p_property_location,
            p_consultation_date, p_additional_comments, 'NEW', 'GOOGLE_FORMS',
            p_source_row_id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        )
        ON CONFLICT (source_row_id) DO NOTHING
        RETURNING service_requests.id, service_requests.created_at INTO v_request_id, v_created_at;
    EXCEPTION WHEN unique_violation THEN
        RETURN;
    END;

    IF v_request_id IS NULL THEN
        RETURN;
    END IF;

    -- 4. Transactional Outbox insertion
    INSERT INTO public.notification_outbox (
        channel, recipient, subject, payload_json, status, retry_count, max_retries, next_retry_at, created_at
    )
    VALUES (
        'EMAIL',
        COALESCE((SELECT email FROM public.customers WHERE customers.id = v_customer_id), 'kanaleclipse@gmail.com'),
        'Нова заявка на консультацію - ' || p_full_name,
        jsonb_build_object(
            'request_id', v_request_id,
            'source_row_id', p_source_row_id,
            'full_name', p_full_name,
            'phone', p_phone,
            'purpose', p_request_purpose,
            'property_type', p_property_type,
            'location', p_property_location,
            'consultation_date', p_consultation_date
        ),
        'PENDING',
        0,
        5,
        CURRENT_TIMESTAMP,
        CURRENT_TIMESTAMP
    );

    -- 5. Return row matching legacy RETURNING columns
    RETURN QUERY SELECT
        v_request_id AS id,
        p_full_name AS res_full_name,
        p_phone AS res_phone,
        p_request_purpose AS res_purpose,
        p_property_type AS res_type,
        p_property_location AS res_location,
        p_consultation_date AS res_date,
        p_additional_comments AS res_comments,
        v_created_at AS res_created_at,
        p_source_row_id AS res_source_row_id;
END;
$$ LANGUAGE plpgsql;
"""

@pytest.fixture(scope="module")
def pg_engine():
    engine = create_engine(POSTGRES_URL)
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM public.notification_attempts WHERE outbox_id IN (SELECT id FROM public.notification_outbox WHERE payload_json->>'phone' = '+380671110099');"))
        conn.execute(text("DELETE FROM public.notification_outbox WHERE payload_json->>'phone' = '+380671110099';"))
        conn.execute(text("DELETE FROM public.service_requests WHERE source_row_id LIKE 'test_lr4_gate1_%';"))
        conn.execute(text("DELETE FROM public.customers WHERE phone = '+380671110099';"))
        conn.execute(text(SQL_LEGACY_FUNCTION))
        conn.execute(text("""
            CREATE OR REPLACE VIEW public.real_estate_requests AS
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
        conn.commit()
    yield engine
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM public.notification_attempts WHERE outbox_id IN (SELECT id FROM public.notification_outbox WHERE payload_json->>'phone' = '+380671110099');"))
        conn.execute(text("DELETE FROM public.notification_outbox WHERE payload_json->>'phone' = '+380671110099';"))
        conn.execute(text("DELETE FROM public.service_requests WHERE source_row_id LIKE 'test_lr4_gate1_%';"))
        conn.execute(text("DELETE FROM public.customers WHERE phone = '+380671110099';"))
        conn.commit()
    engine.dispose()

def test_gate1_legacy_lr4_first_insert_and_returning(pg_engine):
    with pg_engine.connect() as conn:
        res = conn.execute(text("""
            SELECT * FROM public.insert_real_estate_request_legacy(
                'Іван Тестовий', '+380671110099', 'Хочу купити', 'Квартира',
                'м. Львів, Франківський р-н', '2026-11-20'::date, 'Первинна заявка', 'test_lr4_gate1_row_1'
            );
        """)).fetchall()
        assert len(res) == 1
        row = res[0]
        assert row.id is not None
        assert row.res_full_name == 'Іван Тестовий'
        assert row.res_phone == '+380671110099'
        assert row.res_purpose == 'Хочу купити'
        assert row.res_type == 'Квартира'
        assert row.res_source_row_id == 'test_lr4_gate1_row_1'
        conn.commit()

def test_gate1_legacy_lr4_duplicate_on_conflict_idempotency(pg_engine):
    with pg_engine.connect() as conn:
        # Re-running with the SAME source_row_id must return 0 rows (matching ON CONFLICT DO NOTHING)
        res = conn.execute(text("""
            SELECT * FROM public.insert_real_estate_request_legacy(
                'Іван Тестовий', '+380671110099', 'Хочу купити', 'Квартира',
                'м. Львів, Франківський р-н', '2026-11-20'::date, 'Дублікат', 'test_lr4_gate1_row_1'
            );
        """)).fetchall()
        assert len(res) == 0
        conn.commit()

def test_gate1_legacy_lr4_customer_deduplication(pg_engine):
    with pg_engine.connect() as conn:
        # Second distinct row with the SAME phone number: updates customer, creates second service request
        res = conn.execute(text("""
            SELECT * FROM public.insert_real_estate_request_legacy(
                'Іван Тестовий Оновлений', '+380671110099', 'Хочу орендувати', 'Будинок',
                'м. Львів, Сихів', '2026-11-25'::date, 'Друга заявка того ж клієнта', 'test_lr4_gate1_row_2'
            );
        """)).fetchall()
        assert len(res) == 1
        second_req_id = res[0].id
        conn.commit()

        # Check customers table has strictly 1 record for this phone
        custs = conn.execute(text("SELECT id, full_name, phone FROM public.customers WHERE phone = '+380671110099';")).fetchall()
        assert len(custs) == 1
        assert custs[0].full_name == 'Іван Тестовий Оновлений'

        # Check service_requests has 2 distinct requests linking to this customer
        reqs = conn.execute(text(f"SELECT id, request_purpose FROM public.service_requests WHERE customer_id = {custs[0].id};")).fetchall()
        assert len(reqs) == 2

def test_gate1_legacy_lr4_outbox_and_grafana_view(pg_engine):
    with pg_engine.connect() as conn:
        # Verify outbox items were generated
        outbox_items = conn.execute(text("""
            SELECT id, recipient, status, payload_json
            FROM public.notification_outbox
            WHERE payload_json->>'phone' = '+380671110099'
            ORDER BY id ASC;
        """)).fetchall()
        assert len(outbox_items) == 2
        for item in outbox_items:
            assert item.status in ('PENDING', 'SENT', 'MOCK_DELIVERED', 'PROVIDER_ACCEPTED')

        # Verify backward-compatibility view real_estate_requests reads both records
        view_rows = conn.execute(text("""
            SELECT id, full_name, phone, request_purpose
            FROM public.real_estate_requests
            WHERE phone = '+380671110099';
        """)).fetchall()
        assert len(view_rows) == 2
