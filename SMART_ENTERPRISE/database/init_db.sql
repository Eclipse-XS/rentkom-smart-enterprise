-- ==============================================================================
-- DOCKER COMPOSE INITIALIZATION SCRIPT FOR POSTGRESQL
-- Automatically executed on container startup
-- ==============================================================================

-- Database creation is automatically handled by POSTGRES_DB=lab4_real_estate in Docker Compose.
-- When executing manually in psql against a fresh instance, connect with: \c lab4_real_estate;

-- 1. Customers Table (Deduplicated Profiles)
CREATE TABLE IF NOT EXISTS public.customers (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    full_name TEXT NOT NULL CHECK (btrim(full_name) <> ''),
    phone TEXT NOT NULL CHECK (btrim(phone) <> ''),
    email TEXT NULL,
    telegram_chat_id BIGINT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT customers_phone_key UNIQUE (phone)
);

CREATE INDEX IF NOT EXISTS idx_customers_phone ON public.customers (phone);
CREATE INDEX IF NOT EXISTS idx_customers_email ON public.customers (email);

-- 2. Service Requests Table (Normalized Consultation Ingress)
CREATE TABLE IF NOT EXISTS public.service_requests (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id BIGINT NOT NULL REFERENCES public.customers(id) ON DELETE RESTRICT,
    request_purpose TEXT NOT NULL CHECK (
        request_purpose IN (
            'Хочу купити',
            'Хочу орендувати',
            'Хочу продати',
            'Хочу здати в оренду'
        )
    ),
    property_type TEXT NOT NULL CHECK (
        property_type IN (
            'Квартира',
            'Будинок',
            'Комерційне приміщення',
            'Земля'
        )
    ),
    property_location TEXT NOT NULL CHECK (btrim(property_location) <> ''),
    consultation_date DATE NOT NULL,
    additional_comments TEXT NULL,
    status TEXT NOT NULL DEFAULT 'NEW' CHECK (
        status IN ('NEW', 'IN_REVIEW', 'SCHEDULED', 'COMPLETED', 'CANCELLED')
    ),
    source_channel TEXT NOT NULL DEFAULT 'GOOGLE_FORMS' CHECK (
        source_channel IN ('GOOGLE_FORMS', 'TELEGRAM', 'REST_API', 'MANUAL')
    ),
    source_row_id TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT service_requests_source_row_id_key UNIQUE (source_row_id)
);

CREATE INDEX IF NOT EXISTS idx_service_requests_status ON public.service_requests (status);
CREATE INDEX IF NOT EXISTS idx_service_requests_customer ON public.service_requests (customer_id);
CREATE INDEX IF NOT EXISTS idx_service_requests_created ON public.service_requests (created_at DESC);

