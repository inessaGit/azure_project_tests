"""
LAYER 2 — INTEGRATION TESTS
============================
What:  Test the API route handler ↔ database connection as a real pair.
Mock:  Only the external AI call (OpenAI). DB is real (SQLite test instance).
Speed: Seconds. Needs test DB setup/teardown.
Tells you: Component connections work — routes write/read DB correctly.

Fixtures come from tests/conftest.py (no import needed):
  - client:     TestClient wired to the test DB
  - db_session: The raw SQLAlchemy session for direct DB inspection
"""
import pytest
from unittest.mock import patch


# ---------------------------------------------------------------------------
# POST /documents/ — tag and persist a document
# ---------------------------------------------------------------------------

def test_tag_document_returns_200_with_category(client):
    response = client.post("/documents/", json={
        "content": "This non-disclosure agreement is between Acme Corp and Beta Ltd.",
        "filename": "acme_nda.pdf",
    })
    assert response.status_code == 200
    data = response.json()
    assert data["category"] == "NDA"
    assert data["filename"] == "acme_nda.pdf"


def test_tag_document_assigns_db_id(client):
    """DB must assign an integer ID on successful insert."""
    response = client.post("/documents/", json={
        "content": "Master services agreement for consulting work.",
        "filename": "msa.pdf",
    })
    assert response.status_code == 200
    assert isinstance(response.json()["id"], int)


def test_tag_document_content_persisted_correctly(client):
    """The exact content submitted must be retrievable from the DB."""
    content = "Statement of work for Phase 1 deliverables and milestones."
    response = client.post("/documents/", json={
        "content": content,
        "filename": "sow.pdf",
    })
    assert response.json()["content"] == content


def test_tag_document_missing_filename_returns_422(client):
    """Pydantic validation: missing required field → 422, not 500."""
    response = client.post("/documents/", json={"content": "Some legal text."})
    assert response.status_code == 422


def test_tag_document_missing_content_returns_422(client):
    response = client.post("/documents/", json={"filename": "doc.pdf"})
    assert response.status_code == 422


def test_tag_document_empty_body_returns_422(client):
    response = client.post("/documents/", json={})
    assert response.status_code == 422


def test_ambiguous_document_calls_ai_and_stores_result(client):
    """No keyword match → AI called → result stored in DB."""
    with patch("app.classifier.classify_with_ai", return_value="Employment") as mock_ai:
        response = client.post("/documents/", json={
            "content": "The party of the first part hereby agrees to the terms.",
            "filename": "contract.pdf",
        })
        mock_ai.assert_called_once()
    assert response.json()["category"] == "Employment"


# ---------------------------------------------------------------------------
# GET /documents/{id} — retrieve a stored document
# ---------------------------------------------------------------------------

def test_get_document_retrieves_correct_data(client):
    """Round-trip: POST then GET returns the same data."""
    post = client.post("/documents/", json={
        "content": "Employment agreement for full-time engineer.",
        "filename": "offer_letter.pdf",
    })
    doc_id = post.json()["id"]

    get = client.get(f"/documents/{doc_id}")
    assert get.status_code == 200
    assert get.json()["id"] == doc_id
    assert get.json()["filename"] == "offer_letter.pdf"
    assert get.json()["category"] == "Employment"


def test_get_nonexistent_document_returns_404(client):
    response = client.get("/documents/99999")
    assert response.status_code == 404
    assert response.json()["detail"] == "Document not found"


def test_get_document_invalid_id_type_returns_422(client):
    """String where int expected → FastAPI validation error."""
    response = client.get("/documents/not-a-number")
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Test isolation: each test gets a fresh database
# ---------------------------------------------------------------------------

def test_db_is_empty_at_test_start(client):
    """Verify no data bleeds between tests — each starts with empty DB."""
    response = client.get("/documents/1")
    assert response.status_code == 404  # empty DB, doc 1 doesn't exist


def test_two_documents_get_separate_ids(client):
    """Sequential inserts must get distinct IDs (autoincrement working)."""
    r1 = client.post("/documents/", json={
        "content": "non-disclosure agreement doc one",
        "filename": "doc1.pdf",
    })
    r2 = client.post("/documents/", json={
        "content": "master services agreement doc two",
        "filename": "doc2.pdf",
    })
    assert r1.json()["id"] != r2.json()["id"]


# ---------------------------------------------------------------------------
# GET /documents/ — list all documents
# ---------------------------------------------------------------------------

def test_list_documents_empty_db_returns_empty_list(client):
    response = client.get("/documents/")
    assert response.status_code == 200
    assert response.json() == []


def test_list_documents_returns_all_stored(client):
    client.post("/documents/", json={"content": "nda one", "filename": "a.pdf"})
    client.post("/documents/", json={"content": "msa two", "filename": "b.pdf"})
    response = client.get("/documents/")
    assert len(response.json()) == 2
