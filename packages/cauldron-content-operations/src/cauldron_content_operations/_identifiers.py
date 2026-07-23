"""Identifier segment validation used across the content control plane.

Reject values that could be interpreted as path components, absolute paths,
traversal, Windows drive prefixes, or embedded control bytes. Applied to
collection and slug segments before any workspace or disk I/O.
"""
from __future__ import annotations


def _has_control_bytes(value: str) -> bool:
    for ch in value:
        if ord(ch) < 0x20:
            return True
    return False


def validate_identifier_segment(value: str, field_name: str) -> None:
    r"""Raise ``ValueError`` if ``value`` is not a safe identifier segment.

    Rules:
      * Must be a non-empty string.
      * Must not contain forward or back slashes.
      * Must not start with ``/`` or ``\`` (absolute paths).
      * Must not contain a ``..`` component or be exactly ``.`` / ``..``.
      * Must not carry a Windows drive prefix (e.g. ``C:``).
      * Must not contain NUL or other control bytes (< 0x20).
    """
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string, got {type(value).__name__}.")
    if value == "":
        raise ValueError(f"{field_name} must not be empty.")
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
    # Any component that is exactly ".." must be rejected. The slash check
    # above already covers embedded separators, but keep this as an extra
    # defence for future paths.
    for part in value.split("/"):
        if part == "..":
            raise ValueError(
                f"{field_name} must not contain '..' component: {value!r}."
            )
    if len(value) >= 2 and value[1] == ":" and value[0].isalpha():
        raise ValueError(
            f"{field_name} must not carry a Windows drive prefix: {value!r}."
        )
    if _has_control_bytes(value):
        raise ValueError(
            f"{field_name} must not contain control characters."
        )


__all__ = ["validate_identifier_segment"]
