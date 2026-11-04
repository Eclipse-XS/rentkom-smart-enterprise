import pytest
import os
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from core.config import settings

# Force safe test environment settings
settings.ENVIRONMENT = "test"
settings.RUN_OUTBOX_WORKER_IN_LIFESPAN = False
settings.NOTIFICATION_PROVIDER = "mock"
if not settings.TEST_WEBHOOK_SECRET:
    settings.TEST_WEBHOOK_SECRET = "a29bbfa300ff2018c569bd8076f3ca0ed1961d865f566406871ca2e465150b59"
if not settings.INTERNAL_API_KEY:
    settings.INTERNAL_API_KEY = "95612a831a16e21414d0c12d05c5aa282293450936ad8e5704529f4673405360"
if not settings.ADMIN_API_KEY:
    settings.ADMIN_API_KEY = "rentkom-demo-admin-key-2026"

# In-memory SQLite with StaticPool so all connections share the same memory DB
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

from tests.safety import assert_safe_test_database
assert_safe_test_database(SQLALCHEMY_DATABASE_URL)

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)

@event.listens_for(engine, "connect")
def _do_connect(dbapi_connection, connection_record):
    # Disable pysqlite's implicit transaction emission so SAVEPOINT and outer transaction rollback work identically to PostgreSQL
    dbapi_connection.isolation_level = None

@event.listens_for(engine, "begin")
def _do_begin(conn):
    conn.exec_driver_sql("BEGIN")

TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Patch core database module so lifespan uses the in-memory engine during testing
from core.database import Base, get_db
import core.database
import core.main
core.database.engine = engine
core.database.SessionLocal = TestingSessionLocal
core.main.engine = engine
core.main.SessionLocal = TestingSessionLocal
Base.metadata.create_all(bind=engine)

from core.main import app

@pytest.fixture(scope="function")
def db_session():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

@pytest.fixture(scope="function")
def client(db_session):
    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
