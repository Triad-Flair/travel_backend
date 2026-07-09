"""chat_moderation.py must be correct in isolation before it's wired into
the live message endpoints — no DB, no app context, just text in/out."""
import time

import pytest

from app.services.chat_moderation import DEFAULT_KEYWORDS, REDACTION_TEXT, moderate

# ── Phone number catches ────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "text",
    [
        "call me on 9876543210",
        "9876543210",
        "98765 43210",
        "98765-43210",
        "987.654.3210",
        "+91 9876543210",
        "+919876543210",
        "91-9876543210",
        "98-76-54-32-10",
    ],
)
def test_catches_phone_numbers_with_common_separators(text):
    result = moderate(text)
    assert result.flagged
    assert "phone" in result.categories
    assert REDACTION_TEXT in result.redacted_text
    assert "9876543210" not in result.redacted_text.replace(" ", "").replace("-", "").replace(".", "")


def test_phone_redaction_removes_the_number_not_the_whole_message():
    result = moderate("call me on 9876543210 after 6pm")
    assert result.redacted_text == f"call me on {REDACTION_TEXT} after 6pm"


# ── Phone number false-positive avoidance ───────────────────────────────────

@pytest.mark.parametrize(
    "text",
    [
        "your booking reference is TSU-2026-5CE532CE",
        "trip date: 09-07-2026",
        "trip date: 09/07/2026",
        "total amount is 535282",
        "the group has 6 members out of 9",
        "see you at 6:30",
        "meeting on 07-08-2026 at 6pm",
    ],
)
def test_does_not_flag_booking_references_dates_or_amounts_as_phone_numbers(text):
    result = moderate(text)
    assert not result.flagged, f"false positive on: {text!r} -> {result.categories}"
    assert result.redacted_text == text


def test_does_not_flag_a_bare_nine_digit_number():
    result = moderate("code is 987654321")
    assert not result.flagged


def test_does_not_partially_match_an_eleven_digit_number():
    result = moderate("reference 98765432109")
    assert not result.flagged


# ── Email catches ────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "text",
    [
        "email me at traveler@example.com",
        "reach me at first.last+trip@sub.example.co.in",
        "EMAIL@EXAMPLE.COM",
    ],
)
def test_catches_email_addresses(text):
    result = moderate(text)
    assert result.flagged
    assert "email" in result.categories
    assert "@" not in result.redacted_text


def test_does_not_flag_text_without_at_symbol():
    result = moderate("this trip.package looks great, 4.5 stars")
    assert not result.flagged


# ── URL catches ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "text",
    [
        "check https://example.com/trip",
        "visit www.example.com for details",
        "http://short.url/x",
    ],
)
def test_catches_urls(text):
    result = moderate(text)
    assert result.flagged
    assert "url" in result.categories


def test_does_not_flag_bare_domain_without_protocol_or_www():
    """Deliberately conservative — 'example.com' alone (no http/https/www
    prefix) reads too much like ordinary text (e.g. 'goa.trip', 'day1.plan')
    to safely flag without a much higher false-positive rate."""
    result = moderate("the vibe is very go-with-the-flow.chill honestly")
    assert not result.flagged


# ── Keyword catches (config-driven list) ────────────────────────────────────

@pytest.mark.parametrize(
    "text",
    [
        "let's move to WhatsApp",
        "add me on whatsapp",
        "send it on Telegram instead",
        "just gpay me the amount",
        "pay via paytm",
        "let's talk off platform",
        "can we go off-platform for this",
    ],
)
def test_catches_platform_leakage_keywords(text):
    result = moderate(text, keywords=DEFAULT_KEYWORDS)
    assert result.flagged
    assert "keyword" in result.categories


def test_keyword_matching_is_case_insensitive():
    result = moderate("WHATSAPP me", keywords=DEFAULT_KEYWORDS)
    assert result.flagged


def test_keyword_respects_word_boundaries_no_substring_match():
    """'whatsapp' must not match inside an unrelated longer word."""
    result = moderate("whatsapperson is not a word but let's test it", keywords=["whatsapp"])
    assert not result.flagged


def test_no_keywords_configured_means_no_keyword_category():
    result = moderate("let's move to whatsapp", keywords=[])
    assert not result.flagged


def test_keyword_list_is_config_driven_not_hardcoded_in_function_signature():
    """moderate() must accept an externally-supplied keyword list — this is
    what makes the list DB/config-editable without a redeploy."""
    result = moderate("use my custom app xyz123", keywords=["xyz123"])
    assert result.flagged
    assert "keyword" in result.categories


# ── Multiple categories in one message ──────────────────────────────────────

def test_flags_multiple_categories_in_one_message():
    result = moderate(
        "call 9876543210 or email traveler@example.com or check https://x.com",
        keywords=DEFAULT_KEYWORDS,
    )
    assert set(result.categories) == {"phone", "email", "url"}
    assert REDACTION_TEXT in result.redacted_text
    assert "9876543210" not in result.redacted_text
    assert "@" not in result.redacted_text
    assert "https://" not in result.redacted_text


# ── Clean messages pass through untouched ───────────────────────────────────

@pytest.mark.parametrize(
    "text",
    [
        "excited for this trip!",
        "what time should we meet at the hotel?",
        "count me in for the Goa plan",
        "",
    ],
)
def test_clean_messages_are_not_flagged_and_unmodified(text):
    result = moderate(text, keywords=DEFAULT_KEYWORDS)
    assert not result.flagged
    assert result.redacted_text == text
    assert result.categories == []


def test_none_input_does_not_crash():
    result = moderate(None)
    assert not result.flagged
    assert result.redacted_text == ""


# ── Regex safety: no catastrophic backtracking on adversarial input ────────

def test_long_digit_string_does_not_cause_catastrophic_backtracking():
    adversarial = "9" * 5000 + "a"
    start = time.monotonic()
    moderate(adversarial)
    elapsed = time.monotonic() - start
    assert elapsed < 1.0, f"moderate() took {elapsed:.2f}s on adversarial input — possible ReDoS"


def test_long_separator_heavy_string_does_not_cause_catastrophic_backtracking():
    adversarial = "9-" * 3000 + "9"
    start = time.monotonic()
    moderate(adversarial)
    elapsed = time.monotonic() - start
    assert elapsed < 1.0, f"moderate() took {elapsed:.2f}s on adversarial input — possible ReDoS"
