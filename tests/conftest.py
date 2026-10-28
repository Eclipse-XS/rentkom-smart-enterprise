import pytest
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from core.database import Base, get_db
from core.main import app
from core.config import settings

# Fallback test tokens if .env is not present in test working directory
if not settings.TEST_WEBHOOK_SECRET:
    settings.TEST_WEBHOOK_SECRET = os.getenv(
        "TEST_WEBHOOK_SECRET",
        "a29bbfa300ff2018c569bd8076f3ca0ed1961d865f566406871ca2e465150b59"
    )
if not settings.INTERNAL_API_KEY:
    settings.INTERNAL_API_KEY = os.getenv(
        "INTERNAL_API_KEY",
        "95612a831a16e21414d0c12d05c5aa282293450936ad8e5704529f4673405360"
    )

# In-memory SQLite with StaticPool so all connections share the same memory DB
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(scope="function")
def db_session():
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)

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
