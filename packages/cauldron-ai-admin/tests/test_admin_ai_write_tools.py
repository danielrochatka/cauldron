"""Tests for the Admin AI write-capability path.

Verifies that:
1. Authorized administrators receive the expected write/proposal tools.
2. Unauthorized users receive only permitted tools.
3. Content changes go through ContentOperationService.
4. Style changes use the module-owned UIStyleChangeService mutation path.
5. Disabled modules remove their tools from the effective registry.
6. Tool schemas sent to the provider include authorized mutation tools.
7. The diagnostic inventory stays within the configured result-size limit.
8. A representative request can inspect content and then propose a change
   rather than remaining read-only.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(username, perms=()):
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    User = get_user_model()
    user, _ = User.objects.get_or_create(username=username)
    for spec in perms:
        app_label, codename = spec.split(".", 1)
        try:
            perm = Permission.objects.get(
                codename=codename, content_type__app_label=app_label,
            )
            user.user_permissions.add(perm)
        except Permission.DoesNotExist:
            pass
    return User.objects.get(pk=user.pk)  # flush perm cache


def _ctx(user, content_service=None):
    from cauldron_ai_admin.tools import AdminAIToolContext
    ctx = AdminAIToolContext(actor=user, run_id="r1", correlation_id="c1")
    if content_service is not None:
        ctx.content_service = content_service
    return ctx


# ---------------------------------------------------------------------------
# 1. Authorized administrator receives expected write/proposal tools
# ---------------------------------------------------------------------------

def test_authorized_admin_receives_propose_tools():
    from cauldron_ai_admin.builtin_tools import register_builtin_tools
    from cauldron_ai_admin.tools import get_tool_registry

    register_builtin_tools()
    user = _make_user("write-admin", perms=[
        "cauldron_ai_admin.use_admin_ai",
        "cauldron_ai_admin.propose_ui_style_changes",
        "cauldron_ai_admin.view_ui_styles",
        "cauldron_content_operations.view_published_content",
        "cauldron_content_operations.propose_content_changes",
    ])

    registry = get_tool_registry()
    permitted = registry.list_for_actor(user)
    names = {d.name for d in permitted}

    assert "content.create_proposal" in names, (
        "content.create_proposal must be visible to a user with propose_content_changes"
    )
    assert "ui.styles.create_proposal" in names, (
        "ui.styles.create_proposal must be visible to a user with propose_ui_style_changes"
    )


# ---------------------------------------------------------------------------
# 2. Unauthorized user receives only permitted tools
# ---------------------------------------------------------------------------

def test_unauthorized_user_sees_only_read_tools():
    from cauldron_ai_admin.builtin_tools import register_builtin_tools
    from cauldron_ai_admin.tools import RiskLevel, get_tool_registry

    register_builtin_tools()
    # User has only use_admin_ai — no content or style permissions.
    user = _make_user("read-only-user", perms=["cauldron_ai_admin.use_admin_ai"])

    registry = get_tool_registry()
    permitted = registry.list_for_actor(user)
    names = {d.name for d in permitted}

    assert "content.create_proposal" not in names, (
        "content.create_proposal must not be visible without propose_content_changes"
    )
    assert "ui.styles.create_proposal" not in names, (
        "ui.styles.create_proposal must not be visible without propose_ui_style_changes"
    )
    # The tools they can see are all READ_ONLY
    for d in permitted:
        assert d.risk_level == RiskLevel.READ_ONLY, (
            f"{d.name} is not READ_ONLY but visible to unprivileged user"
        )


def test_anonymous_actor_sees_no_tools():
    from cauldron_ai_admin.tools import AdminAIToolRegistry, RiskLevel, AdminAIToolDefinition

    reg = AdminAIToolRegistry()
    reg.register(
        AdminAIToolDefinition(
            name="x.tool",
            version="1.0",
            description="test",
            argument_schema={"type": "object"},
            risk_level=RiskLevel.READ_ONLY,
            required_permission="cauldron_ai_admin.use_admin_ai",
            owning_module="cauldron.ai.admin",
        ),
        lambda ctx, **kw: None,
    )
    assert reg.list_for_actor(None) == []


# ---------------------------------------------------------------------------
# 3. Content changes use ContentOperationService
# ---------------------------------------------------------------------------

def test_content_changes_use_content_operation_service():
    from cauldron_ai_admin.builtin_tools import _handle_create_proposal
    from cauldron_ai_admin.tools import AdminAIToolResult

    fake_result = MagicMock()
    fake_result.ok = True
    fake_result.request_id = "cs-001"

    svc = MagicMock()
    svc.create_change_request.return_value = fake_result

    user = _make_user("content-proposer", perms=["cauldron_ai_admin.use_admin_ai"])
    ctx = _ctx(user, content_service=svc)

    result = _handle_create_proposal(
        ctx,
        operations=[{"kind": "update", "collection": "pages", "item_id": "home"}],
        description="Update the home page intro.",
    )

    assert isinstance(result, AdminAIToolResult)
    assert result.success is True
    # Must route through the service, never bypass it
    svc.create_change_request.assert_called_once()
    call_kwargs = svc.create_change_request.call_args.kwargs
    assert call_kwargs["user"] is user
    assert len(call_kwargs["operations"]) == 1


def test_content_proposal_not_applied_directly():
    """Content proposals return 'proposed' status, never 'applied'."""
    from cauldron_ai_admin.builtin_tools import _handle_create_proposal
    from cauldron_ai_admin.tools import AdminAIToolResult

    fake_result = MagicMock()
    fake_result.ok = True
    fake_result.request_id = "cs-002"

    svc = MagicMock()
    svc.create_change_request.return_value = fake_result

    user = _make_user("content-proposer2", perms=["cauldron_ai_admin.use_admin_ai"])
    ctx = _ctx(user, content_service=svc)

    result = _handle_create_proposal(
        ctx,
        operations=[{"kind": "create", "collection": "blog"}],
    )

    assert isinstance(result, AdminAIToolResult)
    assert result.data["status"] == "proposed"
    # Service method limited to create_change_request only — validate/apply must not be called
    svc.validate_change_request.assert_not_called()
    svc.apply_change_request.assert_not_called()


# ---------------------------------------------------------------------------
# 4. Style changes use module-owned mutation path
# ---------------------------------------------------------------------------

def test_style_changes_use_style_service():
    from cauldron_ai_admin.builtin_tools import _handle_ui_create_proposal
    from cauldron_ai_admin.tools import AdminAIToolResult

    fake_proposal = MagicMock()
    fake_proposal.request_id = "style-001"

    fake_svc = MagicMock()
    fake_svc.create_proposal.return_value = fake_proposal

    user = _make_user("style-proposer", perms=["cauldron_ai_admin.use_admin_ai"])
    ctx = _ctx(user)

    # The handler imports get_style_service lazily via `from .style_service import`;
    # patch the source module attribute so the import resolves to our fake.
    with patch("cauldron_ai_admin.style_service.get_style_service", return_value=fake_svc):
        result = _handle_ui_create_proposal(
            ctx,
            scope="admin",
            target_path="custom.css",
            proposed_content=":root { --color: red; }",
            description="Change primary colour to red.",
        )

    assert isinstance(result, AdminAIToolResult)
    assert result.success is True
    assert result.data["status"] == "proposed"
    fake_svc.create_proposal.assert_called_once()
    call_kwargs = fake_svc.create_proposal.call_args.kwargs
    assert call_kwargs["scope"] == "admin"
    assert call_kwargs["target_path"] == "custom.css"


def test_style_proposal_never_applies_directly():
    """The style service's apply() must not be called by the AI tool."""
    from cauldron_ai_admin.builtin_tools import _handle_ui_create_proposal
    from cauldron_ai_admin.tools import AdminAIToolResult

    fake_proposal = MagicMock()
    fake_proposal.request_id = "style-002"

    fake_svc = MagicMock()
    fake_svc.create_proposal.return_value = fake_proposal

    user = _make_user("style-proposer2", perms=["cauldron_ai_admin.use_admin_ai"])
    ctx = _ctx(user)

    with patch("cauldron_ai_admin.style_service.get_style_service", return_value=fake_svc):
        _handle_ui_create_proposal(
            ctx,
            scope="pages",
            target_path="pages.css",
            proposed_content="body { font-size: 16px; }",
            description="Adjust base font size.",
        )

    fake_svc.apply.assert_not_called()
    fake_svc.approve.assert_not_called()


