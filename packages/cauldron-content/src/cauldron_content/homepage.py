"""Homepage singleton contract for Cauldron CMS.

The Homepage is a reserved singleton in the pages collection. These constants
are the single source of truth — do not scatter "homepage" string literals
across views, AI tools, build code, or templates.
"""
from __future__ import annotations

from cauldron_content.pages import PAGE_COLLECTION, PAGE_SCHEMA, build_page_operation

HOMEPAGE_ITEM_ID = "homepage"
HOMEPAGE_COLLECTION = PAGE_COLLECTION
HOMEPAGE_SCHEMA = PAGE_SCHEMA
HOMEPAGE_TEMPLATE = "homepage"
HOMEPAGE_ROUTE = "/"


def build_homepage_operation(
    *,
    kind: str,
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
) -> dict:
    """Return a ContentOperationService operation dict for the Homepage.

    Item ID, slug, collection, schema, and template are fixed by the singleton
    contract and cannot be overridden by callers.
    """
    return build_page_operation(
        kind=kind,
        item_id=HOMEPAGE_ITEM_ID,
        slug=HOMEPAGE_ITEM_ID,
        status=status,
        title=title,
        body=body,
        expected_hash=expected_hash,
        navigation_title=navigation_title,
        summary=summary,
        seo_title=seo_title,
        meta_description=meta_description,
        canonical_url=canonical_url,
        robots_index=robots_index,
        robots_follow=robots_follow,
        social_title=social_title,
        social_description=social_description,
        social_image=social_image,
        template=HOMEPAGE_TEMPLATE,
    )


__all__ = [
    "HOMEPAGE_ITEM_ID",
    "HOMEPAGE_COLLECTION",
    "HOMEPAGE_SCHEMA",
    "HOMEPAGE_TEMPLATE",
    "HOMEPAGE_ROUTE",
    "build_homepage_operation",
]
