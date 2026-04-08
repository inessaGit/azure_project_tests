import openai

CATEGORY_KEYWORDS = {
    "NDA": ["non-disclosure", "non disclosure", "confidentiality agreement", "nda"],
    "MSA": ["master services", "master agreement", "msa"],
    "SOW": ["statement of work", "sow", "deliverables and milestones"],
    "Employment": ["employment agreement", "offer letter", "compensation and benefits"],
}


def classify_by_keywords(text: str) -> str:
    """Fast keyword-based classification. Returns 'Other' when no match found."""
    text_lower = text.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            return category
    return "Other"


def classify_with_ai(text: str) -> str:
    """AI-based classification for ambiguous documents. Calls external model."""
    response = openai.chat.completions.create(
        model="gpt-4",
        messages=[
            {
                "role": "system",
                "content": (
                    "Classify this legal document into exactly one of: "
                    "NDA, MSA, SOW, Employment, Other. Reply with only the category name."
                ),
            },
            {"role": "user", "content": text[:2000]},
        ],
    )
    return response.choices[0].message.content.strip()


def classify_document(text: str) -> str:
    """Main entry: keyword match first, AI fallback for ambiguous documents."""
    result = classify_by_keywords(text)
    if result != "Other":
        return result
    return classify_with_ai(text)
