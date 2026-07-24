"""Provider-response validation tests."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db

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
    user, _ = User.objects.get_or_create(username="respval")
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


def _service(fake):
    return AdminAIService(
        provider=fake, tool_registry=AdminAIToolRegistry(),
        max_model_turns=2, max_tool_calls=2,
    )


def test_max_tokens_response_fails_run():
    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="r1", content="", stop_reason="max_tokens",
    ))
    svc = _service(fake)
    run = svc.run(_make_user(), "Hi.")
    assert run.status == "failed"
    assert run.error_code == "provider.max_tokens"


def test_timeout_stop_reason_fails_run():
    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="r1", content="", stop_reason="timeout",
    ))
    svc = _service(fake)
    run = svc.run(_make_user(), "Hi.")
    assert run.status == "failed"
    assert run.error_code == "provider.timeout"


def test_final_response_requires_end_turn():
    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="r1", content="oops", stop_reason="",
    ))
    svc = _service(fake)
    run = svc.run(_make_user(), "Hi.")
    assert run.status == "failed"
    assert run.error_code == "provider.invalid_response"


def test_tool_calls_without_tool_use_rejected():
    reg = AdminAIToolRegistry()
    reg.register(_defn("t.read"), lambda ctx, **kw: AdminAIToolResult(
        tool_name="t.read", success=True, data={},
    ))
    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="r1",
        tool_calls=(AIModelToolCall(id="c1", name="t.read", arguments={}),),
        stop_reason="end_turn",  # WRONG — must be tool_use
    ))
    svc = AdminAIService(
        provider=fake, tool_registry=reg,
        max_model_turns=2, max_tool_calls=2,
    )
    run = svc.run(_make_user(), "Bad.")
    assert run.status == "failed"
    assert run.error_code == "provider.invalid_response"


def test_response_content_too_large_rejected():
    huge = "x" * 200000
    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="r1", content=huge, stop_reason="end_turn",
    ))
    svc = AdminAIService(
        provider=fake, tool_registry=AdminAIToolRegistry(),
        max_result_bytes=1024,
    )
    run = svc.run(_make_user(), "Hi.")
    assert run.status == "failed"
    assert run.error_code == "provider.response_too_large"
