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
    PromptAssemblyTooLargeError,
    PromptTemplateMissingError,
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
        caller_system_prompt: str = "",
    ) -> AIPromptAssemblyResult:
        """Assemble system instructions for the given permitted tool set.

        Raises ``PromptTemplateMissingError`` if any permitted tool has no
        registered prompt template (fail-closed; never silently omits).

        Raises ``PromptAssemblyTooLargeError`` if a complete section would
        push the total past ``_MAX_ASSEMBLY_BYTES``. Sections are never
        truncated mid-section.

        Ordering:
        1. Global operating prompt body
        2. Per-tool sections, sorted by tool name
        3. caller_system_prompt (if non-empty)
        4. task_context (if non-empty)

        Returns an ``AIPromptAssemblyResult`` with the assembled text and
        metadata for audit logging.
        """
        global_prompt = self._registry.get_global_prompt()
        included_tool_names: list[str] = []
        template_versions: list[tuple[str, str]] = []

        # Validate all permitted tools have templates before building anything.
        sorted_defs = sorted(permitted_tool_defs, key=lambda d: d.name)
        for defn in sorted_defs:
            tmpl = self._registry.get_tool_template(defn.name)
            if tmpl is None:
                raise PromptTemplateMissingError(
                    f"Permitted tool {defn.name!r} has no registered prompt template."
                )

        # Build sections incrementally, tracking exact byte counts.
        # Never truncate; raise if a complete section would exceed the cap.
        sections: list[str] = []
        running_bytes = 0

        def _add_section(text: str) -> None:
            nonlocal running_bytes
            sep = _SEPARATOR if sections else ""
            candidate = sep + text
            candidate_bytes = len(candidate.encode("utf-8"))
            if running_bytes + candidate_bytes > _MAX_ASSEMBLY_BYTES:
                raise PromptAssemblyTooLargeError(
                    f"Assembled prompt would exceed {_MAX_ASSEMBLY_BYTES} bytes "
                    f"after adding a section ({candidate_bytes} bytes); "
                    f"currently at {running_bytes} bytes."
                )
            sections.append(candidate)
            running_bytes += candidate_bytes

        global_version = ""
        if global_prompt is not None:
            _add_section(global_prompt.body)
            global_version = global_prompt.version

        for defn in sorted_defs:
            tmpl = self._registry.get_tool_template(defn.name)
            # Already validated above — tmpl is never None here.
            assert tmpl is not None
            included_tool_names.append(defn.name)
            template_versions.append((defn.name, tmpl.template_version))
            _add_section(_render_tool_section(tmpl))

        if caller_system_prompt:
            _add_section(caller_system_prompt)

        if task_context:
            _add_section(f"Current task context:\n{task_context}")

        instructions = "".join(sections)

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
