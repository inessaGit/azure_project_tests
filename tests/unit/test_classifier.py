"""
LAYER 1 — UNIT TESTS
====================
What:  Test individual functions in complete isolation.
Mock:  All external dependencies (OpenAI, DB). Pure logic only.
Speed: Milliseconds. Run hundreds per second.
Tells you: Exactly which function broke and why.
"""
import pytest
from unittest.mock import patch, MagicMock

from app.classifier import classify_by_keywords, classify_with_ai, classify_document


# ---------------------------------------------------------------------------
# classify_by_keywords — pure function, no mocking needed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    # Happy path: unambiguous keyword matches
    ("This non-disclosure agreement is between Acme and Beta.", "NDA"),
    ("Master services agreement governing all engagements.",    "MSA"),
    ("Statement of work for the Q3 cloud migration project.",   "SOW"),
    ("Employment agreement for full-time senior engineer.",     "Employment"),
    # No keywords → Other
    ("The party of the first part hereby agrees.",              "Other"),
    ("",                                                         "Other"),
    # Case insensitivity
    ("NON-DISCLOSURE AGREEMENT effective January 1.",           "NDA"),
    ("MASTER AGREEMENT for all services rendered.",             "MSA"),
    # Unhyphenated variant
    ("non disclosure agreement between the parties.",           "NDA"),
    # First match wins when multiple keywords present
    ("NDA and master services agreement combined document.",    "NDA"),
])
def test_classify_by_keywords(text, expected):
    assert classify_by_keywords(text) == expected


def test_classify_by_keywords_very_long_input():
    # Must not raise or time out on large text
    long_text = "non-disclosure " * 50_000
    assert classify_by_keywords(long_text) == "NDA"


def test_classify_by_keywords_whitespace_only():
    assert classify_by_keywords("   \n\t  ") == "Other"


# ---------------------------------------------------------------------------
# classify_with_ai — always mock the OpenAI call
# ---------------------------------------------------------------------------

def test_classify_with_ai_returns_stripped_category():
    """AI response with surrounding whitespace must be stripped."""
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "  Employment  "

    with patch("app.classifier.openai.chat.completions.create", return_value=mock_response):
        result = classify_with_ai("The party agrees to compensation terms.")

    assert result == "Employment"


def test_classify_with_ai_truncates_to_2000_chars():
    """Function must never send more than 2000 chars to the model."""
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "Other"

    with patch("app.classifier.openai.chat.completions.create", return_value=mock_response) as mock_create:
        classify_with_ai("x" * 5000)
        user_message = mock_create.call_args[1]["messages"][1]["content"]
        assert len(user_message) <= 2000


def test_classify_with_ai_passes_correct_model():
    """Must call gpt-4, not a cheaper/wrong model."""
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "NDA"

    with patch("app.classifier.openai.chat.completions.create", return_value=mock_response) as mock_create:
        classify_with_ai("some text")
        called_model = mock_create.call_args[1]["model"]
        assert called_model == "gpt-4"


# ---------------------------------------------------------------------------
# classify_document — orchestration logic: keywords first, AI fallback
# ---------------------------------------------------------------------------

def test_classify_document_uses_keywords_when_match_found():
    """Keyword match → AI must NOT be called. Cost + latency guard."""
    with patch("app.classifier.classify_with_ai") as mock_ai:
        result = classify_document("This non-disclosure agreement is binding.")
        mock_ai.assert_not_called()
    assert result == "NDA"


def test_classify_document_falls_back_to_ai_when_no_keyword_match():
    """No keyword match → AI must be called exactly once."""
    with patch("app.classifier.classify_with_ai", return_value="Employment") as mock_ai:
        result = classify_document("The party of the first part hereby agrees.")
        mock_ai.assert_called_once()
    assert result == "Employment"


def test_classify_document_returns_ai_result_on_fallback():
    """Whatever AI returns must be passed through unchanged."""
    with patch("app.classifier.classify_with_ai", return_value="SOW"):
        result = classify_document("Generic ambiguous legal text here.")
    assert result == "SOW"
