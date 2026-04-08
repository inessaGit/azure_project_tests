"""
conftest.py — shared fixtures available to ALL test layers.

Important: OPENAI_API_KEY must be set before any openai import to allow
the SDK's lazy client proxy to initialize. Tests mock the actual call,
so this key is never used — but the SDK requires it to exist.

How pytest finds this file:
  pytest walks up from the test file until it finds conftest.py.
  Any fixture defined here is auto-available without import.

Scope guide:
  "function" (default) — fresh fixture per test. Maximum isolation.
  "module"             — one fixture per test file. Faster, less isolated.
  "session"            — one fixture for the entire test run.
"""
import os
os.environ.setdefault("OPENAI_API_KEY", "test-key-mock-only")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.database import Base, get_db

# Separate in-memory DB for tests — never touches the real DB
TEST_DATABASE_URL = "sqlite:///./test_legal_tagger.db"

engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="function")
def db_session():
    """
    Provides a clean DB session per test.
    Tables are created before the test and dropped after — total isolation.
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
    FastAPI TestClient with the DB dependency overridden to use the test DB.

    dependency_overrides replaces get_db() in the app with our test version.
    ALWAYS cleared after the test — otherwise it leaks into other tests.
    """
    def override_get_db():
        try:
            yield db_session
        finally:
            pass  # session closed by db_session fixture

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
