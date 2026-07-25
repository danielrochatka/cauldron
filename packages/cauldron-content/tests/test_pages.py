"""Tests for the standard page content contract."""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

def test_page_collection_constant():
    from cauldron_content.pages import PAGE_COLLECTION
    assert PAGE_COLLECTION == "pages"


def test_page_schema_constant():
    from cauldron_content.pages import PAGE_SCHEMA
    assert PAGE_SCHEMA == "page"


# ---------------------------------------------------------------------------
# Create operation
# ---------------------------------------------------------------------------

def test_create_operation_minimal():
    from cauldron_content.pages import build_page_operation, PAGE_COLLECTION, PAGE_SCHEMA
    op = build_page_operation(
        kind="create",
        item_id="abc-123",
        slug="about",
        status="draft",
        title="About Us",
        body="# About\n\nContent here.",
    )
    assert op["kind"] == "create"
    assert op["collection"] == PAGE_COLLECTION
    assert op["schema"] == PAGE_SCHEMA
    assert op["item_id"] == "abc-123"
    assert op["slug"] == "about"
    assert op["status"] == "draft"
    assert op["body"] == "# About\n\nContent here."


def test_create_operation_fixed_collection():
    from cauldron_content.pages import build_page_operation
    op = build_page_operation(kind="create", item_id="x", slug="x", status="draft", title="X", body="")
    assert op["collection"] == "pages"


def test_create_operation_fixed_schema():
    from cauldron_content.pages import build_page_operation
    op = build_page_operation(kind="create", item_id="x", slug="x", status="draft", title="X", body="")
    assert op["schema"] == "page"


def test_create_operation_no_expected_hash():
    from cauldron_content.pages import build_page_operation
    op = build_page_operation(kind="create", item_id="x", slug="x", status="draft", title="X", body="")
    assert "expected_hash" not in op


def test_create_operation_expected_hash_not_included_even_if_provided():
    """expected_hash is only meaningful for updates and must not appear on creates."""
    from cauldron_content.pages import build_page_operation
    op = build_page_operation(
        kind="create", item_id="x", slug="x", status="draft", title="X", body="",
        expected_hash="abc123",
    )
    assert "expected_hash" not in op


# ---------------------------------------------------------------------------
# Update operation
# ---------------------------------------------------------------------------

def test_update_operation_includes_expected_hash():
    from cauldron_content.pages import build_page_operation
    op = build_page_operation(
        kind="update",
        item_id="abc",
        slug="about",
        status="draft",
        title="About",
        body="Content.",
        expected_hash="deadbeef" * 8,
    )
    assert op["kind"] == "update"
    assert op["expected_hash"] == "deadbeef" * 8


def test_update_operation_empty_hash_omitted():
    from cauldron_content.pages import build_page_operation
    op = build_page_operation(
        kind="update", item_id="x", slug="x", status="draft", title="X", body="",
        expected_hash="",
    )
    assert "expected_hash" not in op


# ---------------------------------------------------------------------------
# Metadata mapping
# ---------------------------------------------------------------------------

def test_metadata_all_fields():
    from cauldron_content.pages import build_page_operation
    op = build_page_operation(
        kind="create",
        item_id="pg-1",
        slug="about-us",
        status="published",
        title="About Us",
        body="# About\n\nContent.",
        navigation_title="About",
        summary="Learn about us.",
        seo_title="About Our Org",
        meta_description="We do great things.",
        canonical_url="https://example.com/about-us",
        robots_index=True,
        robots_follow=False,
        social_title="About",
        social_description="Share text",
        social_image="https://example.com/img.png",
        template="wide",
    )
    data = op["data"]
    assert data["title"] == "About Us"
    assert data["navigation_title"] == "About"
    assert data["summary"] == "Learn about us."
    assert data["seo_title"] == "About Our Org"
    assert data["meta_description"] == "We do great things."
    assert data["canonical_url"] == "https://example.com/about-us"
    assert data["robots_index"] is True
    assert data["robots_follow"] is False
    assert data["social_title"] == "About"
    assert data["social_description"] == "Share text"
    assert data["social_image"] == "https://example.com/img.png"
    assert data["template"] == "wide"


