#!/usr/bin/env bash
# ==============================================================================
# Database Bootstrap & Role Provisioning Script (DATA-05)
# Creates dedicated roles with restricted privileges for application & n8n
# ==============================================================================
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    -- 1. Create dedicated application role
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'rentkom_app') THEN
            CREATE ROLE rentkom_app WITH LOGIN PASSWORD '${RENTKOM_APP_PASSWORD:-rentkom_app_secure_pass}';
        END IF;
        IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'rentkom_n8n') THEN
            CREATE ROLE rentkom_n8n WITH LOGIN PASSWORD '${RENTKOM_N8N_PASSWORD:-rentkom_n8n_secure_pass}';
        END IF;
    END \$\$;

    -- 2. Grant least-privilege permissions
    GRANT USAGE ON SCHEMA public TO rentkom_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO rentkom_app;
    GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO rentkom_app;

    GRANT USAGE ON SCHEMA public TO rentkom_n8n;
    GRANT SELECT, INSERT, UPDATE ON public.telegram_inbox TO rentkom_n8n;
    GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO rentkom_n8n;
    GRANT SELECT ON public.customer_feedbacks TO rentkom_n8n;
EOSQL

echo "[INIT-DB] Dedicated database roles and permissions configured successfully."
