"""Tests for PageCreateForm and PageEditForm."""
from __future__ import annotations

import pytest


def _create_data(**overrides):
    base = {
        "title": "About Us",
        "slug": "about-us",
        "navigation_title": "About",
        "summary": "Our story.",
        "template": "page",
        "seo_title": "",
        "meta_description": "",
        "canonical_url": "",
        "robots_index": True,
        "robots_follow": True,
        "social_title": "",
        "social_description": "",
        "social_image": "",
        "body": "# About\n\nContent here.",
        "intended_status": "draft",
        "change_description": "Initial page",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# PageCreateForm — valid submissions
# ---------------------------------------------------------------------------

def test_create_form_valid_minimal():
    from cauldron_admin_content.forms import PageCreateForm
    form = PageCreateForm(data={"title": "Home", "slug": "home", "intended_status": "draft"})
    assert form.is_valid(), form.errors


def test_create_form_valid_full():
    from cauldron_admin_content.forms import PageCreateForm
    form = PageCreateForm(data=_create_data())
    assert form.is_valid(), form.errors


def test_create_form_status_draft():
    from cauldron_admin_content.forms import PageCreateForm
    form = PageCreateForm(data=_create_data(intended_status="draft"))
    assert form.is_valid()
    assert form.cleaned_data["intended_status"] == "draft"


def test_create_form_status_published():
    from cauldron_admin_content.forms import PageCreateForm
    form = PageCreateForm(data=_create_data(intended_status="published"))
    assert form.is_valid()
    assert form.cleaned_data["intended_status"] == "published"


# ---------------------------------------------------------------------------
# Automatic slug generation
# ---------------------------------------------------------------------------

def test_create_form_auto_slug_from_title():
    from cauldron_admin_content.forms import PageCreateForm
    form = PageCreateForm(data=_create_data(slug="", title="About Our Team"))
    assert form.is_valid(), form.errors
    assert form.cleaned_data["slug"] == "about-our-team"


def test_create_form_custom_slug_preserved():
    from cauldron_admin_content.forms import PageCreateForm
    form = PageCreateForm(data=_create_data(slug="custom-slug"))
    assert form.is_valid(), form.errors
    assert form.cleaned_data["slug"] == "custom-slug"


def test_create_form_slug_strips_whitespace():
    from cauldron_admin_content.forms import PageCreateForm
    form = PageCreateForm(data=_create_data(slug="  about-us  "))
    # Slug strip + pattern test
    assert form.is_valid(), form.errors
    assert form.cleaned_data["slug"] == "about-us"


# ---------------------------------------------------------------------------
# Slug validation
# ---------------------------------------------------------------------------

def test_unsafe_slug_uppercase_rejected():
    from cauldron_admin_content.forms import PageCreateForm
    form = PageCreateForm(data=_create_data(slug="About-Us"))
    assert not form.is_valid()
    assert "slug" in form.errors


def test_unsafe_slug_space_rejected():
    from cauldron_admin_content.forms import PageCreateForm
    form = PageCreateForm(data=_create_data(slug="about us"))
    assert not form.is_valid()
    assert "slug" in form.errors


def test_unsafe_slug_traversal_rejected():
    from cauldron_admin_content.forms import PageCreateForm
    form = PageCreateForm(data=_create_data(slug="../etc/passwd"))
    assert not form.is_valid()
    assert "slug" in form.errors


def test_unsafe_slug_slash_rejected():
    from cauldron_admin_content.forms import PageCreateForm
    form = PageCreateForm(data=_create_data(slug="about/us"))
    assert not form.is_valid()
    assert "slug" in form.errors


def test_unsafe_slug_leading_hyphen_rejected():
    from cauldron_admin_content.forms import PageCreateForm
    form = PageCreateForm(data=_create_data(slug="-about"))
    assert not form.is_valid()
    assert "slug" in form.errors


def test_unsafe_slug_trailing_hyphen_rejected():
    from cauldron_admin_content.forms import PageCreateForm
    form = PageCreateForm(data=_create_data(slug="about-"))
    assert not form.is_valid()
    assert "slug" in form.errors


def test_slug_with_numbers_valid():
    from cauldron_admin_content.forms import PageCreateForm
    form = PageCreateForm(data=_create_data(slug="page-2024"))
    assert form.is_valid(), form.errors


# ---------------------------------------------------------------------------
# Field length validation
# ---------------------------------------------------------------------------

def test_title_too_long():
    from cauldron_admin_content.forms import PageCreateForm
    form = PageCreateForm(data=_create_data(title="A" * 201))
    assert not form.is_valid()
    assert "title" in form.errors


def test_navigation_title_too_long():
    from cauldron_admin_content.forms import PageCreateForm
    form = PageCreateForm(data=_create_data(navigation_title="N" * 101))
    assert not form.is_valid()
    assert "navigation_title" in form.errors


def test_summary_too_long():
    from cauldron_admin_content.forms import PageCreateForm
    form = PageCreateForm(data=_create_data(summary="S" * 501))
    assert not form.is_valid()
    assert "summary" in form.errors


def test_seo_title_too_long():
    from cauldron_admin_content.forms import PageCreateForm
    form = PageCreateForm(data=_create_data(seo_title="S" * 71))
    assert not form.is_valid()
    assert "seo_title" in form.errors


def test_meta_description_too_long():
    from cauldron_admin_content.forms import PageCreateForm
    form = PageCreateForm(data=_create_data(meta_description="D" * 321))
    assert not form.is_valid()
    assert "meta_description" in form.errors


def test_body_too_large():
    from cauldron_admin_content.forms import PageCreateForm
    # 600 KB > 500 KB limit
    huge_body = "x" * 600_000
    form = PageCreateForm(data=_create_data(body=huge_body))
    assert not form.is_valid()
    assert "body" in form.errors


def test_change_description_too_long():
    from cauldron_admin_content.forms import PageCreateForm
    form = PageCreateForm(data=_create_data(change_description="D" * 2001))
    assert not form.is_valid()
    assert "change_description" in form.errors


# ---------------------------------------------------------------------------
# Default field values
# ---------------------------------------------------------------------------

def test_default_robots_index_true():
    from cauldron_admin_content.forms import PageCreateForm
    form = PageCreateForm(data=_create_data())
    assert form.is_valid(), form.errors
    # Checkbox checked in data (True value)
    assert form.cleaned_data["robots_index"] is True


def test_default_template_page():
    from cauldron_admin_content.forms import PageCreateForm
    form = PageCreateForm(data=_create_data(template=""))
    assert form.is_valid(), form.errors
    assert form.cleaned_data["template"] == "page"


def test_robots_index_false_when_unchecked():
    from cauldron_admin_content.forms import PageCreateForm
    data = _create_data()
    data.pop("robots_index", None)
    form = PageCreateForm(data=data)
    assert form.is_valid(), form.errors
    assert form.cleaned_data["robots_index"] is False


# ---------------------------------------------------------------------------
# Form does not expose internal fields
# ---------------------------------------------------------------------------

def test_create_form_has_no_provider_field():
    from cauldron_admin_content.forms import PageCreateForm
    form = PageCreateForm()
    assert "provider_name" not in form.fields
    assert "provider" not in form.fields


def test_create_form_has_no_collection_field():
    from cauldron_admin_content.forms import PageCreateForm
    form = PageCreateForm()
    assert "collection" not in form.fields


def test_create_form_has_no_schema_field():
    from cauldron_admin_content.forms import PageCreateForm
    form = PageCreateForm()
    assert "schema" not in form.fields


def test_create_form_has_no_expected_hash_field():
    from cauldron_admin_content.forms import PageCreateForm
    form = PageCreateForm()
    assert "expected_hash" not in form.fields


def test_create_form_has_no_force_field():
    from cauldron_admin_content.forms import PageCreateForm
    form = PageCreateForm()
    assert "force" not in form.fields


def test_create_form_has_no_structured_data_field():
    from cauldron_admin_content.forms import PageCreateForm
    form = PageCreateForm()
    assert "structured_data" not in form.fields


# ---------------------------------------------------------------------------
# PageEditForm — slug is not a field
# ---------------------------------------------------------------------------

def test_edit_form_no_slug_field():
    from cauldron_admin_content.forms import PageEditForm
    form = PageEditForm()
    assert "slug" not in form.fields


def test_edit_form_valid():
    from cauldron_admin_content.forms import PageEditForm
    data = {k: v for k, v in _create_data().items() if k != "slug"}
    form = PageEditForm(data=data)
    assert form.is_valid(), form.errors


def test_edit_form_title_required():
    from cauldron_admin_content.forms import PageEditForm
    data = {k: v for k, v in _create_data().items() if k != "slug"}
    data["title"] = ""
    form = PageEditForm(data=data)
    assert not form.is_valid()
    assert "title" in form.errors
