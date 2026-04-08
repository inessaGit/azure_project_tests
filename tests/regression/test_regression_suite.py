"""
LAYER 4 — REGRESSION TESTS
===========================
What:  Lock in correct behavior permanently. One test per bug fix. Golden dataset.
Mock:  External AI only.
Speed: Fast — runs on every commit in CI.
Tells you: Something that used to work is now broken.

Rule: Every bug fix MUST add a regression test.
      Every item in GOLDEN_DATASET must always pass.
"""
import time
import pytest
from unittest.mock import patch
from app.classifier import classify_by_keywords, classify_document


# ---------------------------------------------------------------------------
# Golden dataset — hand-reviewed, authoritative expected outputs
# ---------------------------------------------------------------------------
# Tests keyword-matching entries through classify_by_keywords (fast, no AI).
# The "Other" case is tested separately via classify_document to verify the
# AI fallback path is actually invoked (not just that keywords return Other).

KEYWORD_DATASET = [
    # Core happy paths
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
    # Edge cases that were previously bugs (now fixed)
    ("NON-DISCLOSURE AGREEMENT",                                                    "NDA"),
    ("Non-Disclosure Agreement\n\nThis agreement is made on the date written above.", "NDA"),
    ("non disclosure agreement between the undersigned parties.",                    "NDA"),
    ("MASTER AGREEMENT for all services rendered hereunder.",                        "MSA"),
    ("CONFIDENTIALITY AGREEMENT protecting trade secrets.",                          "NDA"),
    # Word-boundary fix: these must NOT match as NDA
    ("Please review the agenda before the meeting.",                                 "Other"),
    ("The Hernandez matter is scheduled for Tuesday.",                               "Other"),
]


@pytest.mark.parametrize("document_text,expected_category", KEYWORD_DATASET)
@pytest.mark.regression
def test_golden_dataset(document_text, expected_category):
    """
    Regression guard: these inputs must always produce the expected category.
    A failure here means a previously-correct classification is now broken.
    """
    result = classify_by_keywords(document_text)
    assert result == expected_category, (
        f"\nREGRESSION DETECTED\n"
        f"  Input:    '{document_text[:80]}'\n"
        f"  Got:      '{result}'\n"
        f"  Expected: '{expected_category}'\n"
        f"Check recent changes to CATEGORY_KEYWORDS in classifier.py."
    )


@pytest.mark.regression
def test_golden_dataset_other_triggers_ai_fallback():
    """
    'Other' from keyword matching must reach classify_with_ai.
    Tests the full pipeline, not just the keyword layer.
    Previously this entry was tested against classify_by_keywords only,
    which bypassed the orchestration logic entirely.
    """
    with patch("app.classifier.classify_with_ai", return_value="Other") as mock_ai:
        result = classify_document("The party of the first part hereby agrees to the terms set forth.")
        mock_ai.assert_called_once()
    assert result == "Other"


# ---------------------------------------------------------------------------
# Individual bug regression tests — one per bug, named by what broke
# ---------------------------------------------------------------------------

@pytest.mark.regression
def test_regression_bug_uppercase_nda_not_matched():
    """
    Bug: 'NON-DISCLOSURE' in uppercase was not matched.
    Fix: .lower() normalization before keyword comparison.
    """
    assert classify_by_keywords("NON-DISCLOSURE AGREEMENT") == "NDA"


@pytest.mark.regression
def test_regression_bug_nda_without_hyphen_not_matched():
    """
    Bug: 'non disclosure' (no hyphen) was missing from the keyword list.
    Fix: Added 'non disclosure' as an explicit variant.
    """
    assert classify_by_keywords("non disclosure agreement between the parties") == "NDA"


@pytest.mark.regression
def test_regression_bug_first_match_priority():
    """
    Bug: Non-deterministic category when multiple keywords matched
    (depended on dict iteration order, which was undefined in older Python).
    Fix: CATEGORY_KEYWORDS order is now explicit and documented.
    NDA keywords appear first → NDA must win.
    """
    result = classify_by_keywords("non-disclosure agreement with master services terms")
    assert result == "NDA"


@pytest.mark.regression
def test_regression_empty_string_returns_other_not_error():
    """
    Bug: Empty string input caused a KeyError in early versions.
    Fix: any() on empty iterable returns False cleanly → falls through to Other.
    """
    assert classify_by_keywords("") == "Other"


