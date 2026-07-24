"""Tests for cauldron_ai_admin.redaction."""
from __future__ import annotations

from cauldron_ai_admin.redaction import (
    REDACTED_KEYS,
    REDACTED_MARK,
    bound_utf8,
    redact,
    redact_exception,
)


# --------------------------------------------------------------- redact()


def test_redact_replaces_sensitive_keys_recursively():
    payload = {
        "outer": {
            "api_key": "sk-1234",
            "nested": {"password": "hunter2", "keep": "ok"},
        },
        "safe": "value",
    }
    text = redact(payload, max_bytes=4096)
    assert "sk-1234" not in text
    assert "hunter2" not in text
    assert REDACTED_MARK in text
    assert "ok" in text
    assert "value" in text


def test_redact_case_insensitive_key_match():
    payload = {"Authorization": "Bearer abc", "sessionId": "xyz"}
    text = redact(payload, max_bytes=4096)
    assert "abc" not in text
    assert "xyz" not in text


def test_redact_handles_lists():
    payload = [{"token": "leaked"}, {"safe": "kept"}]
    text = redact(payload, max_bytes=4096)
    assert "leaked" not in text
    assert "kept" in text


def test_redact_respects_utf8_byte_limit():
    """Truncation happens at UTF-8 boundaries; result must round-trip."""
    long_utf = "☃" * 200  # each char is 3 bytes → 600 bytes
    text = redact(long_utf, max_bytes=30)
    # Must be a valid string (no partial codepoints).
    encoded = text.encode("utf-8")
    assert len(encoded) <= 30
    assert text == encoded.decode("utf-8")


def test_redact_none_and_scalar_values():
    assert redact(None, max_bytes=32) == ""
    assert redact(42, max_bytes=32) == "42"
    assert redact("plain", max_bytes=32) == "plain"


def test_redact_bounds_zero_is_empty():
    assert redact({"a": "b"}, max_bytes=0) == ""


def test_redacted_keys_stable_set():
    assert "password" in REDACTED_KEYS
    assert "api_key" in REDACTED_KEYS
    assert "authorization" in REDACTED_KEYS
    assert "cookie" in REDACTED_KEYS


# --------------------------------------------------------------- redact_exception()


def test_redact_exception_hides_message():
    try:
        raise RuntimeError("secret credentials 12345")
    except RuntimeError as exc:
        text = redact_exception(exc, max_bytes=256)
    assert "secret" not in text
    assert "12345" not in text
    assert "RuntimeError" in text


def test_redact_exception_bounded():
    class VeryLongException(Exception):
        pass

    exc = VeryLongException("x" * 5000)
    text = redact_exception(exc, max_bytes=32)
    assert len(text.encode("utf-8")) <= 32


# --------------------------------------------------------------- bound_utf8()


def test_bound_utf8_truncates_at_codepoint():
    text = "☃☃☃☃☃"  # 5×3 = 15 bytes
    assert bound_utf8(text, 6) == "☃☃"
    assert bound_utf8(text, 15) == text


def test_bound_utf8_zero_is_empty():
    assert bound_utf8("hello", 0) == ""
