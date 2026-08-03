"""Authoritative execution-budget constants for the Admin AI service.

Isolated in a leaf module so both service_factory and tools can import from
here without creating a circular dependency between them.
"""
from __future__ import annotations

# Raised from 4096 → 128 KB (argument_bytes) and 8192 → 256 KB (result_bytes)
# to accommodate content workflow payloads (long article bodies, schema
# responses).  See commit af38490 for full rationale.
DEFAULT_MAX_ARGUMENT_BYTES: int = 131072
DEFAULT_MAX_RESULT_BYTES: int = 262144
