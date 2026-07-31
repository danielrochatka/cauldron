"""Tests for AstroPublicUrlProvider — lives in the package that owns the implementation.

Covers:
- Homepage item → returns "/"
- Regular page → returns "/{slug}/"
- Non-pages collection → returns None
"""
from __future__ import annotations

import pytest


def test_homepage_item_returns_root():
    from cauldron_site_astro.public_url import AstroPublicUrlProvider
    from cauldron_content.pages import PAGE_COLLECTION
    from cauldron_content.homepage import HOMEPAGE_ITEM_ID, HOMEPAGE_ROUTE

    provider = AstroPublicUrlProvider()
    result = provider.get_public_url(
        item_id=HOMEPAGE_ITEM_ID,
        slug="homepage",
        collection=PAGE_COLLECTION,
    )
    assert result == HOMEPAGE_ROUTE
    assert result == "/"


def test_regular_page_returns_slug_url():
    from cauldron_site_astro.public_url import AstroPublicUrlProvider
    from cauldron_content.pages import PAGE_COLLECTION

    provider = AstroPublicUrlProvider()
    result = provider.get_public_url(
        item_id="some-id",
        slug="my-article",
        collection=PAGE_COLLECTION,
    )
    assert result == "/my-article/"


def test_non_pages_collection_returns_none():
    from cauldron_site_astro.public_url import AstroPublicUrlProvider

    provider = AstroPublicUrlProvider()
    result = provider.get_public_url(
        item_id="some-id",
        slug="my-doc",
        collection="docs",
    )
    assert result is None


def test_non_pages_collection_authors_returns_none():
    from cauldron_site_astro.public_url import AstroPublicUrlProvider

    provider = AstroPublicUrlProvider()
    result = provider.get_public_url(
        item_id="author-1",
        slug="jane-doe",
        collection="authors",
    )
    assert result is None


def test_regular_page_slug_with_hyphens():
    from cauldron_site_astro.public_url import AstroPublicUrlProvider
    from cauldron_content.pages import PAGE_COLLECTION

    provider = AstroPublicUrlProvider()
    result = provider.get_public_url(
        item_id="page-xyz",
        slug="getting-started-guide",
        collection=PAGE_COLLECTION,
    )
    assert result == "/getting-started-guide/"
