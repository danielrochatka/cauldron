"""Permission-aware prompt assembly for Admin AI requests.

Assembles a system prompt from the registered global operating prompt
and per-tool templates, filtered to the set of tools the actor is
permitted to use.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cauldron_ai.prompt_templates import (
    AIPromptAssemblyResult,
    AIPromptTemplateRegistry,
    AIToolPromptTemplate,
    get_prompt_template_registry,
)

if TYPE_CHECKING:
    from .tools import AdminAIToolDefinition


_MAX_ASSEMBLY_BYTES = 32 * 1024  # 32 KiB hard cap on assembled instructions
_SEPARATOR = "\n\n---\n\n"


class PromptAssemblyService:
    """Assemble permission-aware system instructions for Admin AI requests.

    Instances are cheap to create; the heavyweight state lives in the
    shared ``AIPromptTemplateRegistry``.
    """

    def __init__(self, registry: AIPromptTemplateRegistry | None = None) -> None:
        self._registry = registry or get_prompt_template_registry()

    def assemble(
        self,
        permitted_tool_defs: list[Any],
        *,
        task_context: str = "",
    ) -> AIPromptAssemblyResult:
        """Assemble system instructions for the given permitted tool set.

        Ordering:
        1. Global operating prompt body
        2. Separator
        3. Per-tool sections, sorted by tool name (only for tools with templates)
        4. task_context if provided (appended after Cauldron instructions)

        Returns an ``AIPromptAssemblyResult`` with the assembled text and
        metadata for audit logging.
        """
        global_prompt = self._registry.get_global_prompt()
        parts: list[str] = []
        global_version = ""

        if global_prompt is not None:
            parts.append(global_prompt.body)
            global_version = global_prompt.version

        included_tool_names: list[str] = []
        template_versions: list[tuple[str, str]] = []

        # Stable ordering: sort by tool name.
        sorted_defs = sorted(permitted_tool_defs, key=lambda d: d.name)

        for defn in sorted_defs:
            tmpl = self._registry.get_tool_template(defn.name)
            if tmpl is None:
                continue  # tool has no template; omit silently (do not fail)
            included_tool_names.append(defn.name)
            template_versions.append((defn.name, tmpl.template_version))
            parts.append(_render_tool_section(tmpl))

        if task_context:
            parts.append(f"Current task context:\n{task_context}")

        instructions = _SEPARATOR.join(parts)

        # Enforce size limit: truncate at UTF-8 byte boundary if oversized.
        encoded = instructions.encode("utf-8")
        if len(encoded) > _MAX_ASSEMBLY_BYTES:
            instructions = encoded[:_MAX_ASSEMBLY_BYTES].decode("utf-8", errors="ignore")

        return AIPromptAssemblyResult(
            system_instructions=instructions,
            global_prompt_version=global_version,
            included_tool_names=tuple(included_tool_names),
            template_versions=tuple(template_versions),
            assembled_bytes=len(instructions.encode("utf-8")),
        )


def _render_tool_section(tmpl: AIToolPromptTemplate) -> str:
    """Render a single tool template into plain text for the system prompt."""
    lines = [
        f"## Tool: {tmpl.tool_name}",
        f"Purpose: {tmpl.purpose}",
        f"Risk: {tmpl.risk_level}",
    ]
    write_lower = tmpl.write_scope.strip().lower()
    if write_lower not in ("none.", "none"):
        lines.append(f"Write scope: {tmpl.write_scope}")
    approval_lower = tmpl.approval_requirements.strip().lower()
    if approval_lower not in ("none.", "none; read-only.", "none"):
        lines.append(f"Approval: {tmpl.approval_requirements}")
    lines.append(f"Input: {tmpl.input_expectations}")
    lines.append(f"Result: {tmpl.result_behavior}")
    if tmpl.refusal_behavior:
        lines.append(f"Refusal: {tmpl.refusal_behavior}")
    return "\n".join(lines)
