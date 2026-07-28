"""Tests for the Admin AI write-capability path.

Verifies that:
1. Authorized administrators receive the expected write/proposal tools.
2. Unauthorized users receive only permitted tools; inventory reports the
   absence of PROPOSE tools with an actionable hint.
3. Content changes go through ContentOperationService.
4. Style changes use the module-owned UIStyleChangeService mutation path.
5. Disabled modules remove their tools from the effective registry.
6. Tool schemas sent to the provider include authorized mutation tools.
7. The diagnostic inventory is byte-bounded even with large registries,
   using the effective limit from AdminAIToolContext rather than the
   module default constant.
8. A representative request can inspect content and then propose a change
   rather than remaining read-only.
9. Admin AI contains no direct import of the Astro build service (module
   boundary enforcement).
"""
from __future__ import annotations

import json
import pathlib
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
# 2. Unauthorized user receives only permitted tools; inventory hints
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
    for d in permitted:
        assert d.risk_level == RiskLevel.READ_ONLY, (
            f"{d.name} is not READ_ONLY but visible to unprivileged user"
        )


def test_anonymous_actor_sees_no_tools():
    from cauldron_ai_admin.tools import AdminAIToolDefinition, AdminAIToolRegistry, RiskLevel

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


def test_inventory_reports_no_propose_tools_hint():
    """When an actor has no PROPOSE tools, the inventory must include an
    actionable hint explaining which permissions are required."""
    from cauldron_ai_admin.builtin_tools import (
        _INVENTORY_NO_PROPOSE_HINT,
        _handle_admin_ai_inventory,
        register_builtin_tools,
    )
    from cauldron_ai_admin.tools import AdminAIToolResult

    register_builtin_tools()
    # User has only use_admin_ai — no proposal permissions.
    user = _make_user("hint-test-user", perms=["cauldron_ai_admin.use_admin_ai"])
    ctx = _ctx(user)
    result = _handle_admin_ai_inventory(ctx)

    assert isinstance(result, AdminAIToolResult)
    assert result.success is True
    assert "hint" in result.data, "hint must be present when no PROPOSE tools are accessible"
    hint = result.data["hint"]
    assert "PROPOSE" not in result.data.get("by_risk_level", {}), (
        "by_risk_level must not have a PROPOSE key for an unprivileged actor"
    )
    assert "propose_content_changes" in hint, (
        "hint must name the missing permission"
    )
    assert "propose_ui_style_changes" in hint, (
        "hint must name the missing permission"
    )


def test_inventory_no_hint_when_propose_tools_available():
    """When the actor has PROPOSE tools, the hint must not appear."""
    from cauldron_ai_admin.builtin_tools import _handle_admin_ai_inventory, register_builtin_tools
    from cauldron_ai_admin.tools import AdminAIToolResult

    register_builtin_tools()
    user = _make_user("hint-propose-user", perms=[
        "cauldron_ai_admin.use_admin_ai",
        "cauldron_content_operations.view_published_content",
        "cauldron_content_operations.propose_content_changes",
    ])
    ctx = _ctx(user)
    result = _handle_admin_ai_inventory(ctx)

    assert isinstance(result, AdminAIToolResult)
    assert "hint" not in result.data, (
        "hint must not appear when PROPOSE tools are accessible"
    )


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
    svc.create_change_request.assert_called_once()
    call_kwargs = svc.create_change_request.call_args.kwargs
    assert call_kwargs["user"] is user
    assert len(call_kwargs["operations"]) == 1


def test_content_proposal_not_applied_directly():
    """Content proposals return 'proposed' status and must never invoke
    validate/apply on the content service."""
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
    # patch the source module so the import resolves to our fake.
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
    """The style service's apply/approve must not be called by the AI tool."""
    from cauldron_ai_admin.builtin_tools import _handle_ui_create_proposal

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
    """Tools from a 'disabled' module (unregistered at disable time) must
    not appear in list_for_actor results."""
    from cauldron_ai_admin.tools import (
        AdminAIToolDefinition,
        AdminAIToolRegistry,
        AdminAIToolResult,
        RiskLevel,
    )

    reg = AdminAIToolRegistry()

    def _noop(ctx, **kw):
        return AdminAIToolResult(tool_name="x", success=True)

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

    # Simulate module being disabled: its tool is unregistered.
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
    from cauldron_ai_admin.tools import get_tool_registry

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
# 7. Diagnostic inventory is byte-bounded
# ---------------------------------------------------------------------------

