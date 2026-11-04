#!/usr/bin/env bash
# ==============================================================================
# Rentkom Smart Enterprise: Automated PostgreSQL Restore Script (OPS-02)
# Refuses to restore into production or non-disposable databases.
# ==============================================================================
set -euo pipefail

BACKUP_FILE="${1:-}"
TARGET_DB="${2:-${TARGET_DB:-rentkom_test}}"
DB_USER="${POSTGRES_USER:-postgres}"
DB_HOST="${POSTGRES_HOST:-127.0.0.1}"
DB_PORT="${POSTGRES_PORT:-5432}"

if [ -z "$BACKUP_FILE" ]; then
    echo "Usage: $0 <backup_file.sql> [target_db]" >&2
    exit 1
fi

if [ ! -f "$BACKUP_FILE" ]; then
    echo "[RESTORE] ERROR: Backup file '${BACKUP_FILE}' not found." >&2
    exit 1
fi

# Safety check: enforce disposable/test database names
FORBIDDEN_NAMES="^(postgres|rentkom|lab4_real_estate|rentkom_prod|rentkom_production)$"
if echo "$TARGET_DB" | grep -Ei -q "$FORBIDDEN_NAMES"; then
    echo "[RESTORE] DATA SAFETY ABORT: Refusing to restore into protected/production database '${TARGET_DB}'!" >&2
    exit 2
fi

if ! echo "$TARGET_DB" | grep -Ei -q "(test|disposable|temp)"; then
    echo "[RESTORE] DATA SAFETY ABORT: Target database '${TARGET_DB}' must contain 'test', 'disposable', or 'temp'." >&2
    exit 2
fi

echo "[RESTORE] Restoring '${BACKUP_FILE}' into target database '${TARGET_DB}' at ${DB_HOST}:${DB_PORT}..."
export PGPASSWORD="${POSTGRES_PASSWORD:-postgres}"

psql \
    -h "$DB_HOST" \
    -p "$DB_PORT" \
    -U "$DB_USER" \
    -d "$TARGET_DB" \
    -v ON_ERROR_STOP=1 \
    -f "$BACKUP_FILE"

echo "[RESTORE] SUCCESS: Database '${TARGET_DB}' restored from '${BACKUP_FILE}'."
