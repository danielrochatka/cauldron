"""ITEM 1 — service-side validation of tool-return contracts."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.django_db

from cauldron_ai.contracts import AIModelResponse, AIModelToolCall
from cauldron_ai.testing import FakeAIModelProvider
from cauldron_ai_admin.models import AdminAIToolInvocation
from cauldron_ai_admin.service import AdminAIService
from cauldron_ai_admin.tools import (
    AdminAIToolDefinition,
    AdminAIToolError,
    AdminAIToolRegistry,
    AdminAIToolResult,
    RiskLevel,
)


def _defn(name="t.contract"):
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
    user, _ = User.objects.get_or_create(username="contract-user")
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


def _svc(reg, provider):
    return AdminAIService(
        provider=provider, tool_registry=reg,
        max_model_turns=3, max_tool_calls=5,
    )


def _tool_call(name="t.contract", cid="c1"):
    return AIModelToolCall(id=cid, name=name, arguments={})


def _one_call_provider(name="t.contract"):
    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="r1",
        tool_calls=(_tool_call(name=name),),
        stop_reason="tool_use",
    ))
    return fake


def test_result_with_success_false_fails_run_bad_return_type():
    reg = AdminAIToolRegistry()
    reg.register(
        _defn(),
        lambda ctx, **kw: AdminAIToolResult(
            tool_name="t.contract", success=False, data={},
        ),
    )
    run = _svc(reg, _one_call_provider()).run(_make_user(), "hi")
    assert run.status == "failed"
    assert run.error_code == "tool.bad_return_type"
    inv = AdminAIToolInvocation.objects.get(run=run)
    assert inv.status == "failed"


def test_result_with_non_string_message_fails_run_bad_return_type():
    reg = AdminAIToolRegistry()
    reg.register(
        _defn(),
        # dataclass frozen construction accepts int here; validation is
        # deferred to the service.
        lambda ctx, **kw: AdminAIToolResult(
            tool_name="t.contract", success=True, data={}, message=123,  # type: ignore[arg-type]
        ),
    )
    run = _svc(reg, _one_call_provider()).run(_make_user(), "hi")
    assert run.status == "failed"
    assert run.error_code == "tool.bad_return_type"


def test_result_with_non_json_data_fails_run_bad_return_type():
    reg = AdminAIToolRegistry()
    reg.register(
        _defn(),
        lambda ctx, **kw: AdminAIToolResult(
            tool_name="t.contract", success=True,
            data={"when": datetime(2026, 1, 1, tzinfo=timezone.utc)},
        ),
    )
    run = _svc(reg, _one_call_provider()).run(_make_user(), "hi")
    assert run.status == "failed"
    assert run.error_code == "tool.bad_return_type"


def test_error_with_empty_error_code_fails_run_bad_return_type():
    reg = AdminAIToolRegistry()
    reg.register(
        _defn(),
        lambda ctx, **kw: AdminAIToolError(
            tool_name="t.contract", error_code="", message="oops",
        ),
    )
    run = _svc(reg, _one_call_provider()).run(_make_user(), "hi")
    assert run.status == "failed"
    assert run.error_code == "tool.bad_return_type"


def test_result_mismatched_tool_name_fails_run_bad_return_type():
    reg = AdminAIToolRegistry()
    reg.register(
        _defn(),
        lambda ctx, **kw: AdminAIToolResult(
            tool_name="something.else", success=True, data={},
        ),
    )
    run = _svc(reg, _one_call_provider()).run(_make_user(), "hi")
    assert run.status == "failed"
    assert run.error_code == "tool.bad_return_type"


class _NotJSONSerialisable:
    """A tricky payload: passes strict JSON check for keys but fails json.dumps.

    We use a bytes payload — bytes are rejected by json.dumps but
    _assert_json_compatible checks recursively for non-string keys first
    and json.dumps to catch this.
    """


def test_complete_envelope_serialization_failure_records_not_serializable():
    """Simulate a value that passes construction but breaks json.dumps
    even after the earlier validators.

    We wire the service's envelope encoder to fail by monkeypatching
    ``json.dumps`` — the resulting fault must be ``tool.result_not_serializable``
    with no encoding fallback.
    """
    import json as json_mod
    from unittest.mock import patch

    reg = AdminAIToolRegistry()
    reg.register(
        _defn(),
        lambda ctx, **kw: AdminAIToolResult(
            tool_name="t.contract", success=True, data={"ok": True},
            message="",
        ),
    )
    real_dumps = json_mod.dumps

    def failing_dumps(obj, **kwargs):
        # Only fail on the outer envelope shape — everything else works.
        if (
            isinstance(obj, dict)
            and obj.get("tool_name") == "t.contract"
            and obj.get("success") is True
            and "data" in obj
            and "message" in obj
        ):
            raise TypeError("simulated serialization failure")
        return real_dumps(obj, **kwargs)

    with patch("cauldron_ai_admin.service.json.dumps", side_effect=failing_dumps):
        run = _svc(reg, _one_call_provider()).run(_make_user(), "hi")
    assert run.status == "failed"
    assert run.error_code == "tool.result_not_serializable"


def test_error_mismatched_tool_name_fails_run_bad_return_type():
    reg = AdminAIToolRegistry()
    reg.register(
        _defn(),
        lambda ctx, **kw: AdminAIToolError(
            tool_name="wrong.name", error_code="whatever", message="oops",
        ),
    )
    run = _svc(reg, _one_call_provider()).run(_make_user(), "hi")
    assert run.status == "failed"
    assert run.error_code == "tool.bad_return_type"
