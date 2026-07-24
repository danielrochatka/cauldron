"""ITEM 3 — audit-status semantics for the various timeout paths."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.django_db

from cauldron_ai.contracts import AIModelResponse, AIModelToolCall
from cauldron_ai.testing import FakeAIModelProvider
from cauldron_ai_admin.models import AdminAIToolInvocation
from cauldron_ai_admin.service import AdminAIService
from cauldron_ai_admin.tools import (
    AdminAIToolContext,
    AdminAIToolDefinition,
    AdminAIToolError,
    AdminAIToolRegistry,
    AdminAIToolResult,
    RiskLevel,
)


def _defn(name="t.read", perm="auth.view_user"):
    return AdminAIToolDefinition(
        name=name, version="1.0", description="",
        argument_schema={"type": "object", "properties": {}},
        risk_level=RiskLevel.READ_ONLY,
        required_permission=perm,
        owning_module="cauldron.test",
    )


def _make_user():
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    User = get_user_model()
    user, _ = User.objects.get_or_create(username="timeouts-user")
    for spec in ("auth.view_user", "cauldron_ai_admin.use_admin_ai"):
        app_label, codename = spec.split(".", 1)
        try:
            perm = Permission.objects.get(
                codename=codename, content_type__app_label=app_label,
            )
        except Permission.DoesNotExist:
            continue
        user.user_permissions.add(perm)
    return User.objects.get(pk=user.pk)


def _one_call_provider(name="t.read"):
    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="r1",
        tool_calls=(AIModelToolCall(id="c1", name=name, arguments={}),),
        stop_reason="tool_use",
    ))
    return fake


def test_pre_handler_deadline_expiry_records_timed_out_and_skips_handler():
    """When the run deadline is exhausted by the time we get to the
    handler, the handler must not be called and any persisted
    invocation must record timed_out."""
    called: list[bool] = []

    def handler(ctx, **kw):  # pragma: no cover
        called.append(True)
        return AdminAIToolResult(tool_name="t.read", success=True, data={})

    reg = AdminAIToolRegistry()
    reg.register(_defn(), handler)
    # A run deadline that's already spent (but not immediately expired
    # before the loop check) still gets us to the pre-handler deadline
    # branch: we pick a value large enough to survive the loop-entry
    # check but smaller than DEADLINE_EPSILON (0.1s).
    svc = AdminAIService(
        provider=_one_call_provider(),
        tool_registry=reg,
        max_model_turns=2, max_tool_calls=2,
        run_timeout_seconds=0.05,
    )
    run = svc.run(_make_user(), "Hi.")
    assert run.status == "failed"
    assert called == []
    for inv in AdminAIToolInvocation.objects.filter(run=run):
        # If an invocation was persisted it must reflect the timeout.
        assert inv.status == "timed_out"
        assert inv.error_code == "tool.timeout"
        assert inv.completed_at is not None
        assert inv.duration_ms == 0


def test_handler_returning_tool_timeout_records_timed_out_not_failed():
    """A handler that self-signals a timeout must appear as ``timed_out``,
    not ``failed`` — but the run still fails."""
    reg = AdminAIToolRegistry()
    reg.register(
        _defn(),
        lambda ctx, **kw: AdminAIToolError(
            tool_name="t.read",
            error_code="tool.timeout",
            message="Ran out of time.",
        ),
    )
    svc = AdminAIService(
        provider=_one_call_provider(),
        tool_registry=reg,
        max_model_turns=3, max_tool_calls=5,
    )
    run = svc.run(_make_user(), "Hi.")
    assert run.status == "failed"
    inv = AdminAIToolInvocation.objects.get(run=run)
    assert inv.status == "timed_out"
    assert inv.error_code == "tool.timeout"


def test_post_handler_deadline_expiry_forces_timed_out_and_ignores_result():
    """If the handler runs past the effective deadline, the successful
    result must be ignored and the invocation forced to ``timed_out``."""
    reg = AdminAIToolRegistry()
    # Definition with a very short per-tool timeout — the effective
    # deadline used inside _handle_tool_call is min(run_deadline, per-tool).
    d = AdminAIToolDefinition(
        name="t.slow", version="1.0", description="",
        argument_schema={"type": "object", "properties": {}},
        risk_level=RiskLevel.READ_ONLY,
        required_permission="auth.view_user",
        owning_module="cauldron.test",
        timeout_seconds=0.01,
    )

    def slow_handler(ctx, **kw):
        # Sleep long enough that the effective per-tool deadline is exceeded.
        import time
        time.sleep(0.2)
        return AdminAIToolResult(tool_name="t.slow", success=True, data={})

    reg.register(d, slow_handler)
    svc = AdminAIService(
        provider=_one_call_provider(name="t.slow"),
        tool_registry=reg,
        max_model_turns=2, max_tool_calls=2,
        tool_timeout_seconds=0.01,
    )
    run = svc.run(_make_user(), "Hi.")
    assert run.status == "failed"
    inv = AdminAIToolInvocation.objects.get(run=run)
    assert inv.status == "timed_out"
    assert inv.error_code == "tool.timeout"
