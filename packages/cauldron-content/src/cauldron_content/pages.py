"""Standard page-content contract for Cauldron.

Both the Django admin UI and future AI tools must call build_page_operation()
rather than constructing divergent operation dictionaries. This module has
no Django dependencies so any Python caller can import it.
"""
from __future__ import annotations

from typing import Any

PAGE_COLLECTION = "pages"
PAGE_SCHEMA = "page"

_VALID_KINDS = frozenset({"create", "update"})


def build_page_operation(
    *,
    kind: str,
    item_id: str,
    slug: str,
    status: str,
    title: str,
    body: str,
    expected_hash: str = "",
    navigation_title: str = "",
    summary: str = "",
    seo_title: str = "",
    meta_description: str = "",
    canonical_url: str = "",
    robots_index: bool = True,
    robots_follow: bool = True,
    social_title: str = "",
    social_description: str = "",
    social_image: str = "",
    template: str = "page",
) -> dict[str, Any]:
    """Return a ContentOperationService-compatible operation dict for a page.

    Collection is always PAGE_COLLECTION; schema is always PAGE_SCHEMA.
    Provider selection is left to content routing — callers must not set it here.
    expected_hash is only included for update operations when non-empty.
    Both Django page forms and future AI page tools must call this builder.
    """
    if kind not in _VALID_KINDS:
        raise ValueError(
            f"Invalid kind {kind!r}. Must be one of {sorted(_VALID_KINDS)}."
        )

    op: dict[str, Any] = {
        "kind": kind,
        "collection": PAGE_COLLECTION,
        "schema": PAGE_SCHEMA,
        "item_id": item_id,
        "slug": slug,
        "status": status,
        "body": body,
        "data": {
            "title": title,
            "navigation_title": navigation_title,
            "summary": summary,
            "seo_title": seo_title,
            "meta_description": meta_description,
            "canonical_url": canonical_url,
            "robots_index": robots_index,
            "robots_follow": robots_follow,
            "social_title": social_title,
            "social_description": social_description,
            "social_image": social_image,
            "template": template,
        },
    }

    if kind == "update" and expected_hash:
        op["expected_hash"] = expected_hash

    return op


__all__ = ["PAGE_COLLECTION", "PAGE_SCHEMA", "build_page_operation"]
