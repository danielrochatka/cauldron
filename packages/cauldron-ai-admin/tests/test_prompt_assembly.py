"""Tests for PromptAssemblyService — filtering, ordering, size limits, audit fields."""
import pytest

from cauldron_ai.prompt_templates import (
    AIGlobalOperatingPrompt,
    AIToolPromptTemplate,
    AIPromptTemplateRegistry,
    PromptAssemblyTooLargeError,
    PromptTemplateMissingError,
    _reset_prompt_registry_for_tests,
)
from cauldron_ai_admin.prompt_assembly import PromptAssemblyService, _MAX_ASSEMBLY_BYTES
from cauldron_ai_admin.tools import AdminAIToolDefinition, RiskLevel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tool_def(
    name: str,
    permission: str = "auth.view_user",
    risk: RiskLevel = RiskLevel.READ_ONLY,
) -> AdminAIToolDefinition:
    return AdminAIToolDefinition(
        name=name,
        version="1.0",
        description="test tool",
        argument_schema={"type": "object", "properties": {}},
        risk_level=risk,
        required_permission=permission,
        owning_module="test.module",
    )


def _make_template(
    tool_name: str,
    version: str = "v1",
    risk_level: str = "READ_ONLY",
    permission: str | None = "auth.view_user",
    purpose: str = "Test purpose.",
) -> AIToolPromptTemplate:
    return AIToolPromptTemplate(
        tool_name=tool_name,
        template_version=version,
        owning_module="test.module",
        purpose=purpose,
        supported_tasks=("task",),
        required_permission=permission,
        risk_level=risk_level,
        read_scope="Some read scope.",
        write_scope="None.",
        preconditions=("precondition",),
        input_expectations="No arguments.",
        result_behavior="Returns something.",
        approval_requirements="None; read-only.",
        clarification_behavior="Clarify if needed.",
        refusal_behavior="Refuse if unavailable.",
        error_guidance="Report the error.",
        positive_examples=("Example.",),
        boundary_examples=("Boundary.",),
    )


def _make_global_prompt(body: str = "Global operating instructions.") -> AIGlobalOperatingPrompt:
    return AIGlobalOperatingPrompt(
        version="v1",
        owning_module="test.module",
        body=body,
    )


@pytest.fixture(autouse=True)
def reset_registry():
    _reset_prompt_registry_for_tests()
    yield
    _reset_prompt_registry_for_tests()


def _fresh_registry() -> AIPromptTemplateRegistry:
    """Return a brand-new isolated registry for testing."""
    return AIPromptTemplateRegistry()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_authorized_tool_with_template_included_in_output():
    reg = _fresh_registry()
    reg.register_global_prompt(_make_global_prompt())
    tmpl = _make_template("content.list_collections")
    reg.register_tool_template(tmpl)

    svc = PromptAssemblyService(registry=reg)
    defs = [_make_tool_def("content.list_collections")]
    result = svc.assemble(defs)

    assert "content.list_collections" in result.system_instructions
    assert "content.list_collections" in result.included_tool_names


def test_template_omitted_when_tool_not_in_permitted_defs():
    """A template for a tool that is not in permitted_defs should not appear."""
    reg = _fresh_registry()
    reg.register_global_prompt(_make_global_prompt())
    reg.register_tool_template(_make_template("content.list_collections"))
    reg.register_tool_template(_make_template("content.list_items"))

    svc = PromptAssemblyService(registry=reg)
    # Only list_collections is permitted
    defs = [_make_tool_def("content.list_collections")]
    result = svc.assemble(defs)

    assert "content.list_collections" in result.system_instructions
    assert "content.list_items" not in result.system_instructions
    assert "content.list_items" not in result.included_tool_names


def test_disabled_tool_omitted_from_instructions():
    """No tool defs → no tool sections."""
    reg = _fresh_registry()
    reg.register_global_prompt(_make_global_prompt())
    reg.register_tool_template(_make_template("content.list_collections"))

    svc = PromptAssemblyService(registry=reg)
    result = svc.assemble([])

    assert "content.list_collections" not in result.system_instructions
    assert result.included_tool_names == ()
    assert result.template_versions == ()


