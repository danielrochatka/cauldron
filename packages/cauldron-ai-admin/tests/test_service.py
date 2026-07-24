"""End-to-end tests for AdminAIService."""
import pytest

pytestmark = pytest.mark.django_db

from cauldron_ai.contracts import AIModelResponse, AIModelToolCall
from cauldron_ai.testing import FakeAIModelProvider
from cauldron_ai_admin.models import AdminAIRun, AdminAIToolInvocation
from cauldron_ai_admin.service import AdminAIService
from cauldron_ai_admin.tools import (
    AdminAIToolContext,
    AdminAIToolDefinition,
    AdminAIToolError,
    AdminAIToolRegistry,
    AdminAIToolResult,
    RiskLevel,
)


def _defn(
    name: str,
    risk: RiskLevel = RiskLevel.READ_ONLY,
    perm: str = "auth.view_user",
    schema: dict | None = None,
) -> AdminAIToolDefinition:
    return AdminAIToolDefinition(
        name=name,
        version="1.0",
        description="test tool",
        argument_schema=schema or {"type": "object", "properties": {}},
        risk_level=risk,
        required_permission=perm,
        owning_module="test.module",
    )


def _service(provider, registry) -> AdminAIService:
    return AdminAIService(
        provider=provider,
        tool_registry=registry,
        max_model_turns=3,
        max_tool_calls=5,
    )


def _make_user(username="ai-user", perms=("auth.view_user",), *, include_ai=True):
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    User = get_user_model()
    user, _ = User.objects.get_or_create(username=username)
    # Assign perms by codename by parsing app.codename.
    all_perms = list(perms)
    if include_ai:
        all_perms.append("cauldron_ai_admin.use_admin_ai")
    for spec in all_perms:
        app_label, codename = spec.split(".", 1)
        try:
            perm = Permission.objects.get(
                codename=codename, content_type__app_label=app_label,
            )
        except Permission.DoesNotExist:
            continue
        user.user_permissions.add(perm)
    return User.objects.get(pk=user.pk)  # refresh perm cache


def test_service_runs_with_no_tool_calls():
    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="r1",
        content="Hello there.",
        stop_reason="end_turn",
    ))
    reg = AdminAIToolRegistry()
    svc = _service(fake, reg)
    user = _make_user()
    run = svc.run(user, "Say hi.")
    assert run.status == "completed"
    assert run.final_response == "Hello there."
    assert run.tool_call_count == 0


def test_service_runs_read_only_tool_and_persists_invocation():
    reg = AdminAIToolRegistry()
    reg.register(
        _defn("t.read"),
        lambda ctx, **kw: AdminAIToolResult(
            tool_name="t.read", success=True, data={"ok": True},
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
    svc = _service(fake, reg)
    user = _make_user()
    run = svc.run(user, "Read something.")
    assert run.status == "completed"
    invocations = list(AdminAIToolInvocation.objects.filter(run=run))
    assert len(invocations) == 1
    inv = invocations[0]
    assert inv.tool_name == "t.read"
    assert inv.status == "completed"
    assert inv.risk_level == "READ_ONLY"


def test_service_runs_propose_tool_executes():
    reg = AdminAIToolRegistry()
    reg.register(
        _defn("t.propose", risk=RiskLevel.PROPOSE),
        lambda ctx, **kw: AdminAIToolResult(
            tool_name="t.propose", success=True,
            data={"cs_id": "cs-1", "status": "proposed"},
        ),
    )
    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="r1",
        tool_calls=(AIModelToolCall(id="c1", name="t.propose", arguments={}),),
        stop_reason="tool_use",
    ))
    fake.queue_response(AIModelResponse(
        provider_request_id="r2", content="proposal filed", stop_reason="end_turn",
    ))
    svc = _service(fake, reg)
    user = _make_user()
    run = svc.run(user, "Propose a change.")
    assert run.status == "completed"
    inv = AdminAIToolInvocation.objects.get(run=run)
    assert inv.status == "completed"
    assert inv.risk_level == "PROPOSE"


def test_service_maintenance_tool_is_denied():
    reg = AdminAIToolRegistry()
    reg.register(
        _defn("t.maint", risk=RiskLevel.MAINTENANCE),
        lambda ctx, **kw: (_ for _ in ()).throw(RuntimeError("must not run")),
    )
    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="r1",
        tool_calls=(AIModelToolCall(id="c1", name="t.maint", arguments={}),),
        stop_reason="tool_use",
    ))
    svc = _service(fake, reg)
    user = _make_user()
    run = svc.run(user, "Run maintenance.")
    assert run.status == "waiting_for_approval"
    assert run.error_code == "approval_required"
    inv = AdminAIToolInvocation.objects.get(run=run)
    assert inv.status == "denied"
    assert inv.error_code == "approval_required"


