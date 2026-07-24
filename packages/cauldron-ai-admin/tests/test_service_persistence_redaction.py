"""End-to-end redaction of persisted user_request/result_summary.

Every text field written into the audit trail must be routed through
``redact()`` so a sensitive substring in the user request or a tool
result cannot land in the durable row.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db

from cauldron_ai.contracts import AIModelResponse, AIModelToolCall
from cauldron_ai.testing import FakeAIModelProvider
from cauldron_ai_admin.models import AdminAIRun, AdminAIToolInvocation
from cauldron_ai_admin.service import AdminAIService
from cauldron_ai_admin.tools import (
    AdminAIToolDefinition,
    AdminAIToolRegistry,
    AdminAIToolResult,
    RiskLevel,
)


def _make_user():
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    User = get_user_model()
    user, _ = User.objects.get_or_create(username="redactuser")
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


def _defn(name="t.read"):
    return AdminAIToolDefinition(
        name=name, version="1.0", description="",
        argument_schema={"type": "object", "properties": {}},
        risk_level=RiskLevel.READ_ONLY,
        required_permission="auth.view_user",
        owning_module="cauldron.test",
    )


def test_user_request_containing_api_key_is_redacted_on_persistence():
    """A user request containing an ``api_key`` fragment must be redacted
    before the AdminAIRun row is created."""
    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="r1", content="ok", stop_reason="end_turn",
    ))
    svc = AdminAIService(
        provider=fake, tool_registry=AdminAIToolRegistry(),
    )
    user = _make_user()
    request_text = '{"api_key": "secret123", "please": "help"}'
    run = svc.run(user, request_text)
    persisted = AdminAIRun.objects.get(run_id=run.run_id)
    # The raw secret must not appear anywhere in the persisted text.
    assert "secret123" not in persisted.user_request


def test_tool_result_password_value_is_redacted_in_result_summary():
    """A tool result payload that carries a ``password`` key must have
    the value scrubbed before it is persisted."""
    reg = AdminAIToolRegistry()
    reg.register(
        _defn("t.read"),
        lambda ctx, **kw: AdminAIToolResult(
            tool_name="t.read", success=True,
            data={"user": "alice", "password": "hunter2"},
        ),
    )
    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="r1",
        tool_calls=(AIModelToolCall(id="c1", name="t.read", arguments={}),),
        stop_reason="tool_use",
    ))
    fake.queue_response(AIModelResponse(
        provider_request_id="r2", content="done", stop_reason="end_turn",
    ))
    svc = AdminAIService(
        provider=fake, tool_registry=reg,
        max_model_turns=3, max_tool_calls=5,
    )
    user = _make_user()
    run = svc.run(user, "Read stuff.")
    inv = AdminAIToolInvocation.objects.get(run=run)
    # The raw password value must be scrubbed from the summary.
    assert "hunter2" not in inv.result_summary
    # But the safe key/value context remains.
    assert "alice" in inv.result_summary or "user" in inv.result_summary


def test_final_response_containing_json_secret_is_redacted():
    """When the model's final response is a JSON blob carrying a
    sensitive key, that value must be scrubbed before it lands in the
    ``final_response`` column."""
    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="r1",
        content='{"api_key": "sk-leaked", "note": "here you go"}',
        stop_reason="end_turn",
    ))
    svc = AdminAIService(
        provider=fake, tool_registry=AdminAIToolRegistry(),
    )
    user = _make_user()
    run = svc.run(user, "Give me a summary.")
    persisted = AdminAIRun.objects.get(run_id=run.run_id)
    assert "sk-leaked" not in persisted.final_response
