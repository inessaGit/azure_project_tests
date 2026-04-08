"""
LAYER 4 — REGRESSION TESTS
===========================
What:  Lock in correct behavior permanently. One test per bug fix. Golden dataset.
Mock:  External AI only (same as other layers).
Speed: Fast — runs on every commit in CI.
Tells you: Something that used to work is now broken.

Rule: Every bug fix MUST add a regression test.
      Every item in GOLDEN_DATASET must always pass.
"""
import time
import pytest
from unittest.mock import patch
from app.classifier import classify_by_keywords


# ---------------------------------------------------------------------------
# Golden dataset — hand-reviewed, authoritative expected outputs
# ---------------------------------------------------------------------------
# Format: (document_text, expected_category)
# These must NEVER regress. Add new entries as the system grows.

GOLDEN_DATASET = [
    # --- Core happy paths ---
    (
        "This non-disclosure agreement entered into as of January 1, 2024 between Acme Corp and Beta Ltd.",
        "NDA",
    ),
    (
        "Master services agreement governing the provision of professional services.",
        "MSA",
    ),
    (
        "Statement of work for the development of a custom data pipeline, including deliverables and milestones.",
        "SOW",
    ),
    (
        "Employment agreement for a full-time senior software engineer, including compensation and benefits.",
        "Employment",
    ),
    (
        "The party of the first part hereby agrees to the terms set forth.",
        "Other",
    ),
    # --- Edge cases that were previously bugs (now fixed) ---
    (
        "NON-DISCLOSURE AGREEMENT",                         # all caps
        "NDA",
    ),
    (
        "Non-Disclosure Agreement\n\nThis agreement is made on the date written above.",
        "NDA",
    ),
    (
        "non disclosure agreement between the undersigned parties.",  # no hyphen
        "NDA",
    ),
    (
        "MASTER AGREEMENT for all services rendered hereunder.",      # all caps
        "MSA",
    ),
    (
        "CONFIDENTIALITY AGREEMENT protecting trade secrets.",        # synonym
        "NDA",
    ),
]


@pytest.mark.parametrize("document_text,expected_category", GOLDEN_DATASET)
@pytest.mark.regression
def test_golden_dataset(document_text, expected_category):
    """
    Regression guard: these inputs must always produce the expected category.
    A failure here means a previously-correct classification is now broken.
    """
    result = classify_by_keywords(document_text)
    assert result == expected_category, (
        f"\nREGRESSION DETECTED\n"
        f"  Input:    '{document_text[:80]}...'\n"
        f"  Got:      '{result}'\n"
        f"  Expected: '{expected_category}'\n"
        f"Check recent changes to classifier.py keyword lists."
    )


# ---------------------------------------------------------------------------
# Individual bug regression tests — one per bug, named by what broke
# ---------------------------------------------------------------------------

@pytest.mark.regression
def test_regression_bug_uppercase_nda_not_matched():
    """
    Bug: 'NON-DISCLOSURE' in uppercase was not matched.
    Fix: Added .lower() normalization before keyword comparison.
    Date fixed: 2024-01-15. Must stay fixed.
    """
    assert classify_by_keywords("NON-DISCLOSURE AGREEMENT") == "NDA"


@pytest.mark.regression
def test_regression_bug_nda_without_hyphen_not_matched():
    """
    Bug: 'non disclosure' (no hyphen) was not in the keyword list.
    Fix: Added 'non disclosure' as an explicit keyword variant.
    Must stay fixed.
    """
    assert classify_by_keywords("non disclosure agreement between the parties") == "NDA"


@pytest.mark.regression
def test_regression_bug_first_match_priority():
    """
    Bug: When a document contained keywords from multiple categories,
    the category returned was non-deterministic (dict iteration order).
    Fix: Category order in CATEGORY_KEYWORDS dict is now deterministic.
    """
    # NDA keywords appear first in the dict → NDA must win
    result = classify_by_keywords("non-disclosure agreement with master services terms")
    assert result == "NDA"


@pytest.mark.regression
def test_regression_empty_string_returns_other_not_error():
    """
    Bug: Empty string input caused a KeyError in early versions.
    Fix: any() on empty list returns False cleanly → falls through to Other.
    """
    result = classify_by_keywords("")
    assert result == "Other"


# ---------------------------------------------------------------------------
# Performance regression guard
# ---------------------------------------------------------------------------

@pytest.mark.regression
def test_classification_performance_within_threshold():
    """
    Guard: keyword classification of 1000 documents must complete in < 1s.
    Failure here means a change introduced O(n²) behavior or regex backtracking.
    """
    sample = "This non-disclosure agreement is between Acme Corp and Beta Ltd."
    start = time.perf_counter()
    for _ in range(1000):
        classify_by_keywords(sample)
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0, (
        f"PERFORMANCE REGRESSION: 1000 classifications took {elapsed:.2f}s (limit: 1.0s)"
    )


# ---------------------------------------------------------------------------
# Regression via API layer (catches route-level regressions)
# ---------------------------------------------------------------------------

@pytest.mark.regression
def test_regression_api_returns_category_field(client):
    """
    Guard: API response must always include 'category' field.
    A refactor that renamed the field would break all consumers silently.
    """
    resp = client.post("/documents/", json={
        "content": "This non-disclosure agreement is binding.",
        "filename": "nda.pdf",
    })
    assert resp.status_code == 200
    assert "category" in resp.json(), "Response missing 'category' field — likely a schema regression"


@pytest.mark.regression
def test_regression_404_detail_message_format(client):
    """
    Guard: 404 detail message format must stay stable (consumers may parse it).
    """
    resp = client.get("/documents/999999")
    assert resp.json()["detail"] == "Document not found"
