"""Bounded, deterministic redaction helpers for Admin AI audit records.

Every string persisted from a run/invocation is passed through :func:`redact`
and a UTF-8 byte cap. Exception messages are collapsed to a stable class
name via :func:`redact_exception` so that raw error text (which may include
secrets, filesystem paths, or SQL fragments) never lands in a durable row.
"""
from __future__ import annotations

import json
from typing import Any


# Case-insensitive substrings that mark a *key* as sensitive. Presence in
# a dict key replaces the value with the literal ``[REDACTED]`` marker.
REDACTED_KEYS: frozenset[str] = frozenset({
    "password",
    "secret",
    "token",
    "api_key",
    "authorization",
    "cookie",
    "session",
    "credential",
    "private_key",
    "passwd",
    "auth",
    "bearer",
})

REDACTED_MARK = "[REDACTED]"


def _key_is_sensitive(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    lowered = key.lower()
    return any(marker in lowered for marker in REDACTED_KEYS)


def _redact_tree(value: Any, *, depth: int = 0) -> Any:
    """Return a copy of ``value`` with sensitive keys replaced.

    Handles arbitrary depth up to a defensive limit so a malicious payload
    can't exhaust the recursion budget.
    """
    if depth > 32:
        return "[REDACTED_DEEP]"
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for k, v in value.items():
            if _key_is_sensitive(k):
                redacted[str(k)] = REDACTED_MARK
            else:
                redacted[str(k)] = _redact_tree(v, depth=depth + 1)
        return redacted
    if isinstance(value, (list, tuple)):
        return [_redact_tree(v, depth=depth + 1) for v in value]
    return value


def _truncate_utf8(text: str, max_bytes: int) -> str:
    if max_bytes <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", "ignore")


def redact(value: Any, *, max_bytes: int = 512) -> str:
    """Recursively redact sensitive keys and return a bounded UTF-8 string.

    The output is JSON when the input is a mapping/list, otherwise the
    string form of the value. Both are truncated at ``max_bytes`` UTF-8
    bytes (never mid-codepoint). Sensitive dict keys — matched case-
    insensitively against :data:`REDACTED_KEYS` — have their values
    replaced with the literal marker ``[REDACTED]`` before serialisation.

    Strings that parse as JSON are inspected as their decoded structure
    so a request body like ``'{"api_key": "sk-..."}'`` also has the
    embedded secret scrubbed before persistence.
    """
    if max_bytes < 0:
        raise ValueError("max_bytes must be non-negative")
    from collections.abc import Mapping as _M
    if isinstance(value, _M) or isinstance(value, (list, tuple)):
        cleaned = _redact_tree(value)
        try:
            text = json.dumps(cleaned, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(cleaned)
    elif value is None:
        text = ""
    elif isinstance(value, str):
        # If the string decodes cleanly as JSON, redact sensitive keys in
        # the decoded structure. Otherwise treat as opaque prose.
        stripped = value.strip()
        if stripped.startswith(("{", "[")) and stripped.endswith(("}", "]")):
            try:
                decoded = json.loads(stripped)
            except (TypeError, ValueError):
                text = value
            else:
                cleaned = _redact_tree(decoded)
                try:
                    text = json.dumps(
                        cleaned, sort_keys=True, ensure_ascii=False,
                    )
                except (TypeError, ValueError):  # pragma: no cover
                    text = value
        else:
            text = value
    elif isinstance(value, (int, float, bool)):
        text = str(value)
    else:
        text = str(value)
    return _truncate_utf8(text, max_bytes)


def redact_exception(exc: BaseException, *, max_bytes: int = 256) -> str:
    """Return a stable, secret-free bounded summary for an exception.

    The message deliberately omits ``str(exc)`` — vendor SDKs and Django's
    ORM regularly embed configuration, credentials, or PII in exception
    text. Callers get only the exception class name.
    """
    name = type(exc).__name__ if exc is not None else "UnknownError"
    summary = f"{name}: [details omitted]"
    return _truncate_utf8(summary, max_bytes)


def bound_utf8(text: str, max_bytes: int) -> str:
    """Public helper for truncating already-safe strings by UTF-8 byte count."""
    if not isinstance(text, str):
        text = str(text)
    return _truncate_utf8(text, max_bytes)
