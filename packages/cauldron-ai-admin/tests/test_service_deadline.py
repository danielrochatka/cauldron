"""Deadline enforcement across the tool-execution boundary."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.django_db

from cauldron_ai.contracts import AIModelResponse, AIModelToolCall
from cauldron_ai.testing import FakeAIModelProvider
from cauldron_ai_admin.builtin_tools import _handle_create_proposal
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


def _make_user():
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    User = get_user_model()
    user, _ = User.objects.get_or_create(username="dltest")
    for spec in (
        "auth.view_user",
        "cauldron_ai_admin.use_admin_ai",
        "cauldron_content_operations.propose_content_changes",
    ):
        app_label, codename = spec.split(".", 1)
        try:
            perm = Permission.objects.get(
                codename=codename, content_type__app_label=app_label,
            )
        except Permission.DoesNotExist:
            continue
        user.user_permissions.add(perm)
    return User.objects.get(pk=user.pk)


def _propose_defn():
    return AdminAIToolDefinition(
        name="content.create_proposal",
        version="1.0",
        description="",
        argument_schema={
            "type": "object",
            "properties": {"operations": {"type": "array", "minItems": 1}},
            "required": ["operations"],
        },
        risk_level=RiskLevel.PROPOSE,
        required_permission="cauldron_content_operations.propose_content_changes",
        owning_module="cauldron.ai.admin",
    )


def test_propose_tool_refuses_when_deadline_expired():
    """Directly exercising the ``content.create_proposal`` handler with a
    context whose deadline is in the past: the handler must refuse and
    never call ``create_change_request``."""
    fake_service = MagicMock()
    fake_service.create_change_request.return_value = MagicMock(
        ok=True, request_id="cs-x",
    )
    ctx = AdminAIToolContext(
        actor=MagicMock(),
        run_id="r-1",
        correlation_id="c-1",
        content_service=fake_service,
        deadline=datetime.now(tz=timezone.utc) - timedelta(seconds=5),
    )
    result = _handle_create_proposal(ctx, operations=[{"kind": "create"}])
    assert isinstance(result, AdminAIToolError)
    assert result.error_code == "tool.timeout"
    fake_service.create_change_request.assert_not_called()


def test_service_marks_tool_timed_out_when_deadline_zero():
    """A zero-second run deadline flags any tool call as ``timed_out``."""
    reg = AdminAIToolRegistry()
    reg.register(_propose_defn(), lambda ctx, **kw: (_ for _ in ()).throw(
        RuntimeError("must not run"),
    ))
    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="r1",
        tool_calls=(AIModelToolCall(
            id="c1", name="content.create_proposal",
            arguments={"operations": [{"kind": "create", "collection": "pages"}]},
        ),),
        stop_reason="tool_use",
    ))
    svc = AdminAIService(
        provider=fake, tool_registry=reg,
        max_model_turns=2, max_tool_calls=2,
        run_timeout_seconds=0.001,   # already expired by the time tool runs
    )
    user = _make_user()
    run = svc.run(user, "Propose.")
    # Whichever safety net fires first, the resulting run is failed and
    # no persisted invocation is in ``completed``.
    assert run.status == "failed"
    for inv in AdminAIToolInvocation.objects.filter(run=run):
        assert inv.status != "completed"