# ---------------------------------------------------------------------------
# 5. Disabled modules remove their tools from the effective registry
# ---------------------------------------------------------------------------

def test_unregistered_module_tools_absent_from_actor_view():
    """Tools from a 'disabled' module (never registered or unregistered)
    must not appear in list_for_actor results."""
    from cauldron_ai_admin.tools import (
        AdminAIToolDefinition,
        AdminAIToolRegistry,
        RiskLevel,
    )

    reg = AdminAIToolRegistry()

    def _noop(ctx, **kw):
        from cauldron_ai_admin.tools import AdminAIToolResult
        return AdminAIToolResult(tool_name="x", success=True)

    # Register tools from two modules
    reg.register(
        AdminAIToolDefinition(
            name="active.tool",
            version="1.0",
            description="from active module",
            argument_schema={"type": "object"},
            risk_level=RiskLevel.READ_ONLY,
            required_permission="cauldron_ai_admin.use_admin_ai",
            owning_module="cauldron.active",
        ),
        _noop,
    )
    reg.register(
        AdminAIToolDefinition(
            name="disabled.tool",
            version="1.0",
            description="from disabled module",
            argument_schema={"type": "object"},
            risk_level=RiskLevel.READ_ONLY,
            required_permission="cauldron_ai_admin.use_admin_ai",
            owning_module="cauldron.disabled",
        ),
        _noop,
    )

    # Simulate module being disabled: its tool is unregistered
    reg.unregister("disabled.tool")

    class _Actor:
        is_active = True
        def has_perm(self, perm): return True

    names = {d.name for d in reg.list_for_actor(_Actor())}
    assert "active.tool" in names
    assert "disabled.tool" not in names


