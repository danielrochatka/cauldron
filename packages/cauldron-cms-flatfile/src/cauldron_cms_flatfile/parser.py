"""Parse Markdown files with YAML front matter into ContentItems."""
from __future__ import annotations

from pathlib import Path

import frontmatter

from cauldron_content.contracts import ContentItem, ContentStatus
from cauldron_content.hashing import compute_content_hash, normalize_body

_RESERVED = frozenset({"id", "slug", "status", "schema"})


class ParseError(Exception):
    """Raised for malformed content files."""


def parse_content_file(path: Path, collection: str, provider: str) -> ContentItem:
    """Parse a Markdown file with YAML front matter into a ContentItem."""
    try:
        post = frontmatter.load(str(path), handler=frontmatter.YAMLHandler())
    except Exception as exc:
        raise ParseError(f"Failed to parse {path}: {exc}") from exc

    meta = post.metadata

    for field in ("id", "slug", "status", "schema"):
        if field not in meta:
            raise ParseError(f"Missing required field {field!r} in {path}")

    item_id = str(meta["id"])
    slug = str(meta["slug"])
    status_raw = str(meta["status"]).lower()
    try:
        status = ContentStatus(status_raw)
    except ValueError as exc:
        raise ParseError(f"Unknown status {status_raw!r} in {path}") from exc
    schema = str(meta["schema"])

    data = {k: v for k, v in meta.items() if k not in _RESERVED}

    body = normalize_body(post.content or "")
    h = compute_content_hash(item_id, collection, slug, status.value, schema, data, body)

    return ContentItem(
        id=item_id,
        collection=collection,
        slug=slug,
        status=status,
        schema=schema,
        data=data,
        body=body,
        hash=h,
        provider=provider,
        source_ref=str(path),
    )
