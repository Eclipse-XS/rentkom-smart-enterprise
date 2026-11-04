#!/usr/bin/env bash
# ==============================================================================
# Rentkom Smart Enterprise: Automated PostgreSQL Backup Script (OPS-02)
# ==============================================================================
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-./backups}"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
DB_NAME="${POSTGRES_DB:-lab4_real_estate}"
DB_USER="${POSTGRES_USER:-postgres}"
DB_HOST="${POSTGRES_HOST:-127.0.0.1}"
DB_PORT="${POSTGRES_PORT:-5432}"
OUTPUT_FILE="${1:-${BACKUP_DIR}/${DB_NAME}_backup_${TIMESTAMP}.sql}"

mkdir -p "$(dirname "$OUTPUT_FILE")"

echo "[BACKUP] Initiating backup for database '${DB_NAME}' at ${DB_HOST}:${DB_PORT}..."
export PGPASSWORD="${POSTGRES_PASSWORD:-postgres}"

pg_dump \
    -h "$DB_HOST" \
    -p "$DB_PORT" \
    -U "$DB_USER" \
    -d "$DB_NAME" \
    --no-owner \
    --no-privileges \
    --clean \
    --if-exists \
    -f "$OUTPUT_FILE"

if [ -s "$OUTPUT_FILE" ]; then
    echo "[BACKUP] SUCCESS: Backup saved to '${OUTPUT_FILE}' (size: $(wc -c < "$OUTPUT_FILE") bytes)."
else
    echo "[BACKUP] ERROR: Backup file '${OUTPUT_FILE}' is empty or missing." >&2
    exit 1
fi
