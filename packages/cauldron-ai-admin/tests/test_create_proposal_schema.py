"""Tests for the strict operation-schema used by content.create_proposal."""
from __future__ import annotations

import pytest

from cauldron_ai_admin.builtin_tools import _CREATE_PROPOSAL_SCHEMA
from cauldron_ai_admin.tools import (
    ToolArgumentValidationError,
    validate_tool_arguments,
)


def test_create_proposal_operation_requires_kind_and_collection():
    with pytest.raises(ToolArgumentValidationError):
        validate_tool_arguments(_CREATE_PROPOSAL_SCHEMA, {
            "operations": [{"collection": "pages"}],
        })


def test_create_proposal_operation_kind_enum_enforced():
    with pytest.raises(ToolArgumentValidationError):
        validate_tool_arguments(_CREATE_PROPOSAL_SCHEMA, {
            "operations": [{"kind": "invent", "collection": "pages"}],
        })


def test_create_proposal_operation_additional_properties_rejected():
    with pytest.raises(ToolArgumentValidationError):
        validate_tool_arguments(_CREATE_PROPOSAL_SCHEMA, {
            "operations": [
                {"kind": "create", "collection": "pages", "sneaky": "x"},
            ],
        })


def test_create_proposal_valid_minimal_shape():
    validate_tool_arguments(_CREATE_PROPOSAL_SCHEMA, {
        "operations": [{"kind": "create", "collection": "pages"}],
    })


def test_create_proposal_valid_with_all_fields():
    validate_tool_arguments(_CREATE_PROPOSAL_SCHEMA, {
        "operations": [
            {
                "kind": "update",
                "collection": "pages",
                "item_id": "home",
                "slug": "home",
                "status": "published",
                "schema": "page",
                "data": {"title": "New title"},
                "body": "body text",
                "expected_hash": "abc",
                "provider": "flatfile",
            }
        ],
        "idempotency_key": "k",
        "description": "d",
        "provider_name": "flatfile",
    })


def test_create_proposal_requires_non_empty_operations():
    with pytest.raises(ToolArgumentValidationError):
        validate_tool_arguments(_CREATE_PROPOSAL_SCHEMA, {"operations": []})
