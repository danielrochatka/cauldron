"""Identifier segment validation used by the flat-file reversible adapter.

Re-exports from the public ``cauldron_content.contracts`` API so the workspace
package shares a single validator with the rest of the control plane without
depending on private modules.
"""
from __future__ import annotations

from cauldron_content.contracts import (
    MAX_IDENTIFIER_LENGTH,
    validate_identifier_segment,
)


__all__ = ["validate_identifier_segment", "MAX_IDENTIFIER_LENGTH"]
