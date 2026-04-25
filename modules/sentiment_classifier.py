"""
S&R Extract — Sentiment Classifier
====================================
Classifies social-media comment text into:
  POSITIVE | NEUTRAL | NEGATIVE | IRRELEVANT

Strategy:
  1. Fast keyword-based pass (no external dependency)
  2. If OPENAI_API_KEY is set and `openai` is installed, uncertain
     cases are batched to GPT-3.5-turbo for a second opinion.

The caller receives the same 4-value string regardless of which
strategy resolved the label.
"""
from __future__ import annotations

import os
import re
from typing import Optional

# ---------------------------------------------------------------------------
# Keyword lists — English + common Filipino/Taglish social-media terms
# ---------------------------------------------------------------------------
POSITIVE_KEYWORDS: list[str] = [
    # English
    "good", "great", "excellent", "amazing", "love", "best", "wonderful",
    "fantastic", "happy", "thank", "thanks", "perfect", "nice", "awesome",
    "superb", "outstanding", "impressive", "appreciate", "recommend",
    "satisfied", "quality", "fresh", "legit", "reliable", "trusted",
    "convenient", "helpful", "friendly", "clean", "fast", "efficient",
    "well done", "keep it up", "very good", "so good", "love it",
    # Filipino / Taglish
    "sulit", "ganda", "maganda", "masarap", "nais", "gusto",
    "solid", "mabuti", "nakaka-happy", "saya", "salamat", "maraming salamat",
    "sige pa", "lodi", "idol", "ang galing", "galing", "winner", "pababa ang presyo",
    "ok lang", "ay okay", "ok naman", "magaling", "magpasalamat",
]

NEGATIVE_KEYWORDS: list[str] = [
    # English
    "bad", "terrible", "awful", "hate", "worst", "horrible", "poor",
    "disappointing", "wrong", "fake", "scam", "fraud", "expired",
    "spoiled", "rotten", "disgusting", "never again", "refund", "stolen",
    "missing", "overpriced", "expensive", "slow", "rude", "unhelpful",
    "dirty", "broken", "defective", "complaint", "failed", "false",
    "misleading", "not good", "no good", "very bad", "so bad",
    # Filipino / Taglish
    "malansa", "mabaho", "mahal na mahal", "panget", "basura",
    "nakakalungkot", "hindi ok", "ayaw", "peke", "hindi totoo",
    "walang kwenta", "sayang pera", "hindi sulit", "scam sila",
    "huwag kayo dito", "bigo", "nabigo", "grabe naman",
    "hindi ako satisfied", "hindi maganda", "hindi masarap",
]

IRRELEVANT_PATTERNS: list[str] = [
    r"^[\W\s]+$",                      # only punctuation / emojis / whitespace
    r"^https?://\S+$",                 # bare URL
    r"^@\w+(\s+@\w+)*$",              # mention chain only
    r"^(\w+\s*){1,2}$",               # 1-2 generic words (e.g. "lol", "haha nice")
    r"^(haha|hehe|lol|lmao|😂|🤣|❤️|👍|👏|🙌|😍)+$",  # reaction noise
    r"^\d+$",                          # numbers only
    r"^[.!?]+$",                       # punctuation only
]

_irrelevant_re = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in IRRELEVANT_PATTERNS]

# ---------------------------------------------------------------------------
# OpenAI optional integration
# ---------------------------------------------------------------------------
_OPENAI_ENABLED: Optional[bool] = None   # resolved lazily

def _openai_available() -> bool:
    global _OPENAI_ENABLED
    if _OPENAI_ENABLED is not None:
        return _OPENAI_ENABLED
    if not os.getenv("OPENAI_API_KEY", "").strip():
        _OPENAI_ENABLED = False
        return False
    try:
        import openai  # noqa: F401
        _OPENAI_ENABLED = True
    except ImportError:
        _OPENAI_ENABLED = False
    return _OPENAI_ENABLED