# ---------------------------------------------------------------------------
# 6. Tool schemas sent to the provider include authorized mutation tools
# ---------------------------------------------------------------------------

def test_tool_schemas_sent_to_provider_include_propose_tools():
    """When the actor has propose_content_changes, the AIModelRequest sent to
    the provider must include content.create_proposal in its tools list."""
    from cauldron_ai.contracts import AIModelResponse
    from cauldron_ai.testing import FakeAIModelProvider
    from cauldron_ai_admin.builtin_tools import register_builtin_tools
    from cauldron_ai_admin.service import AdminAIService

    register_builtin_tools()

    user = _make_user("schema-test-user", perms=[
        "cauldron_ai_admin.use_admin_ai",
        "cauldron_ai_admin.view_ui_styles",
        "cauldron_content_operations.view_published_content",
        "cauldron_content_operations.propose_content_changes",
    ])

    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="r1",
        content="I see your tools.",
        stop_reason="end_turn",
    ))

    from cauldron_ai_admin.tools import get_tool_registry
    registry = get_tool_registry()
    from helpers import make_assembly_service_for_tools
    asm = make_assembly_service_for_tools(
        *[d.name for d in registry.list_for_actor(user)]
    )
    svc = AdminAIService(
        provider=fake,
        tool_registry=registry,
        prompt_assembly_service=asm,
    )
    svc.run(user, "What can you do?")

    request = fake.last_request()
    assert request is not None
    tool_names = {t.name for t in request.tools}
    assert "content.create_proposal" in tool_names, (
        "content.create_proposal must be included in AIModelRequest.tools "
        "when actor has propose_content_changes"
    )


# ---------------------------------------------------------------------------
# 7. Diagnostic inventory remains within configured result-size limit
# ---------------------------------------------------------------------------

def test_diagnostic_inventory_within_result_size_limit():
    from cauldron_ai_admin.builtin_tools import _handle_admin_ai_inventory, register_builtin_tools
    from cauldron_ai_admin.tools import AdminAIToolResult, get_tool_registry

    register_builtin_tools()

    # Give the user all permissions to maximise inventory size
    user = _make_user("inventory-test-user", perms=[
        "cauldron_ai_admin.use_admin_ai",
        "cauldron_ai_admin.manage_admin_ai_settings",
        "cauldron_ai_admin.view_ui_styles",
        "cauldron_ai_admin.propose_ui_style_changes",
        "cauldron_content_operations.view_published_content",
        "cauldron_content_operations.propose_content_changes",
        "cauldron_content_operations.view_content_change_requests",
    ])

    ctx = _ctx(user)
    result = _handle_admin_ai_inventory(ctx)

    assert isinstance(result, AdminAIToolResult)
    assert result.success is True

    serialised = json.dumps(result.data).encode("utf-8")
    # Default max_result_bytes is 8192 — inventory must comfortably fit.
    from cauldron_ai_admin.service_factory import EXECUTION_BUDGET_DEFAULTS
    max_bytes = EXECUTION_BUDGET_DEFAULTS["max_result_bytes"]
    assert len(serialised) <= max_bytes, (
        f"Inventory JSON is {len(serialised)} bytes, exceeds max_result_bytes={max_bytes}"
    )


