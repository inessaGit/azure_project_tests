"""
LAYER 3 — SYSTEM TESTS
=======================
What:  Full application stack as a black box. Simulate real user workflows.
Mock:  Only external AI (OpenAI) — everything internal is real.
Speed: Several seconds. Module-scoped DB (persists across class tests).
Tells you: Does the system behave correctly from the user's perspective?

DB scope is "module": tests within a class intentionally share state to
mirror a real user session (upload a batch, then retrieve the batch).

Shared IDs between tests are passed via a module-scoped fixture dict —
not a mutable class variable, which would persist across test runs.
"""
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.database import Base, get_db
from app.dependencies import verify_api_key

_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_Session = sessionmaker(autocommit=False, autoflush=False, bind=_engine)


@pytest.fixture(scope="module")
def system_client():
    """
    Module-scoped client: one in-memory DB for the entire file.
    Auth bypassed via dependency_overrides.
    """
    Base.metadata.create_all(bind=_engine)

    def override_db():
        session = _Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[verify_api_key] = lambda: None
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=_engine)


@pytest.fixture(scope="module")
def batch_ids() -> dict:
    """
    Module-scoped mutable dict for sharing uploaded doc IDs across tests.
    Using a fixture (not a class variable) avoids state persisting across
    test sessions if the suite is run multiple times in the same process.
    """
    return {}


# ---------------------------------------------------------------------------
# Scenario 1: Legal team uploads a batch of documents
# ---------------------------------------------------------------------------

class TestBatchUploadWorkflow:
    """
    Scenario: A paralegal uploads three documents in sequence.
    Each must be classified, stored, and retrievable independently.
    """

    def test_upload_nda(self, system_client, batch_ids):
        with patch("app.classifier.classify_with_ai", return_value="NDA"):
            resp = system_client.post("/documents/", json={
                "content": "This non-disclosure agreement binds both parties to confidentiality.",
                "filename": "vendor_nda.pdf",
            })
        assert resp.status_code == 200
        assert resp.json()["category"] == "NDA"
        batch_ids["nda"] = resp.json()["id"]

    def test_upload_sow(self, system_client, batch_ids):
        with patch("app.classifier.classify_with_ai", return_value="SOW"):
            resp = system_client.post("/documents/", json={
                "content": "Statement of work outlining deliverables for Phase 1.",
                "filename": "phase1_sow.pdf",
            })
        assert resp.status_code == 200
        assert resp.json()["category"] == "SOW"
        batch_ids["sow"] = resp.json()["id"]

    def test_upload_msa(self, system_client, batch_ids):
        with patch("app.classifier.classify_with_ai", return_value="MSA"):
            resp = system_client.post("/documents/", json={
                "content": "Master services agreement for ongoing consulting engagement.",
                "filename": "consulting_msa.pdf",
            })
        assert resp.status_code == 200
        assert resp.json()["category"] == "MSA"
        batch_ids["msa"] = resp.json()["id"]

    def test_all_documents_retrievable(self, system_client, batch_ids):
        """After batch upload, every document is retrievable by its ID."""
        assert batch_ids, "batch_ids is empty — upload tests must have run first"
        for doc_type, doc_id in batch_ids.items():
            resp = system_client.get(f"/documents/{doc_id}")
            assert resp.status_code == 200, f"Failed to retrieve {doc_type} (id={doc_id})"

    def test_list_returns_all_uploaded(self, system_client, batch_ids):
        """GET /documents/ must return all uploaded documents."""
        assert batch_ids, "batch_ids is empty — upload tests must have run first"
        resp = system_client.get("/documents/")
        assert resp.status_code == 200
        ids_in_response = {d["id"] for d in resp.json()}
        for doc_id in batch_ids.values():
            assert doc_id in ids_in_response


# ---------------------------------------------------------------------------
# Scenario 2: Ambiguous document triggers AI classification
# ---------------------------------------------------------------------------

class TestAIFallbackWorkflow:
    """
    Scenario: Document with no recognizable keywords is submitted.
    System must invoke AI and store the AI-assigned category.
    """

    def test_ambiguous_doc_invokes_ai(self, system_client):
        with patch("app.classifier.classify_with_ai", return_value="Employment") as mock_ai:
            resp = system_client.post("/documents/", json={
                "content": "The party of the first part hereby agrees to the terms set forth.",
                "filename": "mystery_contract.pdf",
            })
            mock_ai.assert_called_once()
        assert resp.json()["category"] == "Employment"

    def test_ambiguous_doc_stored_with_ai_category(self, system_client):
        """The AI-assigned category is persisted correctly."""
        with patch("app.classifier.classify_with_ai", return_value="Other"):
            post = system_client.post("/documents/", json={
                "content": "Various provisions and considerations contained herein.",
                "filename": "misc.pdf",
            })
        doc_id = post.json()["id"]
        get = system_client.get(f"/documents/{doc_id}")
        assert get.json()["category"] == "Other"


# ---------------------------------------------------------------------------
# Scenario 3: Error handling from the user's perspective
# ---------------------------------------------------------------------------

class TestErrorScenarios:

    def test_malformed_request_returns_422_not_500(self, system_client):
        """Server must validate, not crash, on bad input."""
        resp = system_client.post("/documents/", json={"content": "text"})
        assert resp.status_code == 422

    def test_oversized_content_returns_422(self, system_client):
        """Content exceeding 500k chars must be rejected at the boundary."""
        resp = system_client.post("/documents/", json={
            "content": "x" * 500_001,
            "filename": "big.pdf",
        })
        assert resp.status_code == 422

    def test_path_traversal_filename_returns_422(self, system_client):
        """Filenames with path separators must be rejected."""
        resp = system_client.post("/documents/", json={
            "content": "non-disclosure agreement text",
            "filename": "../../etc/passwd",
        })
        assert resp.status_code == 422

    def test_retrieve_nonexistent_doc_returns_404(self, system_client):
        resp = system_client.get("/documents/999999")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_health_endpoint_always_returns_ok(self, system_client):
        resp = system_client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_empty_content_classified_as_other(self, system_client):
        """Empty content string is valid input — classified as Other."""
        with patch("app.classifier.classify_with_ai", return_value="Other"):
            resp = system_client.post("/documents/", json={
                "content": "",
                "filename": "empty.pdf",
            })
        assert resp.status_code == 200
        assert resp.json()["category"] == "Other"


# ---------------------------------------------------------------------------
# Scenario 4: Pagination
# ---------------------------------------------------------------------------

class TestPagination:

    def test_list_respects_limit(self, system_client):
        """limit param must cap the number of results returned."""
        for i in range(5):
            with patch("app.classifier.classify_with_ai", return_value="Other"):
                system_client.post("/documents/", json={
                    "content": "generic contract text",
                    "filename": f"doc_{i}.pdf",
                })
        resp = system_client.get("/documents/?limit=2")
        assert resp.status_code == 200
        assert len(resp.json()) <= 2

    def test_list_rejects_limit_over_100(self, system_client):
        """limit > 100 must be rejected — prevents unbounded queries."""
        resp = system_client.get("/documents/?limit=101")
        assert resp.status_code == 422
