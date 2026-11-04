#!/usr/bin/env bash
# ==============================================================================
# Database Bootstrap & Role Provisioning Script (DATA-05)
# Creates dedicated roles with restricted privileges for application & n8n
# ==============================================================================
set -eu

: "${RENTKOM_APP_PASSWORD:?RENTKOM_APP_PASSWORD must be set}"
: "${RENTKOM_N8N_PASSWORD:?RENTKOM_N8N_PASSWORD must be set}"

psql -v ON_ERROR_STOP=1 \
    -v app_password="$RENTKOM_APP_PASSWORD" \
    -v n8n_password="$RENTKOM_N8N_PASSWORD" \
    --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-'EOSQL'
    -- 1. Create dedicated application role
    SELECT format('CREATE ROLE rentkom_app WITH LOGIN PASSWORD %L', :'app_password')
    WHERE NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'rentkom_app') \gexec
    SELECT format('ALTER ROLE rentkom_app WITH LOGIN PASSWORD %L', :'app_password') \gexec
    SELECT format('CREATE ROLE rentkom_n8n WITH LOGIN PASSWORD %L', :'n8n_password')
    WHERE NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'rentkom_n8n') \gexec
    SELECT format('ALTER ROLE rentkom_n8n WITH LOGIN PASSWORD %L', :'n8n_password') \gexec

    -- 2. Grant least-privilege permissions. n8n owns only its own schema.
    CREATE SCHEMA IF NOT EXISTS n8n AUTHORIZATION rentkom_n8n;
    GRANT USAGE ON SCHEMA public TO rentkom_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO rentkom_app;
    GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO rentkom_app;

    GRANT USAGE, CREATE ON SCHEMA n8n TO rentkom_n8n;
    REVOKE CREATE ON SCHEMA public FROM rentkom_n8n;
    REVOKE ALL ON ALL TABLES IN SCHEMA public FROM rentkom_n8n;
    REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM rentkom_n8n;
EOSQL

echo "[INIT-DB] Dedicated database roles and permissions configured successfully."
