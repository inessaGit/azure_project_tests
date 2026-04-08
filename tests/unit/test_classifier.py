"""
LAYER 1 — UNIT TESTS
====================
What:  Test individual functions in complete isolation.
Mock:  All external dependencies (OpenAI). Pure logic only.
Speed: Milliseconds.
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
    # Word-boundary fix: these used to false-positive before boundary matching
    ("Please review the agenda before the meeting.",            "Other"),
    ("The Hernandez matter is scheduled for Tuesday.",          "Other"),
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
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "  Employment  "

    with patch("app.classifier.openai.chat.completions.create", return_value=mock_response):
        result = classify_with_ai("The party agrees to compensation terms.")

    assert result == "Employment"


def test_classify_with_ai_rejects_invalid_category():
    """AI response not in VALID_CATEGORIES must be normalized to 'Other'."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "This appears to be an NDA document."

    with patch("app.classifier.openai.chat.completions.create", return_value=mock_response):
        result = classify_with_ai("some text")

    assert result == "Other"


def test_classify_with_ai_returns_other_on_empty_choices():
    """Empty choices list (filtered response) must not raise IndexError."""
    mock_response = MagicMock()
    mock_response.choices = []

    with patch("app.classifier.openai.chat.completions.create", return_value=mock_response):
        result = classify_with_ai("some text")

    assert result == "Other"


def test_classify_with_ai_returns_other_on_rate_limit():
    """Rate limit error must degrade to 'Other', not raise."""
    import openai as _openai
    with patch("app.classifier.openai.chat.completions.create", side_effect=_openai.RateLimitError("rate limit", response=MagicMock(), body={})):
        result = classify_with_ai("some text")
    assert result == "Other"


def test_classify_with_ai_returns_other_on_timeout():
    """Timeout must degrade to 'Other', not raise."""
    import openai as _openai
    with patch("app.classifier.openai.chat.completions.create", side_effect=_openai.APITimeoutError(request=MagicMock())):
        result = classify_with_ai("some text")
    assert result == "Other"


def test_classify_with_ai_truncates_content_to_2000_chars():
    """Content sent inside <document> tags must not exceed 2000 chars."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Other"

    with patch("app.classifier.openai.chat.completions.create", return_value=mock_response) as mock_create:
        classify_with_ai("x" * 5000)
        user_content = mock_create.call_args[1]["messages"][1]["content"]
        # Content is wrapped: <document>{text[:2000]}</document>
        assert "<document>" in user_content
        inner = user_content.replace("<document>", "").replace("</document>", "")
        assert len(inner) <= 2000


def test_classify_with_ai_uses_configured_model():
    """Must call the model specified in config, not a hardcoded string."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "NDA"

    with patch("app.classifier.openai.chat.completions.create", return_value=mock_response) as mock_create:
        classify_with_ai("some text")
        called_model = mock_create.call_args[1]["model"]
        from app import config
        assert called_model == config.OPENAI_MODEL


def test_classify_with_ai_sets_timeout():
    """A timeout must always be set — no indefinite hangs."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "NDA"

    with patch("app.classifier.openai.chat.completions.create", return_value=mock_response) as mock_create:
        classify_with_ai("some text")
        assert mock_create.call_args[1]["timeout"] is not None


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
