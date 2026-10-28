-- LAB4, variant 4: real-estate consultation requests.
-- Run this file while connected to database lab4_real_estate.

CREATE TABLE IF NOT EXISTS public.real_estate_requests (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    full_name TEXT NOT NULL CHECK (btrim(full_name) <> ''),
    phone TEXT NOT NULL CHECK (btrim(phone) <> ''),
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
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    source_row_id TEXT NOT NULL,
    CONSTRAINT real_estate_requests_source_row_id_key UNIQUE (source_row_id)
);

CREATE INDEX IF NOT EXISTS real_estate_requests_created_at_idx
    ON public.real_estate_requests (created_at DESC);

COMMENT ON TABLE public.real_estate_requests IS
    'Заявки з Google Forms для варіанта 4 лабораторної роботи 4';
COMMENT ON COLUMN public.real_estate_requests.source_row_id IS
    'Стабільний ідентифікатор рядка джерела: spreadsheet_id:sheet_name:row_number';
