"""Tests for the built-in Admin AI tools."""
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.django_db

from cauldron_ai_admin.builtin_tools import (
    ALLOWED_CHECK_TAGS,
    BUILTIN_TOOL_NAMES,
    PROPOSAL_ALLOWED_METHODS,
    _handle_create_proposal,
    _handle_django_checks,
    _handle_get_item,
    _handle_list_collections,
    _handle_list_items,
    _handle_module_status,
    register_builtin_tools,
)
from cauldron_ai_admin.tools import (
    AdminAIToolContext,
    AdminAIToolError,
    AdminAIToolResult,
    get_tool_registry,
)


def _ctx(user=None, content_service=None):
    if user is None:
        from django.contrib.auth import get_user_model
        User = get_user_model()
        user, _ = User.objects.get_or_create(username="builtin-user")
    ctx = AdminAIToolContext(actor=user, run_id="r", correlation_id="c")
    if content_service is not None:
        object.__setattr__(ctx, "_content_service", content_service)
    return ctx, user


def test_register_builtin_tools_covers_all_six():
    register_builtin_tools()
    reg = get_tool_registry()
    for name in BUILTIN_TOOL_NAMES:
        entry = reg.get(name)
        assert entry is not None, f"Missing tool: {name}"


def test_register_builtin_tools_is_idempotent():
    register_builtin_tools()
    register_builtin_tools()  # second call must not raise
    reg = get_tool_registry()
    assert reg.get("content.list_collections") is not None


def test_list_collections_success():
    fake_service = MagicMock()
    fake_service.list_collections.return_value = ["pages", "posts"]
    ctx, _ = _ctx(content_service=fake_service)
    result = _handle_list_collections(ctx)
    assert isinstance(result, AdminAIToolResult)
    assert result.data == {"collections": [{"name": "pages"}, {"name": "posts"}]}
    fake_service.list_collections.assert_called_once()


def test_list_collections_rejects_extra_args():
    ctx, _ = _ctx(content_service=MagicMock())
    result = _handle_list_collections(ctx, extra="nope")
    assert isinstance(result, AdminAIToolError)
    assert result.error_code == "tool.invalid_arguments"


def test_list_items_paginates():
    fake_service = MagicMock()
    items = []
    for i in range(5):
        item = MagicMock()
        item.to_dict.return_value = {"id": f"i{i}"}
        items.append(item)
    fake_service.list_items.return_value = items
    ctx, _ = _ctx(content_service=fake_service)
    result = _handle_list_items(ctx, collection="pages", limit=2, offset=1)
    assert isinstance(result, AdminAIToolResult)
    assert result.data["total"] == 5
    assert result.data["offset"] == 1
    assert len(result.data["items"]) == 2
    assert result.data["items"][0] == {"id": "i1"}


def test_list_items_draft_requires_permission():
    ctx, user = _ctx(content_service=MagicMock())
    # User has no view_draft_content perm.
    result = _handle_list_items(
        ctx, collection="pages", include_drafts=True,
    )
    assert isinstance(result, AdminAIToolError)
    assert result.error_code == "tool.permission_denied"


def test_get_item_not_found_returns_success_with_flag():
    fake_service = MagicMock()
    fake_service.get_item.return_value = None
    ctx, _ = _ctx(content_service=fake_service)
    result = _handle_get_item(ctx, collection="pages", item_id="x")
    assert isinstance(result, AdminAIToolResult)
    assert result.data == {"found": False}


def test_get_item_success():
    fake_service = MagicMock()
    item = MagicMock()
    item.to_dict.return_value = {"id": "home", "title": "Home"}
    fake_service.get_item.return_value = item
    ctx, _ = _ctx(content_service=fake_service)
    result = _handle_get_item(ctx, collection="pages", item_id="home")
    assert isinstance(result, AdminAIToolResult)
    assert result.data == {"found": True, "item": {"id": "home", "title": "Home"}}