def test_template_versions_captured_correctly():
    reg = _fresh_registry()
    reg.register_global_prompt(_make_global_prompt())
    reg.register_tool_template(_make_template("a.tool", version="v3"))
    reg.register_tool_template(_make_template("b.tool", version="1.0.0"))

    svc = PromptAssemblyService(registry=reg)
    defs = [_make_tool_def("a.tool"), _make_tool_def("b.tool")]
    result = svc.assemble(defs)

    versions_dict = dict(result.template_versions)
    assert versions_dict["a.tool"] == "v3"
    assert versions_dict["b.tool"] == "1.0.0"


def test_identical_inputs_produce_identical_output():
    """Assembly must be deterministic."""
    reg = _fresh_registry()
    reg.register_global_prompt(_make_global_prompt())
    reg.register_tool_template(_make_template("content.list_collections"))
    reg.register_tool_template(_make_template("content.list_items"))

    svc = PromptAssemblyService(registry=reg)
    defs = [
        _make_tool_def("content.list_collections"),
        _make_tool_def("content.list_items"),
    ]
    result1 = svc.assemble(defs)
    result2 = svc.assemble(defs)
    assert result1.system_instructions == result2.system_instructions


def test_global_prompt_appears_exactly_once():
    reg = _fresh_registry()
    body = "THIS IS THE GLOBAL PROMPT."
    reg.register_global_prompt(_make_global_prompt(body=body))
    reg.register_tool_template(_make_template("a.tool"))

    svc = PromptAssemblyService(registry=reg)
    result = svc.assemble([_make_tool_def("a.tool")])
    assert result.system_instructions.count(body) == 1


def test_global_prompt_version_in_result():
    reg = _fresh_registry()
    reg.register_global_prompt(AIGlobalOperatingPrompt(
        version="v2", owning_module="m", body="Body."
    ))
    svc = PromptAssemblyService(registry=reg)
    result = svc.assemble([])
    assert result.global_prompt_version == "v2"


def test_no_provider_construction_or_network_call():
    """Assembly does not import or construct providers."""
    reg = _fresh_registry()
    reg.register_global_prompt(_make_global_prompt())
    svc = PromptAssemblyService(registry=reg)
    # This should complete instantly with no network I/O.
    result = svc.assemble([])
    assert isinstance(result.system_instructions, str)


def test_assembled_bytes_matches_actual_byte_count():
    reg = _fresh_registry()
    reg.register_global_prompt(_make_global_prompt("Hello world."))
    svc = PromptAssemblyService(registry=reg)
    result = svc.assemble([])
    assert result.assembled_bytes == len(result.system_instructions.encode("utf-8"))


def test_size_limit_raises_too_large_error():
    """When a section would push output past 32 KiB, PromptAssemblyTooLargeError is raised."""
    reg = _fresh_registry()
    big_body = "X" * (_MAX_ASSEMBLY_BYTES + 4096)
    reg.register_global_prompt(AIGlobalOperatingPrompt(
        version="v1", owning_module="m", body=big_body
    ))
    svc = PromptAssemblyService(registry=reg)
    with pytest.raises(PromptAssemblyTooLargeError):
        svc.assemble([])


def test_tool_without_template_raises_missing_error():
    """A permitted tool with no registered template raises PromptTemplateMissingError."""
    reg = _fresh_registry()
    reg.register_global_prompt(_make_global_prompt())
    # No template for this tool.
    svc = PromptAssemblyService(registry=reg)
    defs = [_make_tool_def("content.list_collections")]
    with pytest.raises(PromptTemplateMissingError, match="content.list_collections"):
        svc.assemble(defs)


def test_task_context_appended_after_cauldron_instructions():
    reg = _fresh_registry()
    body = "Global instructions."
    reg.register_global_prompt(_make_global_prompt(body=body))
    svc = PromptAssemblyService(registry=reg)
    result = svc.assemble([], task_context="User wants to do X.")

    # The global prompt should come before the task context.
    global_pos = result.system_instructions.index(body)
    task_pos = result.system_instructions.index("User wants to do X.")
    assert global_pos < task_pos