_OPENAI_SYSTEM_PROMPT = (
    "You are a social-media sentiment classifier for a Philippine retail brand. "
    "Classify each comment as exactly one of: POSITIVE, NEUTRAL, NEGATIVE, IRRELEVANT. "
    "IRRELEVANT means the comment is off-topic, spam, or contains no meaningful opinion. "
    "Reply ONLY with a JSON array of strings matching the order of the input list. "
    "Example: [\"POSITIVE\",\"NEUTRAL\",\"NEGATIVE\",\"IRRELEVANT\"]"
)

_OPENAI_BATCH_SIZE = 40   # comments per API call


def _classify_batch_openai(texts: list[str]) -> list[str]:
    """Call GPT-3.5-turbo to classify a batch of texts. Returns same-length list."""
    try:
        import openai
        import json as _json

        client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(texts))
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": _OPENAI_SYSTEM_PROMPT},
                {"role": "user", "content": f"Classify these comments:\n{numbered}"},
            ],
            temperature=0.0,
            max_tokens=200,
        )
        raw = response.choices[0].message.content or "[]"
        result = _json.loads(raw)
        if isinstance(result, list) and len(result) == len(texts):
            valid = {"POSITIVE", "NEUTRAL", "NEGATIVE", "IRRELEVANT"}
            return [v if v in valid else "NEUTRAL" for v in result]
    except Exception:
        pass
    return ["NEUTRAL"] * len(texts)


# ---------------------------------------------------------------------------
# Core classification logic
# ---------------------------------------------------------------------------

def _keyword_classify(text: str) -> str:
    """Fast single-text keyword classifier. Returns one of the 4 labels."""
    stripped = text.strip()
    if not stripped:
        return "IRRELEVANT"

    # Check irrelevant patterns first
    for pattern in _irrelevant_re:
        if pattern.match(stripped):
            return "IRRELEVANT"

    lower = stripped.lower()

    pos_hits = sum(1 for kw in POSITIVE_KEYWORDS if kw in lower)
    neg_hits = sum(1 for kw in NEGATIVE_KEYWORDS if kw in lower)

    if pos_hits > neg_hits:
        return "POSITIVE"
    if neg_hits > pos_hits:
        return "NEGATIVE"
    if pos_hits == 0 and neg_hits == 0:
        # Very short comments with no signal → NEUTRAL or IRRELEVANT
        word_count = len(stripped.split())
        if word_count <= 3:
            return "IRRELEVANT"
        return "NEUTRAL"

    # Tie → NEUTRAL
    return "NEUTRAL"


def classify_sentiment(text: str) -> str:
    """
    Classify a single comment string.

    Returns: 'POSITIVE' | 'NEUTRAL' | 'NEGATIVE' | 'IRRELEVANT'
    """
    return _keyword_classify(text)


def classify_comments(comments: list[dict]) -> list[dict]:
    """
    Classify a list of comment dicts in place.

    Each dict must have a 'text' key. A 'sentiment' key is added/updated.
    If OpenAI is available, uncertain comments are batched for a second pass.

    Returns the same list (mutated).
    """
    if not comments:
        return comments

    # First pass: keyword classifier
    uncertain_indices: list[int] = []
    for i, comment in enumerate(comments):
        text = str(comment.get("text") or "").strip()
        label = _keyword_classify(text)
        comment["sentiment"] = label
        # "NEUTRAL" with meaningful text is a candidate for AI refinement
        if label == "NEUTRAL" and len(text.split()) >= 4:
            uncertain_indices.append(i)

    # Second pass: OpenAI for uncertain comments
    if _openai_available() and uncertain_indices:
        batches = [
            uncertain_indices[i: i + _OPENAI_BATCH_SIZE]
            for i in range(0, len(uncertain_indices), _OPENAI_BATCH_SIZE)
        ]
        for batch_indices in batches:
            texts = [str(comments[idx].get("text") or "") for idx in batch_indices]
            ai_labels = _classify_batch_openai(texts)
            for idx, label in zip(batch_indices, ai_labels):
                comments[idx]["sentiment"] = label

    return comments
