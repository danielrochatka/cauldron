"""ITEM 4 — bounded correlation ID propagation throughout the pipeline."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db

from cauldron_ai.contracts import AIModelRequest, AIModelResponse, AIModelToolCall
from cauldron_ai_admin.models import AdminAIToolInvocation
from cauldron_ai_admin.service import (
    MAX_CORRELATION_ID_BYTES,
    AdminAIService,
)
from cauldron_ai_admin.tools import (
    AdminAIToolContext,
    AdminAIToolDefinition,
    AdminAIToolRegistry,
    AdminAIToolResult,
    RiskLevel,
)


class _CapturingProvider:
    """A provider that records requests and pre-configured responses."""

    name = "capturing"

    def __init__(self, responses):
        self._responses = list(responses)
        self.seen_requests: list[AIModelRequest] = []

    def complete(self, request):
        self.seen_requests.append(request)
        return self._responses.pop(0)


def _defn(name="t.corr"):
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
    user, _ = User.objects.get_or_create(username="corr-user")
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


def test_correlation_id_truncated_to_bounded_form_everywhere():
    seen_contexts: list[AdminAIToolContext] = []

    def handler(ctx, **kw):
        seen_contexts.append(ctx)
        return AdminAIToolResult(tool_name="t.corr", success=True, data={})

    reg = AdminAIToolRegistry()
    reg.register(_defn(), handler)

    provider = _CapturingProvider([
        AIModelResponse(
            provider_request_id="r1",
            tool_calls=(AIModelToolCall(id="c1", name="t.corr", arguments={}),),
            stop_reason="tool_use",
        ),
        AIModelResponse(
            provider_request_id="r2", content="done", stop_reason="end_turn",
        ),
    ])
    svc = AdminAIService(
        provider=provider, tool_registry=reg,
        max_model_turns=3, max_tool_calls=5,
    )

    long_id = "a" * 200  # 200 ASCII bytes
    run = svc.run(_make_user(), "Hi.", correlation_id=long_id)
    assert run.status == "completed"

    bounded = "a" * MAX_CORRELATION_ID_BYTES

    # 1. AdminAIRun.correlation_id
    assert run.correlation_id == bounded
    # 2. AIModelRequest.correlation_id — all provider requests see the bounded form
    assert provider.seen_requests, "expected at least one provider request"
    for req in provider.seen_requests:
        assert req.correlation_id == bounded
    # 3. AdminAIToolContext.correlation_id — recorded by the handler
    assert seen_contexts, "handler was never invoked"
    for ctx in seen_contexts:
        assert ctx.correlation_id == bounded
    # 4. AdminAIToolInvocation.correlation_id — every recorded invocation
    for inv in AdminAIToolInvocation.objects.filter(run=run):
        assert inv.correlation_id == bounded


def test_correlation_id_multibyte_truncation_preserves_utf8():
    """Emoji (4-byte UTF-8) padded to overflow must truncate on a
    codepoint boundary — no partial bytes / replacement characters."""

    def handler(ctx, **kw):
        return AdminAIToolResult(tool_name="t.corr", success=True, data={})

    reg = AdminAIToolRegistry()
    reg.register(_defn(), handler)

    provider = _CapturingProvider([
        AIModelResponse(
            provider_request_id="r1",
            tool_calls=(AIModelToolCall(id="c1", name="t.corr", arguments={}),),
            stop_reason="tool_use",
        ),
        AIModelResponse(
            provider_request_id="r2", content="done", stop_reason="end_turn",
        ),
    ])
    svc = AdminAIService(
        provider=provider, tool_registry=reg,
        max_model_turns=3, max_tool_calls=5,
    )
    # 40 emoji glyphs = 40 * 4 = 160 bytes — overflows the 128-byte cap.
    corr = "\U0001F600" * 40
    run = svc.run(_make_user(), "Hi.", correlation_id=corr)
    assert run.status == "completed"

    # 128 / 4 = 32 complete emoji glyphs fit.
    expected = "\U0001F600" * 32
    assert run.correlation_id == expected
    for req in provider.seen_requests:
        assert req.correlation_id == expected
    for inv in AdminAIToolInvocation.objects.filter(run=run):
        assert inv.correlation_id == expected
    # No replacement chars anywhere.
    assert "�" not in run.correlation_id
