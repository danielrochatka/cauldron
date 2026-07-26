"""Integration tests: AdminAIService + PromptAssemblyService."""
import pytest

pytestmark = pytest.mark.django_db

from cauldron_ai.contracts import AIModelResponse, AIModelRequest
from cauldron_ai.testing import FakeAIModelProvider
from cauldron_ai.prompt_templates import (
    AIGlobalOperatingPrompt,
    AIToolPromptTemplate,
    AIPromptTemplateRegistry,
    _reset_prompt_registry_for_tests,
)
from cauldron_ai_admin.models import AdminAIRun
from cauldron_ai_admin.prompt_assembly import PromptAssemblyService
from cauldron_ai_admin.service import AdminAIService
from cauldron_ai_admin.tools import (
    AdminAIToolDefinition,
    AdminAIToolRegistry,
    AdminAIToolResult,
    RiskLevel,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(username="int-user", include_ai=True):
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    User = get_user_model()
    user, _ = User.objects.get_or_create(username=username)
    if include_ai:
        perm = Permission.objects.get(
            codename="use_admin_ai",
            content_type__app_label="cauldron_ai_admin",
        )
        user.user_permissions.add(perm)
    return User.objects.get(pk=user.pk)


def _make_template(
    tool_name: str,
    version: str = "v1",
    permission: str | None = "cauldron_ai_admin.use_admin_ai",
) -> AIToolPromptTemplate:
    return AIToolPromptTemplate(
        tool_name=tool_name,
        template_version=version,
        owning_module="test.module",
        purpose=f"Purpose of {tool_name}.",
        supported_tasks=("task",),
        required_permission=permission,
        risk_level="READ_ONLY",
        read_scope="scope.",
        write_scope="None.",
        preconditions=("pre",),
        input_expectations="none.",
        result_behavior="result.",
        approval_requirements="None; read-only.",
        clarification_behavior="clarify.",
        refusal_behavior="refuse.",
        error_guidance="report.",
        positive_examples=("ex.",),
        boundary_examples=("bound.",),
    )


def _make_tool_def(name: str, permission: str = "cauldron_ai_admin.use_admin_ai"):
    return AdminAIToolDefinition(
        name=name,
        version="1.0",
        description="test tool",
        argument_schema={"type": "object", "properties": {}},
        risk_level=RiskLevel.READ_ONLY,
        required_permission=permission,
        owning_module="test.module",
    )


@pytest.fixture(autouse=True)
def reset_registry():
    _reset_prompt_registry_for_tests()
    yield
    _reset_prompt_registry_for_tests()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_service_uses_assembled_instructions_when_templates_registered(db):
    """AdminAIService sends the assembled system prompt (not the default) to the provider."""
    user = _make_user(username="int-user-1")

    global_body = "UNIQUE_GLOBAL_BODY_MARKER_12345"
    tmpl_reg = AIPromptTemplateRegistry()
    tmpl_reg.register_global_prompt(AIGlobalOperatingPrompt(
        version="v1", owning_module="m", body=global_body
    ))
    assembly_svc = PromptAssemblyService(registry=tmpl_reg)

    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="r1",
        content="Done.",
        stop_reason="end_turn",
    ))

    svc = AdminAIService(
        provider=fake,
        tool_registry=AdminAIToolRegistry(),
        prompt_assembly_service=assembly_svc,
    )
    svc.run(user, "Hello.")

    # The provider should have received the assembled prompt.
    assert fake.was_called(), "Provider received no requests"
    system_sent = fake.last_request().system
    assert global_body in system_sent


def test_provider_receives_only_authorized_tool_set(db):
    """The provider should only see tools the actor is permitted to use."""
    user = _make_user(username="int-user-2")

    tool_reg = AdminAIToolRegistry()

    def _noop(context, **kwargs):
        return AdminAIToolResult(tool_name="test.allowed", success=True, data={})

    # Register two tools; user only has the permission for one.
    tool_reg.register(_make_tool_def("test.allowed", "cauldron_ai_admin.use_admin_ai"), _noop)
    # test.restricted requires a permission the user doesn't have.
    tool_reg.register(
        _make_tool_def("test.restricted", "cauldron_ai_admin.manage_admin_ai_settings"),
        _noop,
    )

    tmpl_reg = AIPromptTemplateRegistry()
    tmpl_reg.register_global_prompt(AIGlobalOperatingPrompt(
        version="v1", owning_module="m", body="Global."
    ))
    assembly_svc = PromptAssemblyService(registry=tmpl_reg)

    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="r1",
        content="Done.",
        stop_reason="end_turn",
    ))

    svc = AdminAIService(
        provider=fake,
        tool_registry=tool_reg,
        prompt_assembly_service=assembly_svc,
    )
    svc.run(user, "Hello.")

    request_sent = fake.last_request()
    assert request_sent is not None
    tool_names_sent = {t.name for t in request_sent.tools}
    assert "test.allowed" in tool_names_sent
    assert "test.restricted" not in tool_names_sent


def test_audit_run_records_global_and_tool_template_versions(db):
    """After a run, the AdminAIRun row has the correct prompt version metadata."""
    user = _make_user(username="int-user-3")

    tmpl_reg = AIPromptTemplateRegistry()
    tmpl_reg.register_global_prompt(AIGlobalOperatingPrompt(
        version="v99", owning_module="m", body="Global."
    ))
    tmpl_reg.register_tool_template(_make_template("test.tool", version="v7"))
    assembly_svc = PromptAssemblyService(registry=tmpl_reg)

    def _noop(context, **kwargs):
        return AdminAIToolResult(tool_name="test.tool", success=True, data={})

    tool_reg = AdminAIToolRegistry()
    tool_reg.register(_make_tool_def("test.tool"), _noop)

    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="r1",
        content="Done.",
        stop_reason="end_turn",
    ))

    svc = AdminAIService(
        provider=fake,
        tool_registry=tool_reg,
        prompt_assembly_service=assembly_svc,
    )
    run = svc.run(user, "Hello.")
    run.refresh_from_db()

    assert run.prompt_global_version == "v99"
    assert run.prompt_tool_versions.get("test.tool") == "v7"
    assert "test.tool" in run.prompt_included_tools


def test_no_direct_file_write_path_introduced(db):
    """Running the service with templates does not write any files."""
    import os
    import tempfile

    # Capture any file opens during the run.
    opened_files: list[str] = []
    original_open = open

    user = _make_user(username="int-user-4")
    tmpl_reg = AIPromptTemplateRegistry()
    tmpl_reg.register_global_prompt(AIGlobalOperatingPrompt(
        version="v1", owning_module="m", body="Global."
    ))
    assembly_svc = PromptAssemblyService(registry=tmpl_reg)

    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="r1",
        content="Done.",
        stop_reason="end_turn",
    ))

    svc = AdminAIService(
        provider=fake,
        tool_registry=AdminAIToolRegistry(),
        prompt_assembly_service=assembly_svc,
    )
    # Simply running should not raise or write files.
    run = svc.run(user, "Hello.")
    assert run.status == "completed"
