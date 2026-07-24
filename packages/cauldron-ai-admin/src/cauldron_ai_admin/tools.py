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

Registration invariants
-----------------------
* ``name`` matches ``r'^[a-z][a-z0-9]*(\\.[a-z][a-z0-9]*)+$'``, ≤128 chars.
* ``version`` matches ``r'^\\d+\\.\\d+(\\.\\d+)?$'`` or ``r'^v\\d+$'``, ≤32.
* ``owning_module`` is a dotted lowercase slug, ≤128 chars.
* ``required_permission`` matches ``r'^[a-z0-9_]+\\.[a-z0-9_]+$'``.
* ``argument_schema`` is a valid Draft-07 JSON Schema.
* ``timeout_seconds`` and ``max_output_bytes`` are positive.
* The ``server.*`` namespace is reserved for ``owning_module ==
  "cauldron.ai.admin.server"``.
* Re-registering the same ``(definition, handler)`` is a silent no-op;
  a differing definition or handler for the same name raises
  ``ValueError``.
"""
from __future__ import annotations

import copy
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

from cauldron_ai.contracts import is_json_serialisable


# -------------------------------------------------------------- validators
# Tool name: dotted lowercase segments, underscores allowed within a
# segment (e.g. ``content.list_collections`` → two segments). Must have
# at least two dotted components.
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_VERSION_RE = re.compile(r"^(?:v\d+|\d+\.\d+(?:\.\d+)?(?:[-.][a-z0-9]+)*)$")
_OWNING_MODULE_RE = re.compile(r"^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9]*)*$")
_PERMISSION_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")

SERVER_NAMESPACE = "server."
SERVER_OWNING_MODULE = "cauldron.ai.admin.server"

_MAX_NAME = 128
_MAX_VERSION = 32
_MAX_OWNING_MODULE = 128
_MAX_PERMISSION = 256


class RiskLevel(str, Enum):
    READ_ONLY = "READ_ONLY"
    PROPOSE = "PROPOSE"
    MAINTENANCE = "MAINTENANCE"
    PRIVILEGED = "PRIVILEGED"


def _check_schema(schema: dict) -> None:
    """Validate the shape of *schema* itself as JSON Schema Draft-07."""
    try:
        from jsonschema import Draft7Validator
    except ImportError as exc:  # pragma: no cover - jsonschema is a dep
        raise RuntimeError("jsonschema is required for AdminAI tools") from exc
    Draft7Validator.check_schema(schema)


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
        # ----- name
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("AdminAIToolDefinition.name must be a non-empty string")
        if len(self.name) > _MAX_NAME:
            raise ValueError("AdminAIToolDefinition.name is too long (max 128 chars)")
        if not _NAME_RE.match(self.name):
            raise ValueError(
                f"AdminAIToolDefinition.name {self.name!r} must match dotted "
                "lowercase segments (a.b, a.b.c, ...)."
            )
        # ----- version
        if not isinstance(self.version, str) or not self.version:
            raise ValueError("AdminAIToolDefinition.version must be a non-empty string")
        if len(self.version) > _MAX_VERSION:
            raise ValueError("AdminAIToolDefinition.version is too long (max 32 chars)")
        if not _VERSION_RE.match(self.version):
            raise ValueError(
                f"AdminAIToolDefinition.version {self.version!r} must be dotted "
                "digits (1.0, 1.0.1) or v-prefixed integer (v1)."
            )
        # ----- description
        if not isinstance(self.description, str):
            raise TypeError("AdminAIToolDefinition.description must be a string")
        # ----- argument_schema
        if not isinstance(self.argument_schema, dict):
            raise TypeError("AdminAIToolDefinition.argument_schema must be a dict")
        # Deep copy so mutation of the caller's dict doesn't leak in.
        frozen_schema = copy.deepcopy(self.argument_schema)
        # Schema validity is enforced by the registry at register() time as
        # well; we run it here too so mis-shaped definitions fail fast even
        # when constructed outside the registry.
        _check_schema(frozen_schema)
        object.__setattr__(self, "argument_schema", frozen_schema)
        # ----- risk_level
        if not isinstance(self.risk_level, RiskLevel):
            raise TypeError("AdminAIToolDefinition.risk_level must be a RiskLevel")
        # ----- required_permission
        if not isinstance(self.required_permission, str) or not self.required_permission:
            raise ValueError(
                "AdminAIToolDefinition.required_permission must be a non-empty string"
            )
        if len(self.required_permission) > _MAX_PERMISSION:
            raise ValueError(
                "AdminAIToolDefinition.required_permission is too long"
            )
        if not _PERMISSION_RE.match(self.required_permission):
            raise ValueError(
                f"AdminAIToolDefinition.required_permission {self.required_permission!r} "
                "must be app_label.codename (lowercase, underscores allowed)."
            )
        # ----- owning_module
        if not isinstance(self.owning_module, str) or not self.owning_module:
            raise ValueError(
                "AdminAIToolDefinition.owning_module must be a non-empty string"
            )
        if len(self.owning_module) > _MAX_OWNING_MODULE:
            raise ValueError(
                "AdminAIToolDefinition.owning_module is too long (max 128 chars)"
            )
        if not _OWNING_MODULE_RE.match(self.owning_module):
            raise ValueError(
                f"AdminAIToolDefinition.owning_module {self.owning_module!r} must be "
                "dotted lowercase segments."
            )
        # ----- timeout_seconds / max_output_bytes
        if not isinstance(self.timeout_seconds, (int, float)) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a positive number")
        if not isinstance(self.max_output_bytes, int) or self.max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be a positive integer")


@dataclass
class AdminAIToolContext:
    """Runtime context passed to a tool handler.

    Not frozen — the service constructs this instance per invocation and
    tests occasionally set attributes. Handlers must treat it as read-only.
    """

    actor: Any                       # Django User instance
    run_id: str
    correlation_id: str
    content_service: Any = None      # ContentOperationService or None
    deadline: datetime | None = None
    dry_run: bool = False

    def deadline_remaining_seconds(self) -> float | None:
        """Return seconds remaining until the run deadline, or ``None``.

        A negative return value means the deadline has already passed; the
        caller should refuse to execute mutations and return a
        ``tool.timeout`` error.
        """
        if self.deadline is None:
            return None
        now = datetime.now(tz=timezone.utc)
        return (self.deadline - now).total_seconds()


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

        # Reserved namespace: server.* is only for the Admin AI server package.
        if definition.name.startswith(SERVER_NAMESPACE) and (
            definition.owning_module != SERVER_OWNING_MODULE
        ):
            raise ValueError(
                f"Tool namespace 'server.*' is reserved for owning_module "
                f"{SERVER_OWNING_MODULE!r}; got {definition.owning_module!r}."
            )

        with self._lock:
            if definition.name in self._tools:
                existing_def, existing_handler = self._tools[definition.name]
                same_defn = (
                    existing_def.name == definition.name
                    and existing_def.version == definition.version
                    and existing_def.owning_module == definition.owning_module
                    and existing_def.description == definition.description
                    and existing_def.argument_schema == definition.argument_schema
                    and existing_def.risk_level == definition.risk_level
                    and existing_def.required_permission == definition.required_permission
                    and existing_def.timeout_seconds == definition.timeout_seconds
                    and existing_def.max_output_bytes == definition.max_output_bytes
                    and existing_def.supports_dry_run == definition.supports_dry_run
                    and existing_def.requires_human_approval == definition.requires_human_approval
                )
                if same_defn and existing_handler is handler:
                    return  # idempotent re-registration
                raise ValueError(
                    f"Admin AI tool {definition.name!r} is already registered "
                    "with a different definition or handler."
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


# ---------------------------------------------------------------------------
# JSON Schema argument validation used by the service.
# ---------------------------------------------------------------------------


class ToolArgumentValidationError(ValueError):
    """Raised when a tool call's arguments violate the tool's schema."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.stable_message = message


def validate_tool_arguments(schema: dict, arguments: Any) -> None:
    """Validate *arguments* against *schema*.

    Raises :class:`ToolArgumentValidationError` with a bounded, stable
    message on failure. The schema itself is not re-validated here (that
    is done at registration time).
    """
    try:
        from jsonschema import Draft7Validator
    except ImportError as exc:  # pragma: no cover - jsonschema is a dep
        raise RuntimeError("jsonschema is required for AdminAI tools") from exc

    if not is_json_serialisable(arguments):
        raise ToolArgumentValidationError(
            "Tool arguments must be JSON-serialisable."
        )

    validator = Draft7Validator(schema)
    errors = sorted(validator.iter_errors(arguments), key=lambda e: e.path)
    if not errors:
        return

    first = errors[0]
    # We include the JSON pointer path so operators can localise the
    # violation, but never the offending value — it may contain secrets.
    if first.absolute_path:
        location = "/".join(str(p) for p in first.absolute_path)
        stable = f"Argument at {location!r} failed schema: {first.validator}"
    else:
        stable = f"Arguments failed schema: {first.validator}"
    raise ToolArgumentValidationError(stable[:400])
