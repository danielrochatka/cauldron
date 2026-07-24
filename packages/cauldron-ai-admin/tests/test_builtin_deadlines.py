"""Every built-in Admin AI tool must refuse when the deadline has expired."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.django_db

from cauldron_ai_admin.builtin_tools import (
    _handle_create_proposal,
    _handle_django_checks,
    _handle_get_item,
    _handle_list_collections,
    _handle_list_items,
    _handle_module_status,
    _handle_preview_change_request,
)
from cauldron_ai_admin.tools import (
    AdminAIToolContext,
    AdminAIToolError,
)


def _expired_ctx(content_service=None):
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user, _ = User.objects.get_or_create(username="deadlineuser")
    return AdminAIToolContext(
        actor=user,
        run_id="r",
        correlation_id="c",
        content_service=content_service,
        deadline=datetime.now(tz=timezone.utc) - timedelta(seconds=1),
    )


def test_list_collections_refuses_when_deadline_expired():
    result = _handle_list_collections(_expired_ctx(MagicMock()))
    assert isinstance(result, AdminAIToolError)
    assert result.error_code == "tool.timeout"


def test_list_items_refuses_when_deadline_expired():
    result = _handle_list_items(
        _expired_ctx(MagicMock()), collection="pages",
    )
    assert isinstance(result, AdminAIToolError)
    assert result.error_code == "tool.timeout"


def test_get_item_refuses_when_deadline_expired():
    result = _handle_get_item(
        _expired_ctx(MagicMock()), collection="pages", item_id="home",
    )
    assert isinstance(result, AdminAIToolError)
    assert result.error_code == "tool.timeout"


def test_create_proposal_refuses_when_deadline_expired():
    fake_service = MagicMock()
    fake_service.create_change_request.return_value = MagicMock(
        ok=True, request_id="cs-1",
    )
    result = _handle_create_proposal(
        _expired_ctx(fake_service), operations=[{"kind": "create"}],
    )
    assert isinstance(result, AdminAIToolError)
    assert result.error_code == "tool.timeout"
    # PROPOSE deadline check prevents create_change_request from being called.
    fake_service.create_change_request.assert_not_called()


def test_preview_refuses_when_deadline_expired():
    fake_service = MagicMock(spec=["get_preview"])
    result = _handle_preview_change_request(
        _expired_ctx(fake_service), cs_id="cs-1",
    )
    assert isinstance(result, AdminAIToolError)
    assert result.error_code == "tool.timeout"


def test_django_checks_refuses_when_deadline_expired():
    result = _handle_django_checks(_expired_ctx())
    assert isinstance(result, AdminAIToolError)
    assert result.error_code == "tool.timeout"


def test_module_status_refuses_when_deadline_expired():
    result = _handle_module_status(_expired_ctx())
    assert isinstance(result, AdminAIToolError)
    assert result.error_code == "tool.timeout"


def test_post_execution_deadline_marks_invocation_timed_out():
    """When a handler runs longer than the run deadline the service must
    record the invocation as ``timed_out`` and fail the run rather than
    treat the result as authoritative.

    We simulate this by having the handler itself sleep past the run's
    tiny time budget.
    """
    import time as _time
    from cauldron_ai.contracts import AIModelResponse, AIModelToolCall
    from cauldron_ai.testing import FakeAIModelProvider
    from cauldron_ai_admin.models import AdminAIToolInvocation
    from cauldron_ai_admin.service import AdminAIService
    from cauldron_ai_admin.tools import (
        AdminAIToolDefinition,
        AdminAIToolRegistry,
        AdminAIToolResult,
        RiskLevel,
    )
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission

    User = get_user_model()
    user, _ = User.objects.get_or_create(username="postdead")
    for spec in ("auth.view_user", "cauldron_ai_admin.use_admin_ai"):
        app_label, codename = spec.split(".", 1)
        try:
            perm = Permission.objects.get(
                codename=codename, content_type__app_label=app_label,
            )
        except Permission.DoesNotExist:
            continue
        user.user_permissions.add(perm)
    user = User.objects.get(pk=user.pk)

    def _slow_handler(ctx, **kw):
        # Sleep past the tool's effective deadline before returning.
        _time.sleep(0.2)
        return AdminAIToolResult(
            tool_name="t.slow", success=True, data={"ok": True},
        )

    reg = AdminAIToolRegistry()
    reg.register(
        AdminAIToolDefinition(
            name="t.slow", version="1.0", description="",
            argument_schema={"type": "object", "properties": {}},
            risk_level=RiskLevel.READ_ONLY,
            required_permission="auth.view_user",
            owning_module="cauldron.test",
            timeout_seconds=0.05,
        ),
        _slow_handler,
    )
    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="r1",
        tool_calls=(AIModelToolCall(id="c1", name="t.slow", arguments={}),),
        stop_reason="tool_use",
    ))
    fake.queue_response(AIModelResponse(
        provider_request_id="r2", content="done", stop_reason="end_turn",
    ))
    svc = AdminAIService(
        provider=fake, tool_registry=reg,
        max_model_turns=3, max_tool_calls=5,
        # Tiny per-tool budget so the effective_deadline is what expires.
        tool_timeout_seconds=0.05,
    )
    run = svc.run(user, "Slow.")
    assert run.status == "failed"
    invocations = list(AdminAIToolInvocation.objects.filter(run=run))
    assert len(invocations) == 1
    inv = invocations[0]
    assert inv.status == "timed_out"
    assert inv.error_code == "tool.timeout"
