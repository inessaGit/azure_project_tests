"""
conftest.py — shared fixtures for ALL test layers.

Environment setup must happen before any app import because config.py
reads env vars at module level.
"""
import os
os.environ.setdefault("OPENAI_API_KEY", "test-key-mock-only")
os.environ.setdefault("API_KEY", "test-api-key")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.database import Base, get_db
from app.dependencies import verify_api_key

# True in-memory SQLite with StaticPool forces all connections to share one
# in-memory database. No file is created, nothing persists between runs.
engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="function")
def db_session():
    """
    Fresh schema per test. Tables created before, dropped after.
    scope="function" = maximum isolation — no test can affect another's data.
    """
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
def client(db_session):
    """
    TestClient with:
    - get_db overridden to use the in-memory test DB
    - verify_api_key bypassed entirely (tests don't need to pass auth headers)

    dependency_overrides is ALWAYS cleared after each test to prevent leakage.
    """
    def override_get_db():
        try:
            yield db_session
        finally:
            pass  # session lifetime managed by db_session fixture

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[verify_api_key] = lambda: None
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
