"""Identifier segment validation used across the content control plane.

This module is a thin re-export of the canonical implementation in
``cauldron_content._identifiers`` so all packages share a single validator.
"""
from __future__ import annotations

from cauldron_content._identifiers import (
    MAX_IDENTIFIER_LENGTH,
    validate_identifier_segment,
)


__all__ = ["validate_identifier_segment", "MAX_IDENTIFIER_LENGTH"]
