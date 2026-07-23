"""Tool definitions, registry, and helpers for Admin AI.

Design notes
------------
The registry is deliberately dumb. Any Cauldron module can extend
Admin AI's tool surface by importing
``cauldron_ai_admin.tools.register_tool`` at app-ready time
(Django ``AppConfig.ready()``). The Admin AI service enumerates tools
via ``get_tool_registry()`` at each request, so newly registered tools
appear without cross-module imports from admin-ai.

Every tool declares:

* a stable dotted name (e.g. ``"content.list_collections"``);
* a JSON Schema for its arguments;
* a Django permission codename the actor must hold;
* a risk level that determines the enforcement path.

Risk-level policy
-----------------
The service applies the same policy per invocation:

* ``READ_ONLY`` — execute if the actor has permission.
* ``PROPOSE`` — execute if the actor has permission; must only create
  non-canonical proposals (change-requests). Never call apply/validate/
  approve/reject/rollback.
* ``MAINTENANCE`` — refuse and record ``approval_required``.
* ``PRIVILEGED`` — refuse and record ``restricted``.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable


class RiskLevel(str, Enum):
    READ_ONLY = "READ_ONLY"
    PROPOSE = "PROPOSE"
    MAINTENANCE = "MAINTENANCE"
    PRIVILEGED = "PRIVILEGED"


@dataclass(frozen=True)
class AdminAIToolDefinition:
    """Immutable declaration of a tool the Admin AI can call."""

    name: str
    version: str
    description: str
    argument_schema: dict
    risk_level: RiskLevel
    required_permission: str
    owning_module: str
    timeout_seconds: float = 30.0
    max_output_bytes: int = 65536
    supports_dry_run: bool = False
    requires_human_approval: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("AdminAIToolDefinition.name must be a non-empty string")
        if not isinstance(self.version, str) or not self.version:
            raise ValueError("AdminAIToolDefinition.version must be a non-empty string")
        if not isinstance(self.description, str):
            raise TypeError("AdminAIToolDefinition.description must be a string")
        if not isinstance(self.argument_schema, dict):
            raise TypeError("AdminAIToolDefinition.argument_schema must be a dict")
        if not isinstance(self.risk_level, RiskLevel):
            raise TypeError("AdminAIToolDefinition.risk_level must be a RiskLevel")
        if not isinstance(self.required_permission, str) or not self.required_permission:
            raise ValueError(
                "AdminAIToolDefinition.required_permission must be a non-empty string"
            )
        if not isinstance(self.owning_module, str) or not self.owning_module:
            raise ValueError(
                "AdminAIToolDefinition.owning_module must be a non-empty string"
            )
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be positive")
        # Defensive copy so mutation of the caller's dict doesn't leak in.
        object.__setattr__(self, "argument_schema", dict(self.argument_schema))


@dataclass(frozen=True)
class AdminAIToolContext:
    """Runtime context passed to a tool handler."""

    actor: Any                       # Django User instance
    run_id: str
    correlation_id: str
    dry_run: bool = False


@dataclass(frozen=True)
class AdminAIToolResult:
    """Success shape returned by a tool handler."""

    tool_name: str
    success: bool = True
    data: Any = None                 # JSON-serialisable
    message: str = ""


@dataclass(frozen=True)
class AdminAIToolError:
    """Failure shape returned by a tool handler."""

    tool_name: str
    error_code: str
    message: str


AdminAIToolHandler = Callable[..., "AdminAIToolResult | AdminAIToolError"]


class AdminAIToolRegistry:
    """Thread-safe, deterministic tool registry.

    Extension point for child modules
    ---------------------------------
    Any Cauldron module (for example a future ``cauldron.ai.admin.server``)
    calls ``register_tool(definition, handler)`` inside its Django
    ``AppConfig.ready()``. Admin AI discovers all tools through this
    registry at request time — the service never imports child modules
    directly.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tools: dict[str, tuple[AdminAIToolDefinition, AdminAIToolHandler]] = {}

    def register(
        self,
        definition: AdminAIToolDefinition,
        handler: AdminAIToolHandler,
    ) -> None:
        if not isinstance(definition, AdminAIToolDefinition):
            raise TypeError("definition must be an AdminAIToolDefinition")
        if not callable(handler):
            raise TypeError("handler must be callable")
        with self._lock:
            if definition.name in self._tools:
                existing_def, existing_handler = self._tools[definition.name]
                if existing_def is definition and existing_handler is handler:
                    return  # idempotent same-args re-registration
                raise ValueError(
                    f"Admin AI tool {definition.name!r} is already registered"
                )
            self._tools[definition.name] = (definition, handler)

    def unregister(self, name: str) -> None:
        with self._lock:
            self._tools.pop(name, None)

    def get(
        self, name: str
    ) -> tuple[AdminAIToolDefinition, AdminAIToolHandler] | None:
        with self._lock:
            return self._tools.get(name)

    def all_definitions(self) -> list[AdminAIToolDefinition]:
        with self._lock:
            defs = [d for d, _ in self._tools.values()]
        return sorted(defs, key=lambda d: d.name)

    def list_for_actor(self, actor: Any) -> list[AdminAIToolDefinition]:
        """Return the tools *actor* is allowed to see, sorted by name.

        The check is by Django permission codename with the app label
        namespace already baked in on the definition. When ``actor`` is
        falsy or has no ``has_perm`` we return an empty list — Admin AI
        never surfaces tools to anonymous callers.
        """
        if actor is None or not getattr(actor, "is_active", False):
            return []
        allowed: list[AdminAIToolDefinition] = []
        for definition in self.all_definitions():
            try:
                if actor.has_perm(definition.required_permission):
                    allowed.append(definition)
            except Exception:  # pragma: no cover - defensive
                continue
        return allowed

    def clear(self) -> None:
        """Test helper: wipe the registry."""
        with self._lock:
            self._tools.clear()

    def duplicate_names(self) -> list[str]:
        """Deterministic list of duplicate names — always empty in practice.

        Kept for parity with the system check that inspects registry
        health.
        """
        # The registry can't hold duplicates (register() raises), but we
        # keep the method here so that health checks read from a stable
        # public API rather than poking at private state.
        return []


# Module-level singleton.
_registry = AdminAIToolRegistry()


def register_tool(
    definition: AdminAIToolDefinition, handler: AdminAIToolHandler
) -> None:
    _registry.register(definition, handler)


def unregister_tool(name: str) -> None:
    _registry.unregister(name)


def get_tool_registry() -> AdminAIToolRegistry:
    return _registry
