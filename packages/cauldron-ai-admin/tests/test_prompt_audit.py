"""Tests for AdminAIRun prompt metadata fields and their persistence."""
import pytest

pytestmark = pytest.mark.django_db

from cauldron_ai.contracts import AIModelResponse
from cauldron_ai.testing import FakeAIModelProvider
from cauldron_ai.prompt_templates import (
    AIGlobalOperatingPrompt,
    AIToolPromptTemplate,
    AIPromptTemplateRegistry,
)
from cauldron_ai.testing import reset_prompt_registry_for_tests
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

def _make_user(username="audit-user"):
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    User = get_user_model()
    user, _ = User.objects.get_or_create(username=username)
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
        purpose="Test purpose.",
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


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_registry():
    reset_prompt_registry_for_tests()
    yield
    reset_prompt_registry_for_tests()


# ---------------------------------------------------------------------------
# Default field values
# ---------------------------------------------------------------------------


def test_adminairun_prompt_global_version_defaults_to_empty(db):
    user = _make_user(username="default-test-user")
    run = AdminAIRun.objects.create(
        actor=user,
        status="created",
        provider_name="test",
        user_request="hello",
    )
    assert run.prompt_global_version == ""


def test_adminairun_prompt_tool_versions_defaults_to_empty_dict(db):
    user = _make_user(username="default-test-user-2")
    run = AdminAIRun.objects.create(
        actor=user,
        status="created",
        provider_name="test",
        user_request="hello",
    )
    assert run.prompt_tool_versions == {}


def test_adminairun_prompt_included_tools_defaults_to_empty_list(db):
    user = _make_user(username="default-test-user-3")
    run = AdminAIRun.objects.create(
        actor=user,
        status="created",
        provider_name="test",
        user_request="hello",
    )
    assert run.prompt_included_tools == []


# ---------------------------------------------------------------------------
# Migration safety: existing rows have safe defaults
# ---------------------------------------------------------------------------


def test_existing_rows_have_safe_defaults(db):
    """Rows created without the new fields have empty/dict/list defaults."""
    user = _make_user(username="existing-row-user")
    run = AdminAIRun.objects.create(
        actor=user,
        status="created",
        provider_name="test",
        user_request="hello",
    )
    # Force-refresh from the database.
    run.refresh_from_db()
    assert run.prompt_global_version == ""
    assert run.prompt_tool_versions == {}
    assert run.prompt_included_tools == []


# ---------------------------------------------------------------------------
# After a run completes, new fields have expected values
# ---------------------------------------------------------------------------


def _simple_tool_handler(context, **kwargs):
    return AdminAIToolResult(
        tool_name="test.simple",
        success=True,
        data={"ok": True},
    )


def test_run_persists_prompt_global_version(db):
    user = _make_user(username="run-audit-user-1")

    # Set up isolated registries.
    tmpl_reg = AIPromptTemplateRegistry()
    tmpl_reg.register_global_prompt(AIGlobalOperatingPrompt(
        version="v42", owning_module="m", body="Global prompt text."
    ))
    assembly_svc = PromptAssemblyService(registry=tmpl_reg)

    tool_reg = AdminAIToolRegistry()

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

    assert run.prompt_global_version == "v42"


def test_run_persists_prompt_tool_versions(db):
    user = _make_user(username="run-audit-user-2")

    tmpl_reg = AIPromptTemplateRegistry()
    tmpl_reg.register_global_prompt(AIGlobalOperatingPrompt(
        version="v1", owning_module="m", body="Global."
    ))
    tmpl_reg.register_tool_template(_make_template("test.simple", version="v3"))
    assembly_svc = PromptAssemblyService(registry=tmpl_reg)

    tool_reg = AdminAIToolRegistry()
    tool_reg.register(_make_tool_def("test.simple"), _simple_tool_handler)

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

    assert run.prompt_tool_versions.get("test.simple") == "v3"


def test_run_persists_prompt_included_tools(db):
    user = _make_user(username="run-audit-user-3")

    tmpl_reg = AIPromptTemplateRegistry()
    tmpl_reg.register_global_prompt(AIGlobalOperatingPrompt(
        version="v1", owning_module="m", body="Global."
    ))
    tmpl_reg.register_tool_template(_make_template("test.simple"))
    assembly_svc = PromptAssemblyService(registry=tmpl_reg)

    tool_reg = AdminAIToolRegistry()
    tool_reg.register(_make_tool_def("test.simple"), _simple_tool_handler)

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

    assert "test.simple" in run.prompt_included_tools


def test_provider_and_model_audit_fields_remain_intact(db):
    """The prompt metadata fields must not clobber existing audit fields."""
    user = _make_user(username="run-audit-user-4")

    tmpl_reg = AIPromptTemplateRegistry()
    tmpl_reg.register_global_prompt(AIGlobalOperatingPrompt(
        version="v1", owning_module="m", body="Global."
    ))
    assembly_svc = PromptAssemblyService(registry=tmpl_reg)

    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="prov-req-123",
        content="Done.",
        stop_reason="end_turn",
    ))

    svc = AdminAIService(
        provider=fake,
        tool_registry=AdminAIToolRegistry(),
        prompt_assembly_service=assembly_svc,
    )
    run = svc.run(user, "Hello.")
    run.refresh_from_db()

    assert run.provider_name == "fake"
    assert run.status == "completed"
    assert run.final_response == "Done."
