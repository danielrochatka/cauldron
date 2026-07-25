"""Content proposal form and dedicated page forms for Cauldron Admin."""
from __future__ import annotations

import json
import re

from django import forms
from django.utils.text import slugify


# ---------------------------------------------------------------------------
# Generic proposal form (advanced / technical interface)
# ---------------------------------------------------------------------------

class ContentProposalForm(forms.Form):
    """Minimal generic form for creating a content proposal. Advanced interface."""

    OPERATION_CHOICES = [
        ("create", "Create"),
        ("update", "Update"),
        ("delete", "Delete"),
    ]

    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("published", "Published"),
    ]

    collection = forms.CharField(max_length=128, help_text="The content collection name.")
    operation = forms.ChoiceField(choices=OPERATION_CHOICES)
    item_id = forms.CharField(max_length=256, help_text="Stable content item ID.")
    slug = forms.CharField(max_length=256, required=False, help_text="URL-safe slug.")
    status = forms.ChoiceField(choices=STATUS_CHOICES, initial="draft")
    schema = forms.CharField(max_length=128, required=False, help_text="Schema name.")
    structured_data = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 10}),
        required=False,
        help_text="Structured data as JSON.",
    )
    body = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 20}),
        required=False,
        help_text="Optional Markdown body.",
    )
    expected_hash = forms.CharField(
        max_length=64,
        required=False,
        help_text="Current content hash for update/delete (optimistic concurrency).",
    )
    provider_name = forms.CharField(max_length=128, required=False, help_text="Provider name (optional).")
    description = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}),
        required=False,
        help_text="Description of this change.",
    )

    def clean_structured_data(self):
        value = self.cleaned_data.get("structured_data", "").strip()
        if not value:
            return {}
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise forms.ValidationError(f"Invalid JSON: {exc}")

    def to_operation(self) -> dict:
        data = self.cleaned_data
        return {
            "kind": data["operation"],
            "collection": data["collection"],
            "item_id": data["item_id"],
            "slug": data.get("slug", "") or data["item_id"],
            "status": data.get("status", "draft"),
            "schema": data.get("schema", ""),
            "data": data.get("structured_data") or {},
            "body": data.get("body", ""),
            "expected_hash": data.get("expected_hash", ""),
            "provider": data.get("provider_name", ""),
        }


# ---------------------------------------------------------------------------
# Page slug validation
# ---------------------------------------------------------------------------

_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MAX_BODY_BYTES = 500_000  # 500 KB
_MAX_DESCRIPTION = 2_000


def _validate_page_slug(value: str) -> None:
    """Raise ValidationError if slug does not match the page slug pattern."""
    if not _SLUG_PATTERN.match(value):
        raise forms.ValidationError(
            "Slug must contain only lowercase letters, digits, and hyphens "
            "(e.g. about-us). No spaces, uppercase letters, or special characters."
        )


# ---------------------------------------------------------------------------
# Shared page fields base form
# ---------------------------------------------------------------------------