def test_get_item_draft_requires_permission():
    ctx, _ = _ctx(content_service=MagicMock())
    result = _handle_get_item(
        ctx, collection="pages", item_id="home", include_drafts=True,
    )
    assert isinstance(result, AdminAIToolError)
    assert result.error_code == "tool.permission_denied"


def test_create_proposal_calls_only_allowed_method():
    """PROPOSE tool must never call apply/validate/approve/reject/rollback."""
    fake_service = MagicMock()
    fake_service.create_change_request.return_value = MagicMock(
        ok=True, request_id="cs-99",
    )
    # Sanity: enforce our invariant on the service surface.
    assert PROPOSAL_ALLOWED_METHODS == frozenset({"create_change_request"})
    ctx, _ = _ctx(content_service=fake_service)
    result = _handle_create_proposal(
        ctx, operations=[{"kind": "create"}], provider_name="flatfile",
    )
    assert isinstance(result, AdminAIToolResult)
    assert result.data == {"cs_id": "cs-99", "status": "proposed"}
    fake_service.create_change_request.assert_called_once()
    # No forbidden calls happened.
    for banned in ("validate_change_request", "approve_change_request",
                   "reject_change_request", "apply_change_request",
                   "rollback_change_request"):
        assert not getattr(fake_service, banned).called, f"{banned} was called"


def test_create_proposal_does_not_touch_content_repository():
    """The proposal tool must never bypass the operations service.

    We assert the handler only touches ``svc.create_change_request`` and
    never any repository-shaped API.
    """
    fake_service = MagicMock(spec=["create_change_request"])
    fake_service.create_change_request.return_value = MagicMock(
        ok=True, request_id="cs-1",
    )
    ctx, _ = _ctx(content_service=fake_service)
    result = _handle_create_proposal(
        ctx, operations=[{"kind": "create"}],
    )
    assert isinstance(result, AdminAIToolResult)


def test_create_proposal_requires_operations():
    ctx, _ = _ctx(content_service=MagicMock())
    result = _handle_create_proposal(ctx, operations=[])
    assert isinstance(result, AdminAIToolError)
    assert result.error_code == "tool.invalid_arguments"


def test_create_proposal_propagates_service_error():
    fake_service = MagicMock()
    fake_service.create_change_request.return_value = MagicMock(
        ok=False,
        error=MagicMock(code="operations.invalid", message="bad"),
    )
    ctx, _ = _ctx(content_service=fake_service)
    result = _handle_create_proposal(ctx, operations=[{"kind": "create"}])
    assert isinstance(result, AdminAIToolError)
    assert result.error_code == "operations.invalid"


def test_django_checks_rejects_non_allowlisted_tags():
    ctx, _ = _ctx()
    result = _handle_django_checks(ctx, tags=["not-a-tag"])
    assert isinstance(result, AdminAIToolError)
    assert result.error_code == "tool.invalid_arguments"


def test_django_checks_uses_python_api_not_subprocess():
    """We must call django.core.checks.registry.run_checks, not spawn a process."""
    ctx, _ = _ctx()
    with patch("subprocess.run") as sub, \
         patch("subprocess.Popen") as sub_popen, \
         patch("django.core.checks.registry.registry.run_checks", return_value=[]) as run_checks:
        result = _handle_django_checks(ctx, tags=["security"])
    assert not sub.called
    assert not sub_popen.called
    assert run_checks.called
    assert isinstance(result, AdminAIToolResult)


def test_django_checks_bounded_finding_count():
    ctx, _ = _ctx()
    fake_msgs = [MagicMock(id=f"E{i}", msg="m", hint="", level=40) for i in range(60)]
    with patch(
        "django.core.checks.registry.registry.run_checks", return_value=fake_msgs,
    ):
        result = _handle_django_checks(ctx, tags=["security"])
    assert isinstance(result, AdminAIToolResult)
    assert len(result.data["findings"]) == 50
    assert result.data["truncated"] is True