def test_diagnostic_inventory_reports_new_fields():
    """Inventory result must include total_accessible, returned, truncated."""
    from cauldron_ai_admin.builtin_tools import _handle_admin_ai_inventory, register_builtin_tools
    from cauldron_ai_admin.tools import AdminAIToolResult

    register_builtin_tools()

    user = _make_user("inventory-fields-user", perms=[
        "cauldron_ai_admin.use_admin_ai",
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
    data = result.data
    assert "total_accessible" in data
    assert "returned" in data
    assert "truncated" in data
    assert "by_risk_level" in data
    assert isinstance(data["total_accessible"], int)
    assert isinstance(data["returned"], int)
    assert isinstance(data["truncated"], bool)
    assert data["returned"] <= data["total_accessible"]

    # With only the builtin tools the set is small enough to fit untruncated.
    assert data["truncated"] is False
    serialised = json.dumps(data).encode("utf-8")
    from cauldron_ai_admin.service_factory import EXECUTION_BUDGET_DEFAULTS
    max_bytes = EXECUTION_BUDGET_DEFAULTS["max_result_bytes"]
    assert len(serialised) <= max_bytes, (
        f"Inventory JSON is {len(serialised)} bytes, exceeds max_result_bytes={max_bytes}"
    )


def test_inventory_groups_tools_by_risk_level():
    from cauldron_ai_admin.builtin_tools import _handle_admin_ai_inventory, register_builtin_tools
    from cauldron_ai_admin.tools import AdminAIToolResult

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


def test_inventory_byte_bounded_large_registry():
    """With a synthetic registry containing far more tools than can fit,
    the handler must truncate at the *context-provided* effective limit,
    not the module-level EXECUTION_BUDGET_DEFAULTS constant.

    This test uses context.max_result_bytes=3000 — well below the 8192
    default — to prove the effective limit drives truncation, not the
    fallback constant."""
    from cauldron_ai_admin.builtin_tools import _handle_admin_ai_inventory
    from cauldron_ai_admin.tools import (
        AdminAIToolContext,
        AdminAIToolDefinition,
        AdminAIToolRegistry,
        AdminAIToolResult,
        RiskLevel,
    )

    CUSTOM_LIMIT = 3000

    reg = AdminAIToolRegistry()

    def _noop(ctx, **kw):
        return AdminAIToolResult(tool_name="x.y", success=True)

    # Register 200 synthetic tools — far more than can fit in 3 KB.
    for i in range(200):
        reg.register(
            AdminAIToolDefinition(
                name=f"synthetic.tool{i:03d}",
                version="1.0",
                description=(
                    f"Synthetic tool {i:03d}. This description is intentionally "
                    "long enough that the cumulative JSON will exceed the byte "
                    "limit well before all 200 tools have been included."
                ),
                argument_schema={"type": "object"},
                risk_level=RiskLevel.READ_ONLY,
                required_permission="cauldron_ai_admin.use_admin_ai",
                owning_module="cauldron.ai.admin",
            ),
            _noop,
        )

    class _AllPermsActor:
        is_active = True
        def has_perm(self, perm): return True

    with patch("cauldron_ai_admin.builtin_tools.get_tool_registry", return_value=reg):
        ctx = AdminAIToolContext(
            actor=_AllPermsActor(), run_id="r", correlation_id="c",
            max_result_bytes=CUSTOM_LIMIT,
        )
        result = _handle_admin_ai_inventory(ctx)

    assert isinstance(result, AdminAIToolResult)
    assert result.success is True

    data = result.data
    serialised = json.dumps(data).encode("utf-8")
    # Must fit within the *context* limit (3000), not the module default (8192).
    assert len(serialised) <= CUSTOM_LIMIT, (
        f"Inventory JSON is {len(serialised)} bytes; "
        f"exceeds context-provided limit of {CUSTOM_LIMIT}"
    )
    assert data["truncated"] is True, (
        "200 tools must exceed the 3000-byte budget and force truncation"
    )
    assert data["total_accessible"] == 200
    assert data["returned"] < 200


# ---------------------------------------------------------------------------
# 9. Admin AI contains no direct Astro import (module boundary)
# ---------------------------------------------------------------------------

def test_no_direct_astro_import_in_admin_ai():
    """Admin AI must not import cauldron_site_astro directly.

    Public-site builds are owned by cauldron.site.astro. Any rebuild tool
    must be registered by that module through the Admin AI tool registry
    rather than imported directly by cauldron-ai-admin.
    """
    src_dir = (
        pathlib.Path(__file__).parent.parent / "src" / "cauldron_ai_admin"
    )
    violations: list[str] = []
    for py_file in sorted(src_dir.rglob("*.py")):
        source = py_file.read_text(encoding="utf-8")
        if "cauldron_site_astro" in source:
            violations.append(py_file.name)

    assert not violations, (
        f"These Admin AI source files import cauldron_site_astro directly: "
        f"{violations}. Rebuild tools must be registered by cauldron.site.astro "
        "through the Admin AI tool registry, not imported directly."
    )


# ---------------------------------------------------------------------------
# 8. Representative request inspects content then proposes a change
# ---------------------------------------------------------------------------

def test_representative_request_inspects_then_proposes():
    """A fully authorized actor should be able to inspect and then propose
    in a single run — the run must not remain read-only.

    FakeAIModelProvider drives the tool-call sequence; the content service
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
    proposal_inv = next(i for i in invocations if i.tool_name == "content.create_proposal")
    assert proposal_inv.status == "completed"


# ---------------------------------------------------------------------------
# Effective max_result_bytes propagation through context
# ---------------------------------------------------------------------------

def test_inventory_honors_custom_lower_max_result_bytes():
    """Passing a lower max_result_bytes on the context must constrain the
    inventory output to that limit, not the 8192 module default."""
    from cauldron_ai_admin.builtin_tools import _handle_admin_ai_inventory
    from cauldron_ai_admin.tools import (
        AdminAIToolContext,
        AdminAIToolDefinition,
        AdminAIToolRegistry,
        AdminAIToolResult,
        RiskLevel,
    )

    CUSTOM_LIMIT = 2000
    reg = AdminAIToolRegistry()

    def _noop(ctx, **kw):
        return AdminAIToolResult(tool_name="x.y", success=True)

    for i in range(100):
        reg.register(
            AdminAIToolDefinition(
                name=f"bounded.tool{i:03d}",
                version="1.0",
                description=f"Tool {i:03d} for limit-honoring test with padding.",
                argument_schema={"type": "object"},
                risk_level=RiskLevel.READ_ONLY,
                required_permission="cauldron_ai_admin.use_admin_ai",
                owning_module="cauldron.ai.admin",
            ),
            _noop,
        )

    class _AllPermsActor:
        is_active = True
        def has_perm(self, perm): return True

    with patch("cauldron_ai_admin.builtin_tools.get_tool_registry", return_value=reg):
        ctx = AdminAIToolContext(
            actor=_AllPermsActor(), run_id="r", correlation_id="c",
            max_result_bytes=CUSTOM_LIMIT,
        )
        result = _handle_admin_ai_inventory(ctx)

    assert isinstance(result, AdminAIToolResult)
    serialised = json.dumps(result.data).encode("utf-8")
    assert len(serialised) <= CUSTOM_LIMIT, (
        f"Result ({len(serialised)} bytes) exceeds custom limit {CUSTOM_LIMIT}"
    )
    # 100 tools must exceed 2000 bytes so truncation must kick in.
    assert result.data["truncated"] is True
    assert result.data["returned"] < result.data["total_accessible"]


def test_saved_runtime_max_result_bytes_flows_into_context(tmp_path):
    """When max_result_bytes is saved in the runtime settings store the value
    must flow through resolve_runtime_settings → AdminAIService → context."""
    from cauldron_ai.contracts import AIModelResponse, AIModelToolCall
    from cauldron_ai.testing import FakeAIModelProvider
    from cauldron_ai_admin.builtin_tools import _handle_admin_ai_inventory, register_builtin_tools
    from cauldron_ai_admin.provider_config import AIProviderSettingsStore, _reset_store_for_tests
    from cauldron_ai_admin.service import AdminAIService
    from cauldron_ai_admin.service_factory import resolve_runtime_settings
    from cauldron_ai_admin.tools import get_tool_registry

    SAVED_LIMIT = 4096

    # Write saved runtime setting.
    store_path = tmp_path / "ai.json"
    _reset_store_for_tests(path=store_path)
    store = AIProviderSettingsStore(store_path)
    store.set_runtime({"max_result_bytes": SAVED_LIMIT})

    # resolve_runtime_settings must pick up the saved value.
    resolved = resolve_runtime_settings(store, cfg={})
    assert resolved["max_result_bytes"] == SAVED_LIMIT, (
        f"resolve_runtime_settings returned {resolved['max_result_bytes']}, "
        f"expected {SAVED_LIMIT}"
    )

    register_builtin_tools()
    user = _make_user("runtime-limit-user", perms=[
        "cauldron_ai_admin.use_admin_ai",
        "cauldron_content_operations.view_published_content",
    ])

    # Build service with the resolved limit.
    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="r1",
        tool_calls=(
            AIModelToolCall(
                id="c1",
                name="system.admin_ai_inventory",
                arguments={},
            ),
        ),
        stop_reason="tool_use",
    ))
    fake.queue_response(AIModelResponse(
        provider_request_id="r2",
        content="Inventory retrieved.",
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
        prompt_assembly_service=asm,
        max_result_bytes=resolved["max_result_bytes"],
    )
    assert svc.max_result_bytes == SAVED_LIMIT

    run = svc.run(user, "Show me the tool inventory.")
    assert run.status == "completed"

    # The tool invocation must have succeeded within the saved limit.
    from cauldron_ai_admin.models import AdminAIToolInvocation
    inv = AdminAIToolInvocation.objects.get(run=run, tool_name="system.admin_ai_inventory")
    assert inv.status == "completed"


def test_permission_hint_within_configured_limit():
    """The permission hint must not push the result over the configured limit
    even when the limit is small."""
    from cauldron_ai_admin.builtin_tools import _handle_admin_ai_inventory
    from cauldron_ai_admin.tools import (
        AdminAIToolContext,
        AdminAIToolDefinition,
        AdminAIToolRegistry,
        AdminAIToolResult,
        RiskLevel,
    )

    # Tight limit — hint + a handful of tools must still fit.
    SMALL_LIMIT = 1500
    reg = AdminAIToolRegistry()

    def _noop(ctx, **kw):
        return AdminAIToolResult(tool_name="x.y", success=True)

    for i in range(5):
        reg.register(
            AdminAIToolDefinition(
                name=f"hint.tool{i:03d}",
                version="1.0",
                description=f"Tool {i:03d}.",
                argument_schema={"type": "object"},
                risk_level=RiskLevel.READ_ONLY,
                required_permission="cauldron_ai_admin.use_admin_ai",
                owning_module="cauldron.ai.admin",
            ),
            _noop,
        )

    class _ReadOnlyActor:
        is_active = True
        def has_perm(self, perm): return True  # has use_admin_ai only

    with patch("cauldron_ai_admin.builtin_tools.get_tool_registry", return_value=reg):
        ctx = AdminAIToolContext(
            actor=_ReadOnlyActor(), run_id="r", correlation_id="c",
            max_result_bytes=SMALL_LIMIT,
        )
        result = _handle_admin_ai_inventory(ctx)

    assert isinstance(result, AdminAIToolResult)
    assert result.success is True

    serialised = json.dumps(result.data).encode("utf-8")
    assert len(serialised) <= SMALL_LIMIT, (
        f"Result with hint is {len(serialised)} bytes, exceeds limit {SMALL_LIMIT}"
    )


def test_inventory_default_fallback_matches_execution_budget_default():
    """A context created without an explicit max_result_bytes must fall back to
    the same value as EXECUTION_BUDGET_DEFAULTS['max_result_bytes'] (8192)."""
    from cauldron_ai_admin.service_factory import EXECUTION_BUDGET_DEFAULTS
    from cauldron_ai_admin.tools import AdminAIToolContext, _CONTEXT_DEFAULT_MAX_RESULT_BYTES

    assert _CONTEXT_DEFAULT_MAX_RESULT_BYTES == EXECUTION_BUDGET_DEFAULTS["max_result_bytes"], (
        "_CONTEXT_DEFAULT_MAX_RESULT_BYTES must match EXECUTION_BUDGET_DEFAULTS "
        "so contexts created outside the service behave identically to those "
        "created by a freshly configured AdminAIService."
    )

    user_mock = MagicMock()
    ctx = AdminAIToolContext(actor=user_mock, run_id="r", correlation_id="c")
    assert ctx.max_result_bytes == EXECUTION_BUDGET_DEFAULTS["max_result_bytes"]


def test_inventory_default_8192_behavior_unchanged():
    """A default context (no explicit max_result_bytes) must produce inventory
    output that fits within 8192 bytes for the standard builtin tool set."""
    from cauldron_ai_admin.builtin_tools import _handle_admin_ai_inventory, register_builtin_tools
    from cauldron_ai_admin.tools import AdminAIToolResult

    register_builtin_tools()

    user = _make_user("default-limit-user", perms=[
        "cauldron_ai_admin.use_admin_ai",
        "cauldron_ai_admin.view_ui_styles",
        "cauldron_ai_admin.propose_ui_style_changes",
        "cauldron_content_operations.view_published_content",
        "cauldron_content_operations.propose_content_changes",
        "cauldron_content_operations.view_content_change_requests",
    ])
    ctx = _ctx(user)  # uses _CONTEXT_DEFAULT_MAX_RESULT_BYTES = 8192
    result = _handle_admin_ai_inventory(ctx)

    assert isinstance(result, AdminAIToolResult)
    assert result.success is True
    assert result.data["truncated"] is False, (
        "Builtin tool set must fit within the 8192-byte default"
    )
    serialised = json.dumps(result.data).encode("utf-8")
    assert len(serialised) <= 8192