def test_service_privileged_tool_is_restricted():
    reg = AdminAIToolRegistry()
    reg.register(
        _defn("t.priv", risk=RiskLevel.PRIVILEGED),
        lambda ctx, **kw: (_ for _ in ()).throw(RuntimeError("must not run")),
    )
    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="r1",
        tool_calls=(AIModelToolCall(id="c1", name="t.priv", arguments={}),),
        stop_reason="tool_use",
    ))
    svc = _service(fake, reg)
    user = _make_user()
    run = svc.run(user, "Run privileged.")
    assert run.status == "waiting_for_approval"
    assert run.error_code == "restricted"
    inv = AdminAIToolInvocation.objects.get(run=run)
    assert inv.status == "denied"
    assert inv.error_code == "restricted"


def test_service_unknown_tool_fails_run():
    reg = AdminAIToolRegistry()
    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="r1",
        tool_calls=(AIModelToolCall(id="c1", name="does.not.exist", arguments={}),),
        stop_reason="tool_use",
    ))
    svc = _service(fake, reg)
    user = _make_user()
    run = svc.run(user, "Try nothing.")
    assert run.status == "failed"
    assert run.error_code == "tool.unknown"
    inv = AdminAIToolInvocation.objects.get(run=run)
    assert inv.tool_name == "does.not.exist"
    assert inv.status == "denied"


def test_service_oversized_arguments_rejected():
    reg = AdminAIToolRegistry()
    reg.register(_defn("t.read"), lambda ctx, **kw: AdminAIToolResult(
        tool_name="t.read", success=True, data={},
    ))
    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="r1",
        tool_calls=(
            AIModelToolCall(id="c1", name="t.read", arguments={"blob": "x" * 40000}),
        ),
        stop_reason="tool_use",
    ))
    svc = AdminAIService(
        provider=fake, tool_registry=reg,
        max_argument_bytes=1024, max_model_turns=3, max_tool_calls=5,
    )
    user = _make_user()
    run = svc.run(user, "Try big args.")
    assert run.status == "failed"
    assert run.error_code == "tool.arguments_too_large"


def test_service_permission_denied():
    reg = AdminAIToolRegistry()
    reg.register(
        _defn("t.read", perm="auth.change_user"),
        lambda ctx, **kw: AdminAIToolResult(tool_name="t.read"),
    )
    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="r1",
        tool_calls=(AIModelToolCall(id="c1", name="t.read", arguments={}),),
        stop_reason="tool_use",
    ))
    svc = _service(fake, reg)
    user = _make_user(perms=())
    run = svc.run(user, "Try read.")
    assert run.status == "failed"
    assert run.error_code == "tool.permission_denied"


def test_service_max_turns_exceeded():
    reg = AdminAIToolRegistry()
    reg.register(_defn("t.read"), lambda ctx, **kw: AdminAIToolResult(
        tool_name="t.read", success=True, data={"n": 1},
    ))
    fake = FakeAIModelProvider()
    for i in range(4):
        fake.queue_response(AIModelResponse(
            provider_request_id=f"r{i}",
            tool_calls=(
                AIModelToolCall(id=f"c{i}", name="t.read", arguments={}),
            ),
            stop_reason="tool_use",
        ))
    svc = AdminAIService(
        provider=fake, tool_registry=reg, max_model_turns=2, max_tool_calls=100,
    )
    user = _make_user()
    run = svc.run(user, "Loop forever.")
    assert run.status == "failed"
    assert run.error_code == "run.max_turns_exceeded"


def test_service_max_tool_calls_exceeded():
    reg = AdminAIToolRegistry()
    reg.register(_defn("t.read"), lambda ctx, **kw: AdminAIToolResult(
        tool_name="t.read", success=True, data={},
    ))
    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="r1",
        tool_calls=(
            AIModelToolCall(id="c1", name="t.read", arguments={}),
            AIModelToolCall(id="c2", name="t.read", arguments={}),
            AIModelToolCall(id="c3", name="t.read", arguments={}),
        ),
        stop_reason="tool_use",
    ))
    svc = AdminAIService(
        provider=fake, tool_registry=reg,
        max_model_turns=5, max_tool_calls=2,
    )
    user = _make_user()
    run = svc.run(user, "Batch.")
    assert run.status == "failed"
    assert run.error_code == "run.max_tool_calls_exceeded"


