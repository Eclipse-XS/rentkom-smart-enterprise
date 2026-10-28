-- ==============================================================================
-- RENTKOM SMART ENTERPRISE: CENTRALIZED DATA MODEL V2
-- PostgreSQL 15+ compatible DDL with transactional outbox, audit, and legacy view
-- ==============================================================================

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
        status IN ('PENDING', 'PROCESSING', 'SENT', 'FAILED')
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

-- 8. Data Migration & Compatibility Bridge
-- If legacy physical table real_estate_requests exists, migrate rows into customers and service_requests,
-- then rename the legacy table so the backward-compatibility VIEW can be created without relation collision.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name = 'real_estate_requests'
          AND table_type = 'BASE TABLE'
    ) THEN
        -- Insert distinct customers from legacy table
        INSERT INTO public.customers (full_name, phone, created_at)
        SELECT DISTINCT full_name, phone, MIN(created_at)
        FROM public.real_estate_requests
        ON CONFLICT (phone) DO UPDATE
        SET full_name = EXCLUDED.full_name;

        -- Insert service requests linked to customer
        INSERT INTO public.service_requests (
            customer_id, request_purpose, property_type, property_location,
            consultation_date, additional_comments, status, source_channel,
            source_row_id, created_at
        )
        SELECT
            c.id, r.request_purpose, r.property_type, r.property_location,
            r.consultation_date, r.additional_comments, 'COMPLETED', 'GOOGLE_FORMS',
            r.source_row_id, r.created_at
        FROM public.real_estate_requests r
        JOIN public.customers c ON c.phone = r.phone
        ON CONFLICT (source_row_id) DO NOTHING;

        -- Safely archive legacy table so the view name can be bound
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'real_estate_requests_legacy_lr4'
        ) THEN
            ALTER TABLE public.real_estate_requests RENAME TO real_estate_requests_legacy_lr4;
        ELSE
            EXECUTE 'ALTER TABLE public.real_estate_requests RENAME TO real_estate_requests_legacy_lr4_' || to_char(CURRENT_TIMESTAMP, 'YYYYMMDD_HH24MISS');
        END IF;
    END IF;
END $$;

-- 9. Backward Compatibility View for LR4 / Legacy Grafana panels
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