class _PageBaseForm(forms.Form):
    """Common fields shared by PageCreateForm and PageEditForm."""

    # Page section
    title = forms.CharField(
        max_length=200,
        label="Title",
        help_text="Page title (1–200 characters, required).",
    )
    navigation_title = forms.CharField(
        max_length=100,
        required=False,
        label="Navigation title",
        help_text="Short label for navigation menus (optional, max 100 characters).",
    )
    summary = forms.CharField(
        max_length=500,
        required=False,
        label="Summary",
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Brief page description (optional, max 500 characters).",
    )
    template = forms.CharField(
        max_length=128,
        required=False,
        label="Template",
        help_text='Template name (default: "page").',
    )

    # Search metadata section
    seo_title = forms.CharField(
        max_length=70,
        required=False,
        label="SEO title",
        help_text="Override title for search engines (optional, max 70 characters).",
    )
    meta_description = forms.CharField(
        max_length=320,
        required=False,
        label="Meta description",
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Search result snippet (optional, max 320 characters).",
    )
    canonical_url = forms.CharField(
        max_length=2048,
        required=False,
        label="Canonical URL",
        help_text="Canonical URL override (optional).",
    )
    robots_index = forms.BooleanField(
        required=False,
        label="Allow search indexing",
        help_text="Allow search engines to index this page.",
    )
    robots_follow = forms.BooleanField(
        required=False,
        label="Allow link following",
        help_text="Allow search engines to follow links on this page.",
    )

    # Social metadata section
    social_title = forms.CharField(
        max_length=100,
        required=False,
        label="Social title",
        help_text="Title for social media sharing (optional, max 100 characters).",
    )
    social_description = forms.CharField(
        max_length=300,
        required=False,
        label="Social description",
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Description for social media sharing (optional, max 300 characters).",
    )
    social_image = forms.CharField(
        max_length=2048,
        required=False,
        label="Social image URL",
        help_text="Image URL for social media sharing (optional).",
    )

    # Content section
    body = forms.CharField(
        required=False,
        label="Markdown content",
        widget=forms.Textarea(attrs={
            "rows": 20,
            "class": "cui-body-editor",
            "placeholder": "# Page Title\n\nWrite your content here in Markdown format.",
        }),
        help_text=(
            "Page body written in Markdown. "
            "Use # for headings, **bold**, _italic_, and [link](url). "
            "Content is displayed as plain text until a renderer is configured."
        ),
    )

    # Workflow section
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("published", "Published after approval"),
    ]
    intended_status = forms.ChoiceField(
        choices=STATUS_CHOICES,
        initial="draft",
        label="Intended page status",
        help_text=(
            '"Published after approval" means the page will be set to published '
            "once the change request completes the full approval and application workflow."
        ),
    )
    change_description = forms.CharField(
        max_length=_MAX_DESCRIPTION,
        required=False,
        label="Change description",
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Optional description of what this change does.",
    )

    def _clean_str(self, field: str) -> str:
        return (self.cleaned_data.get(field) or "").strip()

    def clean_title(self):
        value = self._clean_str("title")
        if not value:
            raise forms.ValidationError("Title is required.")
        return value

    def clean_navigation_title(self):
        return self._clean_str("navigation_title")

    def clean_summary(self):
        return self._clean_str("summary")

    def clean_template(self):
        value = self._clean_str("template")
        return value or "page"

    def clean_seo_title(self):
        return self._clean_str("seo_title")

    def clean_meta_description(self):
        return self._clean_str("meta_description")

    def clean_canonical_url(self):
        return self._clean_str("canonical_url")

    def clean_social_title(self):
        return self._clean_str("social_title")

    def clean_social_description(self):
        return self._clean_str("social_description")

    def clean_social_image(self):
        return self._clean_str("social_image")

    def clean_body(self):
        value = self.cleaned_data.get("body") or ""
        if len(value.encode("utf-8")) > _MAX_BODY_BYTES:
            raise forms.ValidationError(
                f"Body is too large (max {_MAX_BODY_BYTES // 1000} KB)."
            )
        return value

    def clean_change_description(self):
        return self._clean_str("change_description")


# ---------------------------------------------------------------------------
# PageCreateForm
# ---------------------------------------------------------------------------

class PageCreateForm(_PageBaseForm):
    """Form for creating a new page through the standard page content pipeline.

    Does not expose provider, collection, schema, raw JSON, expected_hash, or force.
    Slug is editable; if blank it is auto-generated from the title.
    """

    slug = forms.CharField(
        max_length=128,
        required=False,
        label="URL slug",
        help_text=(
            "URL-friendly identifier (e.g. about-us). "
            "Leave blank to auto-generate from the title. "
            "Use only lowercase letters, digits, and hyphens."
        ),
    )

    field_order = [
        "title",
        "slug",
        "navigation_title",
        "summary",
        "template",
        "seo_title",
        "meta_description",
        "canonical_url",
        "robots_index",
        "robots_follow",
        "social_title",
        "social_description",
        "social_image",
        "body",
        "intended_status",
        "change_description",
    ]

    def clean_slug(self):
        value = self._clean_str("slug")
        if not value:
            title = self.cleaned_data.get("title", "")
            value = slugify(title)
        if not value:
            raise forms.ValidationError(
                "Could not generate a slug from the title. Please enter one manually."
            )
        _validate_page_slug(value)
        return value


# ---------------------------------------------------------------------------
# PageEditForm
# ---------------------------------------------------------------------------

class PageEditForm(_PageBaseForm):
    """Form for editing an existing page.

    Slug is read-only during Phase 1 and is not included as a form field;
    the view displays the current slug and preserves it from the loaded item.
    """

    field_order = [
        "title",
        "navigation_title",
        "summary",
        "template",
        "seo_title",
        "meta_description",
        "canonical_url",
        "robots_index",
        "robots_follow",
        "social_title",
        "social_description",
        "social_image",
        "body",
        "intended_status",
        "change_description",
    ]
