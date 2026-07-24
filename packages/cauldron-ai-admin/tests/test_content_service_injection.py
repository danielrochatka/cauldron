"""Tests for the injected content-service seam used by built-in tools."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.django_db

from cauldron_ai_admin.builtin_tools import (
    _handle_create_proposal,
    _handle_get_item,
    _handle_list_collections,
    _handle_list_items,
    _handle_preview_change_request,
)
from cauldron_ai_admin.tools import AdminAIToolContext, AdminAIToolError, AdminAIToolResult


def _ctx(content_service=None, deadline=None):
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user, _ = User.objects.get_or_create(username="ctxuser")
    return AdminAIToolContext(
        actor=user,
        run_id="r",
        correlation_id="c",
        content_service=content_service,
        deadline=deadline,
    )


def test_list_collections_returns_service_unavailable_when_content_service_none():
    ctx = _ctx(content_service=None)
    result = _handle_list_collections(ctx)
    assert isinstance(result, AdminAIToolError)
    assert result.error_code == "tool.service_unavailable"


def test_list_items_returns_service_unavailable_when_content_service_none():
    ctx = _ctx(content_service=None)
    result = _handle_list_items(ctx, collection="pages")
    assert isinstance(result, AdminAIToolError)
    assert result.error_code == "tool.service_unavailable"


def test_get_item_returns_service_unavailable_when_content_service_none():
    ctx = _ctx(content_service=None)
    result = _handle_get_item(ctx, collection="pages", item_id="home")
    assert isinstance(result, AdminAIToolError)
    assert result.error_code == "tool.service_unavailable"


def test_create_proposal_returns_service_unavailable_when_content_service_none():
    ctx = _ctx(
        content_service=None,
        deadline=datetime.now(tz=timezone.utc) + timedelta(seconds=10),
    )
    result = _handle_create_proposal(ctx, operations=[{"kind": "create"}])
    assert isinstance(result, AdminAIToolError)
    assert result.error_code == "tool.service_unavailable"


# --------------------------------------------------------------- preview tool


def test_preview_change_request_calls_get_preview_and_never_mutates():
    fake_service = MagicMock(spec=["get_preview"])
    op = MagicMock()
    op.operation_type = "create"
    op.collection = "pages"
    op.item_id = "home"
    op.provider = "flatfile"
    op.diff_summary = "no change"
    op.has_conflict = False

    preview = MagicMock()
    preview.request_id = "cs-42"
    preview.operations = (op,)
    fake_service.get_preview.return_value = preview

    ctx = _ctx(content_service=fake_service)
    result = _handle_preview_change_request(ctx, cs_id="cs-42")
    assert isinstance(result, AdminAIToolResult)
    assert result.data["cs_id"] == "cs-42"
    assert result.data["operations"][0]["collection"] == "pages"
    fake_service.get_preview.assert_called_once()
    # Any mutation-shaped attribute would fail because spec restricts.
    assert not hasattr(fake_service, "create_change_request")
    assert not hasattr(fake_service, "apply_change_request")


def test_preview_change_request_not_found():
    fake_service = MagicMock(spec=["get_preview"])
    fake_service.get_preview.return_value = None
    ctx = _ctx(content_service=fake_service)
    result = _handle_preview_change_request(ctx, cs_id="missing")
    assert isinstance(result, AdminAIToolResult)
    assert result.data == {"found": False}


def test_preview_service_unavailable():
    ctx = _ctx(content_service=None)
    result = _handle_preview_change_request(ctx, cs_id="whatever")
    assert isinstance(result, AdminAIToolError)
    assert result.error_code == "tool.service_unavailable"
