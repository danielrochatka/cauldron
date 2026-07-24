"""Verify the service builds a structured multi-turn conversation.

After a tool_use turn the service must:
* append an ``assistant`` message carrying the tool_calls tuple, then
* append one ``tool`` message per invocation carrying tool_call_id.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db

from cauldron_ai.contracts import AIModelResponse, AIModelToolCall
from cauldron_ai.testing import FakeAIModelProvider
from cauldron_ai_admin.service import AdminAIService
from cauldron_ai_admin.tools import (
    AdminAIToolDefinition,
    AdminAIToolRegistry,
    AdminAIToolResult,
    RiskLevel,
)


def _defn(name="t.read"):
    return AdminAIToolDefinition(
        name=name, version="1.0", description="",
        argument_schema={"type": "object", "properties": {}},
        risk_level=RiskLevel.READ_ONLY,
        required_permission="auth.view_user",
        owning_module="cauldron.test",
    )


def _make_user():
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    User = get_user_model()
    user, _ = User.objects.get_or_create(username="convo")
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


def test_second_provider_request_includes_assistant_and_tool_messages():
    reg = AdminAIToolRegistry()
    reg.register(_defn("t.read"), lambda ctx, **kw: AdminAIToolResult(
        tool_name="t.read", success=True, data={"count": 42},
    ))
    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="r1",
        tool_calls=(AIModelToolCall(id="tc-1", name="t.read", arguments={}),),
        stop_reason="tool_use",
    ))
    fake.queue_response(AIModelResponse(
        provider_request_id="r2", content="done", stop_reason="end_turn",
    ))
    svc = AdminAIService(
        provider=fake, tool_registry=reg,
        max_model_turns=3, max_tool_calls=5,
    )
    run = svc.run(_make_user(), "Read something.")
    assert run.status == "completed"

    # Two provider requests should have been made.
    assert fake.call_count() == 2
    second = fake.requests()[1]
    roles = [m.role for m in second.messages]
    # user -> assistant(with tool_calls) -> tool
    assert roles == ["user", "assistant", "tool"]

    assistant_msg = second.messages[1]
    assert len(assistant_msg.tool_calls) == 1
    assert assistant_msg.tool_calls[0].id == "tc-1"
    assert assistant_msg.tool_calls[0].name == "t.read"

    tool_msg = second.messages[2]
    assert tool_msg.tool_call_id == "tc-1"