def test_service_duplicate_call_id_rejected():
    """Duplicate ids inside a single response are rejected at the provider
    validation layer (``provider.invalid_response``)."""
    reg = AdminAIToolRegistry()
    reg.register(_defn("t.read"), lambda ctx, **kw: AdminAIToolResult(
        tool_name="t.read", success=True, data={},
    ))
    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="r1",
        tool_calls=(
            AIModelToolCall(id="dup", name="t.read", arguments={}),
            AIModelToolCall(id="dup", name="t.read", arguments={}),
        ),
        stop_reason="tool_use",
    ))
    svc = _service(fake, reg)
    user = _make_user()
    run = svc.run(user, "Dupe.")
    assert run.status == "failed"
    assert run.error_code == "provider.invalid_response"


def test_service_duplicate_call_id_across_turns_rejected():
    """Same tool-call id reused across separate turns is a
    ``tool.duplicate_call_id`` audit error."""
    reg = AdminAIToolRegistry()
    reg.register(_defn("t.read"), lambda ctx, **kw: AdminAIToolResult(
        tool_name="t.read", success=True, data={},
    ))
    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="r1",
        tool_calls=(AIModelToolCall(id="dup", name="t.read", arguments={}),),
        stop_reason="tool_use",
    ))
    fake.queue_response(AIModelResponse(
        provider_request_id="r2",
        tool_calls=(AIModelToolCall(id="dup", name="t.read", arguments={}),),
        stop_reason="tool_use",
    ))
    svc = _service(fake, reg)
    user = _make_user()
    run = svc.run(user, "Dupe.")
    assert run.status == "failed"
    assert run.error_code == "tool.duplicate_call_id"


def test_service_invalid_arguments_rejected():
    reg = AdminAIToolRegistry()
    reg.register(
        _defn(
            "t.strict",
            schema={
                "type": "object",
                "required": ["collection"],
                "properties": {"collection": {"type": "string"}},
            },
        ),
        lambda ctx, **kw: AdminAIToolResult(tool_name="t.strict"),
    )
    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="r1",
        tool_calls=(AIModelToolCall(id="c1", name="t.strict", arguments={}),),
        stop_reason="tool_use",
    ))
    svc = _service(fake, reg)
    user = _make_user()
    run = svc.run(user, "Bad args.")
    assert run.status == "failed"
    assert run.error_code == "tool.invalid_arguments"


def test_service_records_denied_invocation_for_denials():
    """Denied maintenance/privileged tools still leave an audit row."""
    reg = AdminAIToolRegistry()
    reg.register(
        _defn("t.maint", risk=RiskLevel.MAINTENANCE),
        lambda ctx, **kw: AdminAIToolResult(tool_name="t.maint"),
    )
    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="r1",
        tool_calls=(AIModelToolCall(id="c1", name="t.maint", arguments={}),),
        stop_reason="tool_use",
    ))
    svc = _service(fake, reg)
    user = _make_user()
    run = svc.run(user, "Do maintenance.")
    inv = AdminAIToolInvocation.objects.get(run=run)
    assert inv.status == "denied"
    assert run.tool_call_count == 1


def test_service_requires_active_actor():
    from django.core.exceptions import PermissionDenied
    reg = AdminAIToolRegistry()
    fake = FakeAIModelProvider()
    svc = _service(fake, reg)
    with pytest.raises(PermissionDenied):
        svc.run(None, "Hello.")


def test_service_requires_non_empty_request():
    reg = AdminAIToolRegistry()
    fake = FakeAIModelProvider()
    svc = _service(fake, reg)
    user = _make_user()
    with pytest.raises(ValueError):
        svc.run(user, "")


def test_service_admin_ai_tool_error_with_mismatched_tool_name_fails_run():
    """Handler returning AdminAIToolError with a mismatched tool_name is
    treated as a contract violation and fails the run."""
    reg = AdminAIToolRegistry()
    reg.register(
        _defn("t.read"),
        lambda ctx, **kw: AdminAIToolError(
            tool_name="t.other", error_code="whatever", message="oops",
        ),
    )
    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="r1",
        tool_calls=(AIModelToolCall(id="c1", name="t.read", arguments={}),),
        stop_reason="tool_use",
    ))
    svc = _service(fake, reg)
    user = _make_user()
    run = svc.run(user, "Bad handler.")
    assert run.status == "failed"
    assert run.error_code == "tool.bad_return_type"


def test_service_stores_provider_request_id():
    reg = AdminAIToolRegistry()
    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="prov-req-1", content="ok", stop_reason="end_turn",
    ))
    svc = _service(fake, reg)
    user = _make_user()
    run = svc.run(user, "Say hi.")
    assert run.provider_request_id == "prov-req-1"
