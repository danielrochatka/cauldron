"""Identifier segment validation used by the flat-file reversible adapter.

Thin re-export of ``cauldron_content._identifiers.validate_identifier_segment``
so the workspace package shares a single validator with the rest of the
control plane.
"""
from __future__ import annotations

from cauldron_content._identifiers import (
    MAX_IDENTIFIER_LENGTH,
    validate_identifier_segment,
)


__all__ = ["validate_identifier_segment", "MAX_IDENTIFIER_LENGTH"]
