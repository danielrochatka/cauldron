"""Shared identifier segment validator used across the content control plane.

This is the canonical implementation. Other packages (``cauldron_content_operations``,
``cauldron_workspace_flatfile``, ``cauldron_cms_flatfile``) import from here
rather than maintain divergent copies.

Rules:
  * Value must be a non-empty ``str``.
  * Must not contain forward or back slashes.
  * Must not be exactly ``.`` or ``..`` or contain a ``..`` component.
  * Must not carry a Windows drive prefix (``C:``, ``d:``).
  * Must not contain ASCII control characters, DEL (U+007F), or any
    character whose Unicode category starts with ``C`` (Cc, Cf, Co, Cs).
  * Length limit: 255 characters.
"""
from __future__ import annotations

import unicodedata


MAX_IDENTIFIER_LENGTH = 255


def _is_disallowed_char(ch: str) -> bool:
    # ASCII control range (0x00-0x1F), DEL (0x7F).
    if ord(ch) < 0x20 or ord(ch) == 0x7F:
        return True
    # Unicode "Other" categories: Cc (control), Cf (format), Co (private use),
    # Cs (surrogate). Category "Cn" (unassigned) is not observable in a
    # decoded ``str`` so we do not need to test for it explicitly.
    cat = unicodedata.category(ch)
    if cat.startswith("C"):
        return True
    return False


def validate_identifier_segment(value: str, field_name: str) -> None:
    r"""Raise ``ValueError`` if ``value`` is not a safe identifier segment."""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string, got {type(value).__name__}.")
    if value == "":
        raise ValueError(f"{field_name} must not be empty.")
    if len(value) > MAX_IDENTIFIER_LENGTH:
        raise ValueError(
            f"{field_name} exceeds max length of {MAX_IDENTIFIER_LENGTH}."
        )
    if "/" in value or "\\" in value:
        raise ValueError(
            f"{field_name} must not contain path separators: {value!r}."
        )
    if value.startswith(("/", "\\")):
        raise ValueError(
            f"{field_name} must not start with a path separator: {value!r}."
        )
    if value in (".", ".."):
        raise ValueError(
            f"{field_name} must not be a special path component: {value!r}."
        )
    for part in value.split("/"):
        if part == "..":
            raise ValueError(
                f"{field_name} must not contain '..' component: {value!r}."
            )
    if len(value) >= 2 and value[1] == ":" and value[0].isalpha():
        raise ValueError(
            f"{field_name} must not carry a Windows drive prefix: {value!r}."
        )
    for ch in value:
        if _is_disallowed_char(ch):
            raise ValueError(
                f"{field_name} must not contain control or format characters."
            )


__all__ = ["validate_identifier_segment", "MAX_IDENTIFIER_LENGTH"]