def test_django_checks_default_no_tags_ok():
    """Empty tag list runs all checks."""
    ctx, _ = _ctx()
    with patch(
        "django.core.checks.registry.registry.run_checks", return_value=[],
    ) as run_checks:
        result = _handle_django_checks(ctx)
    assert isinstance(result, AdminAIToolResult)
    assert run_checks.called


def test_allowed_check_tags_stable():
    assert "security" in ALLOWED_CHECK_TAGS
    assert "database" in ALLOWED_CHECK_TAGS
    assert "models" in ALLOWED_CHECK_TAGS


def test_module_status_no_secrets_or_paths():
    ctx, _ = _ctx()
    result = _handle_module_status(ctx)
    assert isinstance(result, AdminAIToolResult)
    modules = result.data["modules"]
    # Serialise the payload and confirm no filesystem-looking paths leak.
    import json
    serialised = json.dumps(result.data)
    assert "/home" not in serialised
    assert "SECRET_KEY" not in serialised
    # Per the module_status contract each entry surfaces exactly these keys.
    for m in modules:
        assert set(m.keys()) == {
            "name", "capabilities", "dependencies",
            "status", "version", "health", "dependency_health",
        }
    # Top-level shape is stable too.
    assert set(result.data.keys()) == {
        "modules", "capabilities", "resolution_errors", "discovery_errors",
    }


def test_module_status_health_is_unknown_not_ok_for_active_modules():
    """Absent an explicit health signal the tool must NOT fabricate
    ``ok`` — the value has to be ``unknown``."""
    from unittest.mock import patch
    ctx, _ = _ctx()
    fake_registry = MagicMock()
    fake_registry.graph_info.return_value = [{
        "slug": "cauldron.example",
        "provides": ["ex.cap"],
        "requires": [],
        "deps": [],
        "active": True,
        "version": "1.0.0",
    }]
    fake_registry.lifecycle_errors.return_value = []
    fake_registry.capabilities.return_value = {"ex.cap": ["cauldron.example"]}
    fake_registry.dependency_graph.return_value = {"cauldron.example": []}
    fake_registry.errors.return_value = []
    fake_registry.discovery_errors.return_value = []
    with patch(
        "cauldron.modules.registry.registry", fake_registry,
    ):
        result = _handle_module_status(ctx)
    assert isinstance(result, AdminAIToolResult)
    assert result.data["modules"][0]["health"] == "unknown"
    assert result.data["modules"][0]["status"] == "active"


def test_module_status_reports_error_status_for_lifecycle_failures():
    """Modules with recorded lifecycle errors must surface ``status='error'``
    and ``health='degraded'``."""
    from unittest.mock import patch
    ctx, _ = _ctx()
    fake_registry = MagicMock()
    fake_registry.graph_info.return_value = [{
        "slug": "cauldron.broken",
        "provides": [],
        "requires": [],
        "deps": [],
        "active": True,
        "version": "1.0.0",
    }]
    fake_error = MagicMock()
    fake_error.module_slug = "cauldron.broken"
    fake_error.phase = "register"
    fake_registry.lifecycle_errors.return_value = [fake_error]
    fake_registry.capabilities.return_value = {}
    fake_registry.dependency_graph.return_value = {"cauldron.broken": []}
    fake_registry.errors.return_value = []
    fake_registry.discovery_errors.return_value = []
    with patch(
        "cauldron.modules.registry.registry", fake_registry,
    ):
        result = _handle_module_status(ctx)
    assert isinstance(result, AdminAIToolResult)
    entry = result.data["modules"][0]
    assert entry["status"] == "error"
    assert entry["health"] == "degraded"


def test_module_status_rejects_extra_args():
    ctx, _ = _ctx()
    result = _handle_module_status(ctx, unexpected=True)
    assert isinstance(result, AdminAIToolError)
    assert result.error_code == "tool.invalid_arguments"