-- 3. Request Status Lifecycle History
CREATE TABLE IF NOT EXISTS public.request_status_history (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    request_id BIGINT NOT NULL REFERENCES public.service_requests(id) ON DELETE CASCADE,
    old_status TEXT NULL,
    new_status TEXT NOT NULL,
    changed_by TEXT NOT NULL DEFAULT 'system',
    comment TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_request_status_history_request ON public.request_status_history (request_id);

-- 4. Customer Feedback & Review Storage
CREATE TABLE IF NOT EXISTS public.customer_feedbacks (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id BIGINT NULL REFERENCES public.customers(id) ON DELETE SET NULL,
    client_identifier TEXT NOT NULL,
    service_rating INT NOT NULL CHECK (service_rating BETWEEN 1 AND 5),
    realtor_rating INT NOT NULL CHECK (realtor_rating BETWEEN 1 AND 5),
    feedback_text TEXT NULL,
    source_row_id TEXT NULL,
    source_channel TEXT NOT NULL DEFAULT 'GOOGLE_FORMS',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT customer_feedbacks_source_row_id_key UNIQUE (source_row_id)
);

CREATE INDEX IF NOT EXISTS idx_customer_feedbacks_created ON public.customer_feedbacks (created_at DESC);

-- 5. Transactional Notification Outbox
CREATE TABLE IF NOT EXISTS public.notification_outbox (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    channel TEXT NOT NULL CHECK (channel IN ('EMAIL', 'TELEGRAM', 'SMS')),
    recipient TEXT NOT NULL CHECK (btrim(recipient) <> ''),
    subject TEXT NOT NULL,
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK (
        status IN ('PENDING', 'PROCESSING', 'SENT', 'MOCK_DELIVERED', 'PROVIDER_ACCEPTED', 'FAILED')
    ),
    retry_count INT NOT NULL DEFAULT 0,
    max_retries INT NOT NULL DEFAULT 5,
    last_error TEXT NULL,
    next_retry_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sent_at TIMESTAMPTZ NULL
);

CREATE INDEX IF NOT EXISTS idx_notification_outbox_pending
    ON public.notification_outbox (status, next_retry_at)
    WHERE status IN ('PENDING', 'FAILED');

-- 6. Notification Delivery Attempts Audit
CREATE TABLE IF NOT EXISTS public.notification_attempts (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    outbox_id BIGINT NOT NULL REFERENCES public.notification_outbox(id) ON DELETE CASCADE,
    attempt_number INT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('SUCCESS', 'ERROR')),
    response_code INT NULL,
    error_message TEXT NULL,
    attempted_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_notification_attempts_outbox ON public.notification_attempts (outbox_id);

-- 7. System Audit Events
CREATE TABLE IF NOT EXISTS public.audit_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id BIGINT NULL,
    details_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_audit_events_created ON public.audit_events (created_at DESC);

-- 8. Telegram Inbox (Idempotency, Concurrent Lease, Deduplication)
CREATE TABLE IF NOT EXISTS public.telegram_inbox (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    bot_id VARCHAR(100) NOT NULL DEFAULT 'rentkom_bot',
    update_id BIGINT NOT NULL,
    chat_id BIGINT,
    message_text TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'PROCESSING',
    response_payload JSONB,
    locked_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    retry_count INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_telegram_inbox_bot_update UNIQUE (bot_id, update_id)
);

CREATE INDEX IF NOT EXISTS idx_telegram_inbox_bot_update ON public.telegram_inbox (bot_id, update_id);
CREATE INDEX IF NOT EXISTS idx_telegram_inbox_status_locked ON public.telegram_inbox (status, locked_at);

-- 9. Backward Compatibility View for LR4 / Legacy Grafana panels
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name = 'real_estate_requests'
          AND table_type = 'BASE TABLE'
    ) THEN
        ALTER TABLE public.real_estate_requests RENAME TO real_estate_requests_legacy_lr4;
    END IF;
END $$;

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

-- 8.1 INSTEAD OF INSERT Trigger for Complete Legacy Compatibility
CREATE OR REPLACE FUNCTION public.trg_real_estate_requests_insert()
RETURNS TRIGGER AS $$
DECLARE
    v_customer_id BIGINT;
BEGIN
    INSERT INTO public.customers (full_name, phone, created_at, updated_at)
    VALUES (NEW.full_name, NEW.phone, COALESCE(NEW.created_at, CURRENT_TIMESTAMP), COALESCE(NEW.created_at, CURRENT_TIMESTAMP))
    ON CONFLICT (phone) DO UPDATE
        SET full_name = EXCLUDED.full_name, updated_at = CURRENT_TIMESTAMP
    RETURNING id INTO v_customer_id;

    INSERT INTO public.service_requests (
        customer_id, request_purpose, property_type, property_location,
        consultation_date, additional_comments, status, source_channel,
        source_row_id, created_at, updated_at
    )
    VALUES (
        v_customer_id, NEW.request_purpose, NEW.property_type, NEW.property_location,
        NEW.consultation_date, NEW.additional_comments, 'NEW', 'GOOGLE_FORMS',
        NEW.source_row_id, COALESCE(NEW.created_at, CURRENT_TIMESTAMP), COALESCE(NEW.created_at, CURRENT_TIMESTAMP)
    )
    ON CONFLICT (source_row_id) DO NOTHING
    RETURNING id INTO NEW.id;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_instead_of_insert_real_estate_requests ON public.real_estate_requests;
CREATE TRIGGER trg_instead_of_insert_real_estate_requests
INSTEAD OF INSERT ON public.real_estate_requests
FOR EACH ROW EXECUTE FUNCTION public.trg_real_estate_requests_insert();