def test_inventory_groups_tools_by_risk_level():
    from cauldron_ai_admin.builtin_tools import _handle_admin_ai_inventory, register_builtin_tools
    from cauldron_ai_admin.tools import AdminAIToolResult, get_tool_registry

    register_builtin_tools()

    user = _make_user("inventory-grouping-user", perms=[
        "cauldron_ai_admin.use_admin_ai",
        "cauldron_content_operations.view_published_content",
        "cauldron_content_operations.propose_content_changes",
    ])
    ctx = _ctx(user)
    result = _handle_admin_ai_inventory(ctx)

    assert isinstance(result, AdminAIToolResult)
    by_risk = result.data["by_risk_level"]
    assert "READ_ONLY" in by_risk
    assert "PROPOSE" in by_risk

    propose_names = {entry["name"] for entry in by_risk["PROPOSE"]}
    assert "content.create_proposal" in propose_names


# ---------------------------------------------------------------------------
# 8. Representative request inspects content then proposes a change
# ---------------------------------------------------------------------------

def test_representative_request_inspects_then_proposes():
    """A fully authorized actor making a real service run should be able to:
    1. Call a read tool (content.list_items).
    2. Call a write tool (content.create_proposal).
    3. Return a final response — confirming the run is not read-only.

    The FakeAIModelProvider drives the tool-call sequence; the content service
    is stubbed so no real database writes occur.
    """
    from cauldron_ai.contracts import AIModelResponse, AIModelToolCall
    from cauldron_ai.testing import FakeAIModelProvider
    from cauldron_ai_admin.builtin_tools import register_builtin_tools
    from cauldron_ai_admin.models import AdminAIToolInvocation
    from cauldron_ai_admin.service import AdminAIService
    from cauldron_ai_admin.tools import get_tool_registry

    register_builtin_tools()

    user = _make_user("e2e-propose-user", perms=[
        "cauldron_ai_admin.use_admin_ai",
        "cauldron_content_operations.view_published_content",
        "cauldron_content_operations.propose_content_changes",
    ])

    # Stub the content service so the handler doesn't need a real DB.
    fake_item = MagicMock()
    fake_item.to_dict.return_value = {"id": "home", "title": "Home"}

    fake_cs_result = MagicMock()
    fake_cs_result.ok = True
    fake_cs_result.request_id = "cs-e2e-001"

    fake_cs = MagicMock()
    fake_cs.list_items.return_value = [fake_item]
    fake_cs.create_change_request.return_value = fake_cs_result

    # Provider script: inspect → propose → done
    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="r1",
        tool_calls=(
            AIModelToolCall(
                id="c1",
                name="content.list_items",
                arguments={"collection": "pages"},
            ),
        ),
        stop_reason="tool_use",
    ))
    fake.queue_response(AIModelResponse(
        provider_request_id="r2",
        tool_calls=(
            AIModelToolCall(
                id="c2",
                name="content.create_proposal",
                arguments={
                    "operations": [
                        {
                            "kind": "update",
                            "collection": "pages",
                            "item_id": "home",
                        }
                    ],
                    "description": "Update the home page.",
                },
            ),
        ),
        stop_reason="tool_use",
    ))
    fake.queue_response(AIModelResponse(
        provider_request_id="r3",
        content="Proposal created for your review.",
        stop_reason="end_turn",
    ))

    registry = get_tool_registry()
    from helpers import make_assembly_service_for_tools
    asm = make_assembly_service_for_tools(
        *[d.name for d in registry.list_for_actor(user)]
    )
    svc = AdminAIService(
        provider=fake,
        tool_registry=registry,
        content_service=fake_cs,
        prompt_assembly_service=asm,
        max_model_turns=5,
        max_tool_calls=10,
    )

    run = svc.run(user, "Update the home page nav title.")

    assert run.status == "completed"

    invocations = list(
        AdminAIToolInvocation.objects.filter(run=run).order_by("created_at")
    )
    tool_names = [inv.tool_name for inv in invocations]
    assert "content.list_items" in tool_names, (
        "Expected a read-only inspection call before the proposal"
    )
    assert "content.create_proposal" in tool_names, (
        "Expected a proposal tool call — run must not be read-only"
    )
    # Verify the proposal invocation succeeded
    proposal_inv = next(i for i in invocations if i.tool_name == "content.create_proposal")
    assert proposal_inv.status == "completed"
