import os
import tempfile
from urllib.parse import urlparse, urlunparse
import pytest
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker

from core.database import Base
from core.models import Customer, ServiceRequest, CustomerFeedback, NotificationOutbox
from tests.safety import assert_safe_test_database, ensure_test_schema_marker
from tools.backup_restore import backup_postgres_database, restore_postgres_database
from tools.seed_demo_data import seed_demo_data


def _replace_db_name(db_url: str, new_db_name: str) -> str:
    parsed = urlparse(db_url)
    return urlunparse((parsed.scheme, parsed.netloc, f"/{new_db_name}", "", "", ""))


def test_restore_rejects_non_test_database_url():
    """Verify restore_postgres_database refuses to restore into production or default 'postgres' DBs."""
    with pytest.raises(RuntimeError, match="DATA SAFETY ABORT"):
        restore_postgres_database(
            "postgresql+psycopg2://postgres:postgres@127.0.0.1:5432/lab4_real_estate",
            "dummy_backup.sql",
        )
    with pytest.raises(RuntimeError, match="DATA SAFETY ABORT"):
        restore_postgres_database(
            "postgresql+psycopg2://postgres:postgres@127.0.0.1:5432/postgres",
            "dummy_backup.sql",
        )


def test_postgres_backup_and_restore_into_disposable_database():
    """
    Executes end-to-end PostgreSQL backup and restore verification:
      1. Seeds disposable source test database.
      2. Runs pg_dump via backup_postgres_database() and verifies non-zero file size.
      3. Creates an empty disposable target database ('rentkom_restore_test').
      4. Runs psql restore via restore_postgres_database().
      5. Verifies table existence, exact row counts, referential integrity, and FastAPI ORM connectivity.
    """
    source_url = os.getenv("TEST_DATABASE_URL")
    if not source_url:
        pytest.skip("TEST_DATABASE_URL not set; skipping real PostgreSQL backup/restore test.")
    assert_safe_test_database(source_url)

    try:
        src_engine = create_engine(source_url, pool_pre_ping=True, isolation_level="AUTOCOMMIT")
        with src_engine.connect() as conn:
            conn.execute(text("SELECT 1;"))
        ensure_test_schema_marker(src_engine)
    except RuntimeError:
        raise
    except Exception as exc:
        pytest.skip(f"PostgreSQL test database unreachable: {exc}")

    # 1. Ensure schema and demo data exist in source test DB
    Base.metadata.create_all(bind=src_engine)
    SrcSession = sessionmaker(bind=src_engine, expire_on_commit=False)
    src_session = SrcSession()
    seed_demo_data(db=src_session)

    expected_customers = src_session.query(Customer).count()
    expected_requests = src_session.query(ServiceRequest).count()
    expected_feedbacks = src_session.query(CustomerFeedback).count()
    expected_outbox = src_session.query(NotificationOutbox).count()
    src_session.close()
    assert expected_requests >= 25
    assert expected_feedbacks >= 30

    # 2. Create backup file using pg_dump
    dump_path = os.path.join(tempfile.gettempdir(), "rentkom_disposable_backup_test.sql")
    if os.path.exists(dump_path):
        os.remove(dump_path)

    out_path = backup_postgres_database(db_url=source_url, output_file=dump_path)
    assert os.path.isfile(out_path)
    assert os.path.getsize(out_path) > 1000

    # 3. Create fresh disposable target database 'rentkom_restore_test'
    restore_db_name = "rentkom_restore_test"
    restore_url = _replace_db_name(source_url, restore_db_name)
    assert_safe_test_database(restore_url)

    with src_engine.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {restore_db_name};"))
        conn.execute(text(f"CREATE DATABASE {restore_db_name};"))

    try:
        # 4. Restore backup into 'rentkom_restore_test'
        restore_postgres_database(target_db_url=restore_url, backup_file=out_path)

        # 5. Verify restored database tables, row counts, referential integrity, and ORM queries
        target_engine = create_engine(restore_url, pool_pre_ping=True)
        inspector = inspect(target_engine)
        tables = set(inspector.get_table_names())
        for required_table in (
            "customers",
            "service_requests",
            "request_status_history",
            "customer_feedbacks",
            "notification_outbox",
            "notification_attempts",
            "audit_events",
        ):
            assert required_table in tables, f"Restored database missing table '{required_table}'"

        TargetSession = sessionmaker(bind=target_engine, expire_on_commit=False)
        target_session = TargetSession()
        assert target_session.query(Customer).count() == expected_customers
        assert target_session.query(ServiceRequest).count() == expected_requests
        assert target_session.query(CustomerFeedback).count() == expected_feedbacks
        assert target_session.query(NotificationOutbox).count() == expected_outbox

        # Verify ORM relationship traversal (referential integrity)
        sample_req = target_session.query(ServiceRequest).first()
        assert sample_req is not None
        assert sample_req.customer is not None
        assert sample_req.customer.phone.startswith("+380")

        target_session.close()
        target_engine.dispose()
    finally:
        with src_engine.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {restore_db_name};"))
        src_engine.dispose()
        if os.path.exists(dump_path):
            try:
                os.remove(dump_path)
            except OSError:
                pass
