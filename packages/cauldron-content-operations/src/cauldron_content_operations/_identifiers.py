"""Identifier segment validation used across the content control plane.

Re-exports from the public ``cauldron_content.contracts`` API rather than the
private ``_identifiers`` module so this package does not access internals.
"""
from __future__ import annotations

from cauldron_content.contracts import (
    MAX_IDENTIFIER_LENGTH,
    validate_identifier_segment,
)


__all__ = ["validate_identifier_segment", "MAX_IDENTIFIER_LENGTH"]
