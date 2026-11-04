import os
from urllib.parse import urlparse
from sqlalchemy import text
from sqlalchemy.engine import Engine

FORBIDDEN_DB_NAMES = frozenset({
    "postgres",
    "rentkom",
    "lab4_real_estate",
    "rentkom_prod",
    "rentkom_production",
    "template0",
    "template1",
})

SAFETY_MARKER_TABLE = "_rentkom_test_safety_marker"
SAFETY_MARKER_TOKEN = "RENTKOM_DISPOSABLE_TEST_SCHEMA_VERIFIED"


def is_safe_test_database_url(url: str) -> bool:
    """
    Validates that a database URL points to a safe, disposable test database.
    Prevents accidental execution of DROP TABLE or destructive test fixtures
    against application, development, default 'postgres', or production databases.
    """
    if not url or not isinstance(url, str):
        return False

    # SQLite memory or temporary file is always safe
    if url.startswith("sqlite"):
        return True

    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.scheme.startswith("postgresql"):
            return False

        db_name = (parsed.path or "").lstrip("/").strip().lower()
        if not db_name:
            return False

        # Never allow default 'postgres', application DBs, or production DBs
        if db_name in FORBIDDEN_DB_NAMES:
            return False

        # Must explicitly designate test/disposable scope in database name
        is_test_named = (
            "test" in db_name
            or "disposable" in db_name
            or "temp" in db_name
        )
        return is_test_named
    except Exception:
        return False


def assert_safe_test_database(url: str) -> None:
    """Raises RuntimeError if the target database URL fails safety verification."""
    if not is_safe_test_database_url(url):
        raise RuntimeError(
            f"DATA SAFETY ABORT: Refusing destructive test operations against non-test database: '{url}'. "
            f"Target database must never be 'postgres', 'rentkom', or 'lab4_real_estate', and must include "
            f"'test', 'disposable', or 'temp' in its database name (via TEST_DATABASE_URL)."
        )


def get_verified_postgres_test_url() -> str:
    """
    Retrieves TEST_DATABASE_URL from the environment and verifies that it points
    to an explicitly isolated disposable test database before any connection attempt.
    """
    url = os.getenv(
        "TEST_DATABASE_URL",
        "postgresql+psycopg2://postgres@127.0.0.1:5439/rentkom_test",
    )
    assert_safe_test_database(url)
    return url


def ensure_test_schema_marker(engine: Engine) -> None:
    """
    Verifies and stamps a safety marker table inside the disposable test database.
    Before running destructive DROP/TRUNCATE operations on an existing non-empty schema,
    confirms that either no user tables exist yet OR the schema carries our disposable test marker.
    """
    assert_safe_test_database(str(engine.url))

    with engine.begin() as conn:
        existing_tables = set(
            conn.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                )
            ).scalars().all()
        )

        app_tables = existing_tables - {SAFETY_MARKER_TABLE}
        if app_tables and SAFETY_MARKER_TABLE not in existing_tables:
            # Check if this is an unverified pre-existing database with data
            raise RuntimeError(
                f"DATA SAFETY ABORT: Target database '{engine.url.database}' already contains tables "
                f"{sorted(app_tables)} without '{SAFETY_MARKER_TABLE}'. Refusing to run destructive fixtures."
            )

        conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS public.{SAFETY_MARKER_TABLE} (
                    marker_key VARCHAR(64) PRIMARY KEY,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
                INSERT INTO public.{SAFETY_MARKER_TABLE} (marker_key)
                VALUES (:token)
                ON CONFLICT (marker_key) DO NOTHING;
                """
            ),
            {"token": SAFETY_MARKER_TOKEN},
        )
