-- Structure and constraints.
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'real_estate_requests'
ORDER BY ordinal_position;

-- Latest requests.
SELECT id, full_name, phone, request_purpose, property_type,
       property_location, consultation_date, additional_comments,
       created_at, source_row_id
FROM public.real_estate_requests
ORDER BY created_at DESC, id DESC
LIMIT 20;

-- Total records.
SELECT COUNT(*) AS total_records
FROM public.real_estate_requests;

-- Duplicate source identifiers. Expected result: zero rows.
SELECT source_row_id, COUNT(*) AS duplicate_count
FROM public.real_estate_requests
GROUP BY source_row_id
HAVING COUNT(*) > 1;

-- Confirm the unique constraint exists.
SELECT conname, pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE conrelid = 'public.real_estate_requests'::regclass
ORDER BY conname;
