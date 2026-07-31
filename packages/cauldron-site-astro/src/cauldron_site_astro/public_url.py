"""Astro implementation of SitePublicUrlProvider.

Maps content items in the pages collection to their live Astro-built URLs.
The homepage item is a special case: Astro builds it at / (not /homepage/).
All other pages are routed at /{slug}/.
"""
from __future__ import annotations


class AstroPublicUrlProvider:
    def get_public_url(self, *, item_id: str, slug: str, collection: str) -> str | None:
        from cauldron_content.pages import PAGE_COLLECTION
        from cauldron_content.homepage import HOMEPAGE_ITEM_ID, HOMEPAGE_ROUTE
        if collection != PAGE_COLLECTION:
            return None
        if item_id == HOMEPAGE_ITEM_ID:
            return HOMEPAGE_ROUTE
        return f"/{slug}/"