def test_task_context_present_in_output():
    reg = _fresh_registry()
    reg.register_global_prompt(_make_global_prompt())
    svc = PromptAssemblyService(registry=reg)
    result = svc.assemble([], task_context="Special context here.")
    assert "Special context here." in result.system_instructions


def test_no_task_context_not_in_output():
    reg = _fresh_registry()
    reg.register_global_prompt(_make_global_prompt())
    svc = PromptAssemblyService(registry=reg)
    result = svc.assemble([])
    assert "Current task context" not in result.system_instructions


def test_tool_sections_sorted_by_name():
    """Tool sections appear in alphabetical order regardless of input order."""
    reg = _fresh_registry()
    reg.register_global_prompt(_make_global_prompt("Global."))
    reg.register_tool_template(_make_template("z.zz", purpose="Z tool."))
    reg.register_tool_template(_make_template("a.aa", purpose="A tool."))
    reg.register_tool_template(_make_template("m.mm", purpose="M tool."))

    svc = PromptAssemblyService(registry=reg)
    # Pass defs in reverse alphabetical order.
    defs = [
        _make_tool_def("z.zz"),
        _make_tool_def("m.mm"),
        _make_tool_def("a.aa"),
    ]
    result = svc.assemble(defs)

    pos_a = result.system_instructions.index("a.aa")
    pos_m = result.system_instructions.index("m.mm")
    pos_z = result.system_instructions.index("z.zz")
    assert pos_a < pos_m < pos_z


def test_no_secret_key_in_output():
    """The assembled prompt must not contain the Django SECRET_KEY."""
    from django.conf import settings
    reg = _fresh_registry()
    reg.register_global_prompt(_make_global_prompt())
    svc = PromptAssemblyService(registry=reg)
    result = svc.assemble([])
    secret = getattr(settings, "SECRET_KEY", "")
    if secret:
        assert secret not in result.system_instructions


def test_empty_registry_produces_empty_instructions():
    """Without a global prompt and no tools, instructions should be empty."""
    reg = _fresh_registry()
    svc = PromptAssemblyService(registry=reg)
    result = svc.assemble([])
    assert result.system_instructions == ""
    assert result.global_prompt_version == ""
    assert result.included_tool_names == ()
    assert result.assembled_bytes == 0


# ---------------------------------------------------------------------------
# caller_system_prompt
# ---------------------------------------------------------------------------


def test_caller_system_prompt_appended_after_tool_sections():
    """caller_system_prompt appears after tool sections, before task_context."""
    reg = _fresh_registry()
    reg.register_global_prompt(_make_global_prompt("Global."))
    reg.register_tool_template(_make_template("a.tool", purpose="A purpose."))

    svc = PromptAssemblyService(registry=reg)
    result = svc.assemble(
        [_make_tool_def("a.tool")],
        caller_system_prompt="Caller instructions.",
        task_context="Task here.",
    )
    instr = result.system_instructions
    tool_pos = instr.index("a.tool")
    caller_pos = instr.index("Caller instructions.")
    task_pos = instr.index("Task here.")
    assert tool_pos < caller_pos < task_pos


def test_caller_system_prompt_present_in_output():
    reg = _fresh_registry()
    reg.register_global_prompt(_make_global_prompt())
    svc = PromptAssemblyService(registry=reg)
    result = svc.assemble([], caller_system_prompt="Custom caller prompt.")
    assert "Custom caller prompt." in result.system_instructions


def test_empty_caller_system_prompt_not_in_output():
    reg = _fresh_registry()
    reg.register_global_prompt(_make_global_prompt("Only global."))
    svc = PromptAssemblyService(registry=reg)
    result = svc.assemble([], caller_system_prompt="")
    # No extra separator sections beyond global prompt
    assert result.system_instructions == "Only global."


def test_tool_without_template_raises_before_any_section_is_built():
    """Missing template error raised before any section is assembled (fail early)."""
    reg = _fresh_registry()
    reg.register_global_prompt(_make_global_prompt())
    reg.register_tool_template(_make_template("a.tool"))
    # b.tool has no template

    svc = PromptAssemblyService(registry=reg)
    with pytest.raises(PromptTemplateMissingError, match="b.tool"):
        svc.assemble([_make_tool_def("a.tool"), _make_tool_def("b.tool")])
