"""Model-supplied tool-call ID / tool name length enforcement."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.django_db

from cauldron_ai.contracts import AIModelResponse, AIModelToolCall
from cauldron_ai.testing import FakeAIModelProvider
from cauldron_ai_admin.models import AdminAIToolInvocation
from cauldron_ai_admin.service import (
    AdminAIService,
    MAX_TOOL_CALL_ID_BYTES,
    MAX_TOOL_NAME_BYTES,
)
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
    user, _ = User.objects.get_or_create(username="idlim")
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


def test_oversized_tool_call_id_fails_run_without_persisting_invocation():
    """A model-supplied tool-call ID longer than the hard cap must fail
    the run before any AdminAIToolInvocation row exists."""
    reg = AdminAIToolRegistry()
    handler_calls: list[str] = []

    def _handler(ctx, **kw):  # pragma: no cover - must not be called
        handler_calls.append("called")
        return AdminAIToolResult(tool_name="t.read", success=True)

    reg.register(_defn("t.read"), _handler)
    fake = FakeAIModelProvider()
    oversized = "x" * (MAX_TOOL_CALL_ID_BYTES + 10)
    fake.queue_response(AIModelResponse(
        provider_request_id="r1",
        tool_calls=(
            AIModelToolCall(id=oversized, name="t.read", arguments={}),
        ),
        stop_reason="tool_use",
    ))
    svc = AdminAIService(
        provider=fake, tool_registry=reg,
        max_model_turns=2, max_tool_calls=2,
    )
    run = svc.run(_make_user(), "Read.")
    assert run.status == "failed"
    assert run.error_code == "provider.invalid_response"
    # No invocation should have been persisted; no handler was called.
    assert AdminAIToolInvocation.objects.filter(run=run).count() == 0
    assert handler_calls == []


def test_oversized_tool_name_fails_run_without_persisting_invocation():
    """A model-supplied tool name longer than the hard cap must fail the
    run with ``tool.unknown`` before any per-invocation row is written."""
    reg = AdminAIToolRegistry()
    fake = FakeAIModelProvider()
    long_name = "a.b" + ("x" * MAX_TOOL_NAME_BYTES)
    fake.queue_response(AIModelResponse(
        provider_request_id="r1",
        tool_calls=(
            AIModelToolCall(id="c1", name=long_name, arguments={}),
        ),
        stop_reason="tool_use",
    ))
    svc = AdminAIService(
        provider=fake, tool_registry=reg,
        max_model_turns=2, max_tool_calls=2,
    )
    run = svc.run(_make_user(), "Read.")
    assert run.status == "failed"
    assert run.error_code == "tool.unknown"
    assert AdminAIToolInvocation.objects.filter(run=run).count() == 0


def test_correlation_id_over_128_bytes_is_truncated():
    """Caller-supplied correlation IDs are truncated, not rejected."""
    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="r1", content="ok", stop_reason="end_turn",
    ))
    svc = AdminAIService(
        provider=fake, tool_registry=AdminAIToolRegistry(),
    )
    run = svc.run(
        _make_user(), "Hi.", correlation_id="c" * 500,
    )
    assert run.status == "completed"
    assert len(run.correlation_id.encode("utf-8")) <= 128