def test_robots_defaults_are_true():
    from cauldron_content.pages import build_page_operation
    op = build_page_operation(kind="create", item_id="x", slug="x", status="draft", title="X", body="")
    assert op["data"]["robots_index"] is True
    assert op["data"]["robots_follow"] is True


def test_template_default():
    from cauldron_content.pages import build_page_operation
    op = build_page_operation(kind="create", item_id="x", slug="x", status="draft", title="X", body="")
    assert op["data"]["template"] == "page"


def test_body_in_operation_not_in_data():
    from cauldron_content.pages import build_page_operation
    op = build_page_operation(kind="create", item_id="x", slug="x", status="draft", title="X", body="## Hello")
    assert op["body"] == "## Hello"
    assert "body" not in op["data"]


# ---------------------------------------------------------------------------
# Invalid kind
# ---------------------------------------------------------------------------

def test_invalid_kind_raises():
    from cauldron_content.pages import build_page_operation
    with pytest.raises(ValueError, match="Invalid kind"):
        build_page_operation(kind="delete", item_id="x", slug="x", status="draft", title="X", body="")


def test_invalid_kind_delete_rejected():
    from cauldron_content.pages import build_page_operation
    with pytest.raises(ValueError):
        build_page_operation(kind="delete", item_id="x", slug="x", status="draft", title="X", body="")


def test_invalid_kind_garbage():
    from cauldron_content.pages import build_page_operation
    with pytest.raises(ValueError):
        build_page_operation(kind="upsert", item_id="x", slug="x", status="draft", title="X", body="")


# ---------------------------------------------------------------------------
# No Django / provider / AI-specific fields
# ---------------------------------------------------------------------------

def test_no_provider_in_operation():
    from cauldron_content.pages import build_page_operation
    op = build_page_operation(kind="create", item_id="x", slug="x", status="draft", title="X", body="")
    assert "provider" not in op
    assert "provider_name" not in op


def test_no_force_in_operation():
    from cauldron_content.pages import build_page_operation
    op = build_page_operation(kind="create", item_id="x", slug="x", status="draft", title="X", body="")
    assert "force" not in op


def test_operation_is_plain_dict():
    from cauldron_content.pages import build_page_operation
    op = build_page_operation(kind="create", item_id="x", slug="x", status="draft", title="X", body="")
    assert isinstance(op, dict)
    assert isinstance(op["data"], dict)


# ---------------------------------------------------------------------------
# Shared human/AI contract: both callers produce identical output
# ---------------------------------------------------------------------------

def test_human_and_ai_produce_identical_operation():
    """Contract test: the same build_page_operation() call from any Python context
    produces identical output. Neither path contains UI- or AI-specific data."""
    from cauldron_content.pages import build_page_operation

    kwargs = dict(
        kind="create",
        item_id="shared-id-123",
        slug="about-us",
        status="draft",
        title="About Us",
        body="# About\n\nWelcome.",
        navigation_title="About",
        summary="Our story.",
        seo_title="About Our Org",
        meta_description="Learn more.",
        canonical_url="",
        robots_index=True,
        robots_follow=True,
        social_title="",
        social_description="",
        social_image="",
        template="page",
    )

    # Simulate manual (Django form) caller
    human_op = build_page_operation(**kwargs)

    # Simulate future AI caller with same inputs
    ai_op = build_page_operation(**kwargs)

    assert human_op == ai_op

    # Verify neutral fields: no UI or AI specifics
    for key in ("provider", "provider_name", "force", "ai_prompt", "ai_model"):
        assert key not in human_op
        assert key not in ai_op
