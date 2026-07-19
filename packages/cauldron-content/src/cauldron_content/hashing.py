"""Canonical content hash algorithm shared between Python and TypeScript."""
from __future__ import annotations

import hashlib
import json
from typing import Any

_RESERVED_FIELDS = frozenset({"id", "slug", "status", "schema"})


def normalize_body(body: str) -> str:
    """Normalize line endings. Returns empty string for empty inputs.

    - Replaces CRLF and lone CR with LF.
    - Ensures a single trailing newline if the body is non-empty.
    """
    if not body:
        return ""
    text = body.replace("\r\n", "\n").replace("\r", "\n")
    if text and not text.endswith("\n"):
        text += "\n"
    return text


def _sort_deep(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _sort_deep(v) for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        return [_sort_deep(v) for v in obj]
    return obj


def compute_content_hash(
    item_id: str,
    collection: str,
    slug: str,
    status: str,
    schema: str,
    data: dict[str, Any],
    body: str,
) -> str:
    """Canonical SHA-256 hash. MUST match the TypeScript implementation exactly.

    Keys are sorted alphabetically (body, collection, data, id, schema, slug, status),
    JSON is emitted without whitespace, encoded as UTF-8. Result is lowercase hex.
    """
    canonical = {
        "body": normalize_body(body),
        "collection": collection,
        "data": _sort_deep(dict(data)),
        "id": item_id,
        "schema": schema,
        "slug": slug,
        "status": status,
    }
    serialized = json.dumps(
        canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
