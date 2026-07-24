"""Full JSON Schema validation of tool arguments (Draft-07 semantics)."""
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
    ToolArgumentValidationError,
    validate_tool_arguments,
)


def _make_user():
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    User = get_user_model()
    user, _ = User.objects.get_or_create(username="js")
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


NESTED_SCHEMA = {
    "type": "object",
    "properties": {
        "meta": {
            "type": "object",
            "properties": {
                "verb": {"type": "string", "enum": ["read", "write"]},
                "count": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": ["verb"],
            "additionalProperties": False,
        },
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
        },
    },
    "required": ["meta"],
    "additionalProperties": False,
}


def test_valid_arguments_pass():
    validate_tool_arguments(
        NESTED_SCHEMA,
        {"meta": {"verb": "read", "count": 3}, "tags": ["a"]},
    )


def test_nested_required_field_missing_rejected():
    with pytest.raises(ToolArgumentValidationError):
        validate_tool_arguments(
            NESTED_SCHEMA, {"meta": {"count": 3}},
        )


def test_additional_properties_rejected():
    with pytest.raises(ToolArgumentValidationError):
        validate_tool_arguments(
            NESTED_SCHEMA,
            {"meta": {"verb": "read"}, "extra": "sneaky"},
        )


def test_enum_rejection():
    with pytest.raises(ToolArgumentValidationError):
        validate_tool_arguments(
            NESTED_SCHEMA, {"meta": {"verb": "delete"}},
        )


def test_min_max_integer_bounds():
    with pytest.raises(ToolArgumentValidationError):
        validate_tool_arguments(
            NESTED_SCHEMA, {"meta": {"verb": "read", "count": 11}},
        )


def test_boolean_is_not_integer():
    with pytest.raises(ToolArgumentValidationError):
        validate_tool_arguments(
            NESTED_SCHEMA, {"meta": {"verb": "read", "count": True}},
        )


def test_min_items_array():
    with pytest.raises(ToolArgumentValidationError):
        validate_tool_arguments(
            NESTED_SCHEMA, {"meta": {"verb": "read"}, "tags": []},
        )


def test_non_json_serialisable_arguments_rejected():
    schema = {"type": "object", "additionalProperties": True}

    class NotJSON:
        pass

    with pytest.raises(ToolArgumentValidationError):
        validate_tool_arguments(schema, {"custom": NotJSON()})


def test_service_rejects_invalid_arguments_and_records_denied():
    reg = AdminAIToolRegistry()
    reg.register(
        AdminAIToolDefinition(
            name="t.strict", version="1.0", description="",
            argument_schema=NESTED_SCHEMA,
            risk_level=RiskLevel.READ_ONLY,
            required_permission="auth.view_user",
            owning_module="cauldron.test",
        ),
        lambda ctx, **kw: AdminAIToolResult(
            tool_name="t.strict", success=True, data={},
        ),
    )
    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="r1",
        tool_calls=(AIModelToolCall(
            id="c1", name="t.strict",
            arguments={"meta": {}, "extra": "x"},
        ),),
        stop_reason="tool_use",
    ))
    svc = AdminAIService(
        provider=fake, tool_registry=reg,
        max_model_turns=2, max_tool_calls=2,
    )
    run = svc.run(_make_user(), "Do it.")
    assert run.status == "failed"
    assert run.error_code == "tool.invalid_arguments"
