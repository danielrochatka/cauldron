"""Identifier segment validation used by the flat-file reversible adapter.

Mirrors ``cauldron_content_operations._identifiers.validate_identifier_segment``
so the workspace package can validate without depending on the operations
package. The two implementations are kept in sync deliberately.
"""
from __future__ import annotations


def validate_identifier_segment(value: str, field_name: str) -> None:
    """Raise ``ValueError`` if ``value`` is not a safe identifier segment.

    Rules mirror :func:`cauldron_content_operations._identifiers.validate_identifier_segment`.
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
        if ord(ch) < 0x20:
            raise ValueError(
                f"{field_name} must not contain control characters."
            )


__all__ = ["validate_identifier_segment"]
