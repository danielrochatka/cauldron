"""Shared helpers for cauldron-ai-admin tests."""
from __future__ import annotations


def make_assembly_service_for_tools(*tool_names: str):
    """Return a PromptAssemblyService with minimal templates for *tool_names*.

    Intended for unit tests that test service behaviour (tool execution, error
    codes, etc.) and need assembly to succeed but do not care about the
    assembled system prompt content.  An isolated registry is used so these
    tests are not affected by the process-level singleton state.
    """
    from cauldron_ai.prompt_templates import (
        AIGlobalOperatingPrompt,
        AIPromptTemplateRegistry,
        AIToolPromptTemplate,
    )
    from cauldron_ai_admin.prompt_assembly import PromptAssemblyService

    reg = AIPromptTemplateRegistry()
    reg.register_global_prompt(AIGlobalOperatingPrompt(
        version="v1", owning_module="test", body="Test global prompt.",
    ))
    for name in tool_names:
        reg.register_tool_template(AIToolPromptTemplate(
            tool_name=name,
            template_version="v1",
            owning_module="test",
            purpose=f"Test tool {name}.",
            supported_tasks=("task",),
            required_permission=None,
            risk_level="READ_ONLY",
            read_scope="scope.",
            write_scope="None.",
            preconditions=(),
            input_expectations="none.",
            result_behavior="result.",
            approval_requirements="None; read-only.",
            clarification_behavior="clarify.",
            refusal_behavior="refuse.",
            error_guidance="report.",
            positive_examples=(),
            boundary_examples=(),
        ))
    return PromptAssemblyService(registry=reg)
