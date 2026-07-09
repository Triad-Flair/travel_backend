"""Chat anti-leakage detection (PRD 2.2).

Pure, isolated, and independently testable on purpose — no DB, no request
context. `moderate()` takes text and a keyword list and returns a redaction
result; nothing here decides where keywords come from or what happens to a
flagged message. That wiring lives in services/chat.py.

Regex design notes:
  - Every pattern is linear (no nested quantifiers over the same character
    class), so none of them are vulnerable to catastrophic backtracking
    regardless of input length.
  - Patterns are deliberately conservative: a false negative (a leak that
    slips through) is recoverable via the compliance banner and human
    reporting; a false positive (redacting a booking reference or a date)
    directly damages trust in the product. When in doubt, these patterns
    lean toward under-matching.
"""

import re
from dataclasses import dataclass, field

REDACTION_TEXT = "[Hidden Detail]"

# Indian mobile number: optional +91/91 country code, then 10 digits starting
# 6-9, with at most one separator (space/dot/dash) between consecutive
# digits. Lookarounds stop it matching inside a longer digit run (e.g. a
# 12-digit reference number) or immediately after another digit.
_PHONE_PATTERN = re.compile(
    r"(?<!\d)(?:\+?91[\s.-]?)?[6-9]\d(?:[\s.-]?\d){8}(?!\d)"
)

# Standard, conservative email shape — bounded character classes only.
_EMAIL_PATTERN = re.compile(
    r"\b[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)

# http(s):// or www.-prefixed tokens, up to the next whitespace.
_URL_PATTERN = re.compile(r"\b(?:https?://\S+|www\.\S+)")


@dataclass
class ModerationResult:
    redacted_text: str
    flagged: bool
    categories: list[str] = field(default_factory=list)


def _build_keyword_pattern(keywords: list[str]) -> re.Pattern | None:
    cleaned = [re.escape(k.strip()) for k in keywords if k and k.strip()]
    if not cleaned:
        return None
    # Longest-first so "google pay" matches before a bare "pay" would (not
    # currently in the default list, but keeps the pattern order-safe for
    # ops-added keywords with overlapping prefixes).
    cleaned.sort(key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(cleaned) + r")\b", re.IGNORECASE)


def moderate(text: str | None, keywords: list[str] | None = None) -> ModerationResult:
    if not text:
        return ModerationResult(redacted_text=text or "", flagged=False)

    result = text
    categories: list[str] = []

    if _PHONE_PATTERN.search(result):
        result = _PHONE_PATTERN.sub(REDACTION_TEXT, result)
        categories.append("phone")

    if _EMAIL_PATTERN.search(result):
        result = _EMAIL_PATTERN.sub(REDACTION_TEXT, result)
        categories.append("email")

    if _URL_PATTERN.search(result):
        result = _URL_PATTERN.sub(REDACTION_TEXT, result)
        categories.append("url")

    keyword_pattern = _build_keyword_pattern(keywords or [])
    if keyword_pattern and keyword_pattern.search(result):
        result = keyword_pattern.sub(REDACTION_TEXT, result)
        categories.append("keyword")

    return ModerationResult(redacted_text=result, flagged=bool(categories), categories=categories)


# Seeded into chat_moderation_keywords by the migration — kept here too so
# the default set is visible next to the code that uses it, and so tests
# don't need a DB to exercise the keyword-detection path.
DEFAULT_KEYWORDS = [
    "whatsapp",
    "watsapp",
    "wattsapp",
    "telegram",
    "instagram",
    "insta dm",
    "paytm",
    "gpay",
    "google pay",
    "phonepe",
    "phone pe",
    "upi id",
    "venmo",
    "cashapp",
    "off platform",
    "off-platform",
    "outside the app",
    "outside this app",
    "personal number",
    "my number is",
    "call me at",
    "text me at",
    "reach me at",
]
