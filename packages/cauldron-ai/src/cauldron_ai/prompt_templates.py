"""Versioned prompt templates and prompt assembly contracts for Cauldron AI.

This module is provider-neutral and has NO Django dependency.
It defines the registry contracts and process-level singleton
for tool prompt templates and the global operating prompt.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Validation patterns
# ---------------------------------------------------------------------------

_TEMPLATE_TOOL_NAME_RE = re.compile(
    r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$"
)
_TEMPLATE_VERSION_RE = re.compile(
    r"^(?:v\d+|\d+\.\d+(?:\.\d+)?(?:[-.][a-z0-9]+)*)$"
)
_MAX_TOOL_NAME = 128
_MAX_VERSION = 64

_VALID_RISK_LEVELS = frozenset({"READ_ONLY", "PROPOSE", "MAINTENANCE", "PRIVILEGED"})


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PromptTemplateRegistryError(RuntimeError):
    """Raised when a registry invariant is violated (e.g. duplicate registration)."""


class PromptAssemblyError(RuntimeError):
    """Base class for errors that occur during prompt assembly."""


class PromptAssemblyTooLargeError(PromptAssemblyError):
    """Raised when a section would push the assembled prompt over the byte limit."""


class PromptTemplateMissingError(PromptAssemblyError):
    """Raised when a permitted tool has no registered prompt template."""


# ---------------------------------------------------------------------------
# Immutable data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AIToolPromptTemplate:
    """Prompt metadata for a single registered Admin AI tool.

    All fields are immutable. Validated at construction time.
    """

    tool_name: str
    template_version: str
    owning_module: str
    purpose: str
    supported_tasks: tuple[str, ...]
    required_permission: str | None
    risk_level: str  # "READ_ONLY" | "PROPOSE" | "MAINTENANCE" | "PRIVILEGED"
    read_scope: str
    write_scope: str
    preconditions: tuple[str, ...]
    input_expectations: str
    result_behavior: str
    approval_requirements: str
    clarification_behavior: str
    refusal_behavior: str
    error_guidance: str
    positive_examples: tuple[str, ...]
    boundary_examples: tuple[str, ...]

    def __post_init__(self) -> None:
        # Validate tool_name
        if not self.tool_name:
            raise ValueError("AIToolPromptTemplate.tool_name must be non-empty.")
        if len(self.tool_name.encode("utf-8")) > _MAX_TOOL_NAME:
            raise ValueError(
                f"AIToolPromptTemplate.tool_name exceeds {_MAX_TOOL_NAME} bytes."
            )
        if not _TEMPLATE_TOOL_NAME_RE.match(self.tool_name):
            raise ValueError(
                f"AIToolPromptTemplate.tool_name {self.tool_name!r} does not "
                "match required pattern (dotted lowercase segments)."
            )
        # Validate template_version
        if not self.template_version:
            raise ValueError("AIToolPromptTemplate.template_version must be non-empty.")
        if len(self.template_version.encode("utf-8")) > _MAX_VERSION:
            raise ValueError(
                f"AIToolPromptTemplate.template_version exceeds {_MAX_VERSION} bytes."
            )
        if not _TEMPLATE_VERSION_RE.match(self.template_version):
            raise ValueError(
                f"AIToolPromptTemplate.template_version {self.template_version!r} "
                "is not a valid version string (e.g. 'v1' or '1.0.0')."
            )
        # Validate risk_level
        if self.risk_level not in _VALID_RISK_LEVELS:
            raise ValueError(
                f"AIToolPromptTemplate.risk_level {self.risk_level!r} is not "
                f"one of {sorted(_VALID_RISK_LEVELS)!r}."
            )


@dataclass(frozen=True)
class AIGlobalOperatingPrompt:
    """The global operating prompt for Admin AI.

    Exactly one may be registered per process. Validated at construction.
    """

    version: str       # validated against VERSION_RE, ≤64 chars
    owning_module: str
    body: str          # the actual text; must be non-empty

    def __post_init__(self) -> None:
        if not self.version:
            raise ValueError("AIGlobalOperatingPrompt.version must be non-empty.")
        if len(self.version.encode("utf-8")) > _MAX_VERSION:
            raise ValueError(
                f"AIGlobalOperatingPrompt.version exceeds {_MAX_VERSION} bytes."
            )
        if not _TEMPLATE_VERSION_RE.match(self.version):
            raise ValueError(
                f"AIGlobalOperatingPrompt.version {self.version!r} is not a "
                "valid version string."
            )
        if not self.body or not self.body.strip():
            raise ValueError("AIGlobalOperatingPrompt.body must be non-empty.")


@dataclass(frozen=True)
class AIPromptAssemblyResult:
    """Immutable result of assembling a system prompt for a single request."""

    system_instructions: str
    global_prompt_version: str
    included_tool_names: tuple[str, ...]
    template_versions: tuple[tuple[str, str], ...]  # ((tool_name, version), ...)
    assembled_bytes: int   # len(system_instructions.encode("utf-8"))


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class AIPromptTemplateRegistry:
    """Thread-safe, process-level singleton registry for prompt templates.

    Use ``get_prompt_template_registry()`` to obtain the singleton.
    In tests use ``_reset_prompt_registry_for_tests()`` to get a clean slate.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tool_templates: dict[str, AIToolPromptTemplate] = {}
        self._global_prompt: AIGlobalOperatingPrompt | None = None

    def register_tool_template(self, template: AIToolPromptTemplate) -> None:
        """Register a tool prompt template.

        Re-registering the identical instance is a silent no-op.
        Registering a different template for the same tool_name raises
        ``PromptTemplateRegistryError``.
        """
        with self._lock:
            existing = self._tool_templates.get(template.tool_name)
            if existing is not None:
                if existing == template:
                    return  # idempotent re-registration
                raise PromptTemplateRegistryError(
                    f"A different AIToolPromptTemplate for tool "
                    f"{template.tool_name!r} is already registered. "
                    "Unregister it first (or use clear() in tests)."
                )
            self._tool_templates[template.tool_name] = template

    def register_global_prompt(self, prompt: AIGlobalOperatingPrompt) -> None:
        """Register the global operating prompt.

        Re-registering the identical instance is a silent no-op.
        Registering a different prompt when one is already registered raises
        ``PromptTemplateRegistryError``.
        """
        with self._lock:
            if self._global_prompt is not None:
                if self._global_prompt == prompt:
                    return  # idempotent re-registration
                raise PromptTemplateRegistryError(
                    "A global operating prompt is already registered. "
                    "Use clear() in tests to reset."
                )
            self._global_prompt = prompt

    def get_tool_template(self, tool_name: str) -> AIToolPromptTemplate | None:
        """Return the template for *tool_name*, or None if not registered."""
        with self._lock:
            return self._tool_templates.get(tool_name)

    def get_global_prompt(self) -> AIGlobalOperatingPrompt | None:
        """Return the global operating prompt, or None if not registered."""
        with self._lock:
            return self._global_prompt

    def all_tool_templates(self) -> list[AIToolPromptTemplate]:
        """Return all registered tool templates, sorted by tool_name."""
        with self._lock:
            return sorted(
                self._tool_templates.values(), key=lambda t: t.tool_name
            )

    def clear(self) -> None:
        """Remove all registered templates and the global prompt. Test helper."""
        with self._lock:
            self._tool_templates.clear()
            self._global_prompt = None


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_registry: AIPromptTemplateRegistry | None = None
_registry_lock = threading.Lock()


def get_prompt_template_registry() -> AIPromptTemplateRegistry:
    """Return the process-level singleton registry, creating it if needed."""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = AIPromptTemplateRegistry()
    return _registry


def _reset_prompt_registry_for_tests() -> None:
    """Replace the singleton with a fresh registry. Only for tests."""
    global _registry
    with _registry_lock:
        _registry = AIPromptTemplateRegistry()


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


def register_tool_template(template: AIToolPromptTemplate) -> None:
    """Register a tool prompt template on the singleton registry."""
    get_prompt_template_registry().register_tool_template(template)


def register_global_prompt(prompt: AIGlobalOperatingPrompt) -> None:
    """Register the global operating prompt on the singleton registry."""
    get_prompt_template_registry().register_global_prompt(prompt)


def get_tool_template(tool_name: str) -> AIToolPromptTemplate | None:
    """Return the template for *tool_name* from the singleton registry."""
    return get_prompt_template_registry().get_tool_template(tool_name)


def get_global_prompt() -> AIGlobalOperatingPrompt | None:
    """Return the global operating prompt from the singleton registry."""
    return get_prompt_template_registry().get_global_prompt()