@pytest.mark.regression
def test_regression_agenda_no_longer_matches_nda():
    """
    Bug: 'agenda' contains 'nda' as a substring — was classified as NDA.
    Fix: Word-boundary matching (re.search with \\b) prevents false positives.
    """
    assert classify_by_keywords("Please review the agenda for today's meeting.") == "Other"


@pytest.mark.regression
def test_regression_hernandez_no_longer_matches_nda():
    """
    Bug: 'Hernandez' contains 'nda' as a substring — was classified as NDA.
    Fix: Word-boundary matching.
    """
    assert classify_by_keywords("The Hernandez matter is scheduled for Tuesday.") == "Other"


# ---------------------------------------------------------------------------
# AI response validation regression tests
# ---------------------------------------------------------------------------

@pytest.mark.regression
def test_regression_invalid_ai_response_normalized_to_other():
    """
    Bug: AI returning free text (e.g. 'This appears to be an NDA.') was stored
    directly as the category value, corrupting the database.
    Fix: Response validated against VALID_CATEGORIES; anything else → 'Other'.
    """
    from unittest.mock import MagicMock
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "This appears to be an NDA document."

    with patch("app.classifier.openai.chat.completions.create", return_value=mock_response):
        from app.classifier import classify_with_ai
        result = classify_with_ai("ambiguous text")
    assert result == "Other"


@pytest.mark.regression
def test_regression_empty_ai_choices_no_index_error():
    """
    Bug: response.choices[0] raised IndexError when OpenAI returned an
    empty choices list (filtered response, error response).
    Fix: Guard added before indexing.
    """
    from unittest.mock import MagicMock
    mock_response = MagicMock()
    mock_response.choices = []

    with patch("app.classifier.openai.chat.completions.create", return_value=mock_response):
        from app.classifier import classify_with_ai
        result = classify_with_ai("some text")
    assert result == "Other"


# ---------------------------------------------------------------------------
# Performance regression guard
# ---------------------------------------------------------------------------

@pytest.mark.regression
def test_classification_performance_within_threshold():
    """
    Guard: 1000 keyword classifications must complete in < 0.05s.
    (Actual runtime is ~0.001s — the threshold allows 50x regression headroom
    before failing, which is enough to catch real regressions without being
    flaky on a loaded CI machine.)
    """
    sample = "This non-disclosure agreement is between Acme Corp and Beta Ltd."
    start = time.perf_counter()
    for _ in range(1000):
        classify_by_keywords(sample)
    elapsed = time.perf_counter() - start
    assert elapsed < 0.05, (
        f"PERFORMANCE REGRESSION: 1000 classifications took {elapsed:.3f}s (limit: 0.05s)"
    )


# ---------------------------------------------------------------------------
# Regression via API layer (catches route-level regressions)
# ---------------------------------------------------------------------------

@pytest.mark.regression
def test_regression_api_response_includes_required_fields(client):
    """
    Guard: API response schema must always include these fields.
    A rename/removal would break all consumers silently.
    """
    resp = client.post("/documents/", json={
        "content": "This non-disclosure agreement is binding.",
        "filename": "nda.pdf",
    })
    assert resp.status_code == 200
    data = resp.json()
    for field in ("id", "filename", "category", "content", "created_at"):
        assert field in data, f"Response missing required field: '{field}'"


@pytest.mark.regression
def test_regression_404_detail_message_stable(client):
    """
    Guard: 404 detail message format must stay stable — consumers may parse it.
    """
    resp = client.get("/documents/999999")
    assert resp.json()["detail"] == "Document not found"


@pytest.mark.regression
def test_regression_oversized_input_rejected(client):
    """
    Guard: content > 500k chars must return 422, not reach the classifier.
    """
    resp = client.post("/documents/", json={
        "content": "x" * 500_001,
        "filename": "big.pdf",
    })
    assert resp.status_code == 422


@pytest.mark.regression
def test_regression_path_traversal_filename_rejected(client):
    """
    Guard: path-traversal filenames must be rejected at the model boundary.
    """
    resp = client.post("/documents/", json={
        "content": "non-disclosure agreement",
        "filename": "../../etc/passwd",
    })
    assert resp.status_code == 422