-- 8.2 Supported Stored Function for Legacy LR4 Ingress (Idempotent ON CONFLICT replacement)
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
    SELECT r.id, r.created_at INTO v_request_id, v_created_at
    FROM public.service_requests r
    WHERE r.source_row_id = p_source_row_id;

    IF FOUND THEN
        RETURN;
    END IF;

    INSERT INTO public.customers (full_name, phone, created_at, updated_at)
    VALUES (p_full_name, p_phone, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    ON CONFLICT (phone) DO UPDATE
        SET full_name = EXCLUDED.full_name, updated_at = CURRENT_TIMESTAMP
    RETURNING customers.id INTO v_customer_id;

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

-- 9. Seed Baseline Historical Records (LR4 verified data)
INSERT INTO public.customers (full_name, phone, created_at, updated_at) VALUES
('Олексій Мельник', '+380671112233', '2026-10-18 10:15:00+03', '2026-10-18 10:15:00+03'),
('Ірина Ковальчук', '+380502223344', '2026-10-19 11:20:00+03', '2026-10-19 11:20:00+03'),
('Маркіян Дуб', '+380933334455', '2026-10-20 14:00:00+03', '2026-10-20 14:00:00+03'),
('Софія Бойко', '+380674445566', '2026-10-21 16:45:00+03', '2026-10-21 16:45:00+03'),
('Василь Гончарук', '+380689998877', '2026-10-22 10:29:16+03', '2026-10-22 10:29:16+03')
ON CONFLICT (phone) DO NOTHING;

INSERT INTO public.service_requests (customer_id, request_purpose, property_type, property_location, consultation_date, additional_comments, status, source_channel, source_row_id, created_at, updated_at)
SELECT c.id, d.request_purpose, d.property_type, d.property_location, d.consultation_date::date, d.additional_comments, d.status, d.source_channel, d.source_row_id, d.created_at::timestamptz, d.updated_at::timestamptz
FROM (VALUES
    ('+380671112233', 'Хочу купити', 'Квартира', 'Франківський р-н, вул. Генерала Чупринки', '2026-10-20', 'Потрібен 2-кімнатний варіант біля Політехніки', 'COMPLETED', 'GOOGLE_FORMS', '1sPj...:Лист1:2', '2026-10-18 10:15:00+03', '2026-10-18 10:15:00+03'),
    ('+380502223344', 'Хочу орендувати', 'Квартира', 'Сихівський р-н, проспект Червоної Калини', '2026-10-21', 'Оренда для сім''ї, довгостроково', 'COMPLETED', 'GOOGLE_FORMS', '1sPj...:Лист1:3', '2026-10-19 11:20:00+03', '2026-10-19 11:20:00+03'),
    ('+380933334455', 'Хочу продати', 'Будинок', 'Личаківський р-н, Винники', '2026-10-22', 'Терміновий продаж будинку 150 кв.м', 'COMPLETED', 'GOOGLE_FORMS', '1sPj...:Лист1:4', '2026-10-20 14:00:00+03', '2026-10-20 14:00:00+03'),
    ('+380674445566', 'Хочу здати в оренду', 'Квартира', 'Галицький р-н, площа Ринок', '2026-10-23', 'Квартира з ремонтом під подобову/довгострокову', 'COMPLETED', 'GOOGLE_FORMS', '1sPj...:Лист1:5', '2026-10-21 16:45:00+03', '2026-10-21 16:45:00+03'),
    ('+380689998877', 'Хочу купити', 'Квартира', 'м. Львів, Шевченківський р-н', '2026-10-24', 'Консультація щодо купівлі нерухомості', 'COMPLETED', 'GOOGLE_FORMS', '1sPj...:Лист1:6', '2026-10-22 10:29:16+03', '2026-10-22 10:29:16+03')
) AS d(phone, request_purpose, property_type, property_location, consultation_date, additional_comments, status, source_channel, source_row_id, created_at, updated_at)
JOIN public.customers c ON c.phone = d.phone
ON CONFLICT (source_row_id) DO NOTHING;

-- Grant permissions to lab4_user if exists
DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'lab4_user') THEN
        GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO lab4_user;
        GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO lab4_user;
    END IF;
END $$;
