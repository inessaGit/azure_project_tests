import re
import openai
from app import config

VALID_CATEGORIES = {"NDA", "MSA", "SOW", "Employment", "Other"}

CATEGORY_KEYWORDS = {
    "NDA": ["non-disclosure", "non disclosure", "confidentiality agreement", "nda"],
    "MSA": ["master services", "master agreement", "msa"],
    "SOW": ["statement of work", "sow", "deliverables and milestones"],
    "Employment": ["employment agreement", "offer letter", "compensation and benefits"],
}


def classify_by_keywords(text: str) -> str:
    """
    Fast keyword-based classification using word-boundary matching.
    Returns 'Other' when no match is found.

    Uses re.search with \\b boundaries so short acronyms like 'nda' and 'msa'
    don't fire on words that contain them as substrings (e.g. 'agenda', 'Hernandez').
    """
    text_lower = text.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if re.search(r"\b" + re.escape(kw) + r"\b", text_lower):
                return category
    return "Other"


def classify_with_ai(text: str) -> str:
    """
    AI-based classification for ambiguous documents. Calls OpenAI synchronously.

    COMPLIANCE NOTE: document content is transmitted to OpenAI's API. Ensure your
    data processing agreement covers this. For privileged legal content, consider
    Azure OpenAI with a private endpoint and an enterprise DPA.

    Error handling:
    - Any OpenAI error (rate limit, timeout, 5xx) returns "Other" rather than
      propagating a 500. This degrades gracefully — the document is stored as
      unclassified rather than lost. Log and alert on these in production.
    """
    try:
        response = openai.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Classify the legal document enclosed in <document> tags into "
                        "exactly one of: NDA, MSA, SOW, Employment, Other. "
                        "Reply with only the category name."
                    ),
                },
                # Content is wrapped in XML-style tags to reduce prompt injection
                # surface: injected instructions in the document are less likely to
                # override the system prompt when clearly delimited.
                {"role": "user", "content": f"<document>{text[:2000]}</document>"},
            ],
            timeout=10.0,
        )
        if not response.choices:
            return "Other"
        raw = response.choices[0].message.content.strip()
        return raw if raw in VALID_CATEGORIES else "Other"

    except openai.RateLimitError:
        # TODO: add circuit breaker / backoff in production
        return "Other"
    except openai.APITimeoutError:
        return "Other"
    except openai.OpenAIError:
        return "Other"


def classify_document(text: str) -> str:
    """Main entry: keyword match first, AI fallback for ambiguous documents."""
    result = classify_by_keywords(text)
    if result != "Other":
        return result
    return classify_with_ai(text)
