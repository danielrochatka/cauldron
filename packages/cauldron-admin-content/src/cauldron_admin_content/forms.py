"""Content proposal form for Django Admin."""
from __future__ import annotations

import json

from django import forms


class ContentProposalForm(forms.Form):
    """Minimal generic form for creating a content proposal."""

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
