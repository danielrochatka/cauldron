"""Frozen contracts describing a single AI model turn.

These types are provider-neutral. Concrete provider adapters translate
between these contracts and the wire format used by the model vendor.
Everything is immutable — dicts are copied defensively on construction
so callers cannot mutate what a consumer already observed.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any, Mapping


_ALLOWED_ROLES = frozenset({"system", "user", "assistant", "tool"})
_ALLOWED_STOP_REASONS = frozenset({"", "end_turn", "tool_use", "max_tokens", "timeout"})


def _deep_freeze_json_value(value: Any) -> Any:
    """Return a deep-copied version of ``value`` after asserting JSON-compat.

    We *only* accept JSON-serialisable primitives, dicts (with string keys),
    and lists/tuples of the same. This keeps schemas and tool arguments
    hashable-in-effect at rest, and makes downstream serialisation safe.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(k): _deep_freeze_json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_deep_freeze_json_value(v) for v in value]
    raise TypeError(
        f"Value of type {type(value).__name__!r} is not JSON-serialisable."
    )


@dataclass(frozen=True)
class AIModelToolCall:
    """A single tool invocation requested by the model."""

    id: str  # unique per request; model-supplied
    name: str
    arguments: dict  # JSON-decoded arguments

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("AIModelToolCall.id must be a non-empty string")
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("AIModelToolCall.name must be a non-empty string")
        if not isinstance(self.arguments, Mapping):
            raise TypeError("AIModelToolCall.arguments must be a mapping")
        # Deep defensive copy so callers can't mutate the record after
        # construction, and the whole nested tree is isolated from any
        # mutation of the source dict.
        object.__setattr__(self, "arguments", copy.deepcopy(dict(self.arguments)))


@dataclass(frozen=True)
class AIModelMessage:
    """A single turn in a conversation with the model."""

    role: str  # "system" | "user" | "assistant" | "tool"
    content: str = ""
    tool_call_id: str | None = None  # required when role == "tool"
    tool_calls: tuple[AIModelToolCall, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.role not in _ALLOWED_ROLES:
            raise ValueError(
                f"AIModelMessage.role must be one of {sorted(_ALLOWED_ROLES)}, "
                f"got {self.role!r}"
            )
        if not isinstance(self.content, str):
            raise TypeError("AIModelMessage.content must be a string")
        if not isinstance(self.tool_calls, tuple):
            raise TypeError("AIModelMessage.tool_calls must be a tuple")
        for tc in self.tool_calls:
            if not isinstance(tc, AIModelToolCall):
                raise TypeError(
                    "AIModelMessage.tool_calls must contain AIModelToolCall"
                )
        if self.tool_call_id is not None and not isinstance(self.tool_call_id, str):
            raise TypeError("AIModelMessage.tool_call_id must be a string or None")

        # Role invariants.
        if self.role in ("user", "system"):
            if self.tool_calls:
                raise ValueError(
                    f"AIModelMessage with role {self.role!r} must not carry tool_calls"
                )
            if self.tool_call_id is not None:
                raise ValueError(
                    f"AIModelMessage with role {self.role!r} must not carry tool_call_id"
                )
        elif self.role == "assistant":
            if self.tool_call_id is not None:
                raise ValueError(
                    "AIModelMessage with role 'assistant' must not carry tool_call_id"
                )
        elif self.role == "tool":
            if not self.tool_call_id:
                raise ValueError(
                    "AIModelMessage.tool_call_id is required when role == 'tool'"
                )
            if self.tool_calls:
                raise ValueError(
                    "AIModelMessage with role 'tool' must not carry tool_calls"
                )


@dataclass(frozen=True)
class AIModelToolDefinition:
    """A tool definition exposed to the model."""

    name: str
    description: str
    parameters: dict  # JSON Schema object — deep-frozen at construction time

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("AIModelToolDefinition.name must be a non-empty string")
        if not isinstance(self.description, str):
            raise TypeError("AIModelToolDefinition.description must be a string")
        if not isinstance(self.parameters, Mapping):
            raise TypeError("AIModelToolDefinition.parameters must be a mapping")
        # Deep copy so nested schema mutations don't leak in.
        object.__setattr__(
            self, "parameters", copy.deepcopy(dict(self.parameters))
        )


@dataclass(frozen=True)
class AIModelRequest:
    """A single request to the model provider."""

    messages: tuple[AIModelMessage, ...]
    tools: tuple[AIModelToolDefinition, ...] = ()
    system: str = ""
    max_tokens: int = 4096
    timeout_seconds: float = 60.0
    correlation_id: str = ""
    deadline_seconds: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.messages, tuple):
            raise TypeError("AIModelRequest.messages must be a tuple")
        for m in self.messages:
            if not isinstance(m, AIModelMessage):
                raise TypeError("AIModelRequest.messages must contain AIModelMessage")
        if not isinstance(self.tools, tuple):
            raise TypeError("AIModelRequest.tools must be a tuple")
        for t in self.tools:
            if not isinstance(t, AIModelToolDefinition):
                raise TypeError(
                    "AIModelRequest.tools must contain AIModelToolDefinition"
                )
        if not isinstance(self.max_tokens, int) or self.max_tokens <= 0:
            raise ValueError("AIModelRequest.max_tokens must be a positive integer")
        if not isinstance(self.timeout_seconds, (int, float)) or self.timeout_seconds <= 0:
            raise ValueError("AIModelRequest.timeout_seconds must be positive")
        if self.deadline_seconds is not None:
            if (
                not isinstance(self.deadline_seconds, (int, float))
                or isinstance(self.deadline_seconds, bool)
                or self.deadline_seconds <= 0
            ):
                raise ValueError(
                    "AIModelRequest.deadline_seconds must be a positive number or None"
                )


@dataclass(frozen=True)
class AIModelResponse:
    """A single response from the model provider."""

    provider_request_id: str
    content: str = ""
    tool_calls: tuple[AIModelToolCall, ...] = field(default_factory=tuple)
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str = ""  # "end_turn" | "tool_use" | "max_tokens" | "timeout"

    def __post_init__(self) -> None:
        if not isinstance(self.provider_request_id, str):
            raise TypeError("AIModelResponse.provider_request_id must be a string")
        if not isinstance(self.content, str):
            raise TypeError("AIModelResponse.content must be a string")
        if not isinstance(self.tool_calls, tuple):
            raise TypeError("AIModelResponse.tool_calls must be a tuple")
        for c in self.tool_calls:
            if not isinstance(c, AIModelToolCall):
                raise TypeError(
                    "AIModelResponse.tool_calls must contain AIModelToolCall"
                )
        if self.stop_reason not in _ALLOWED_STOP_REASONS:
            raise ValueError(
                f"AIModelResponse.stop_reason must be one of "
                f"{sorted(_ALLOWED_STOP_REASONS)}, got {self.stop_reason!r}"
            )
        if not isinstance(self.input_tokens, int) or self.input_tokens < 0:
            raise ValueError("AIModelResponse.input_tokens must be a non-negative int")
        if not isinstance(self.output_tokens, int) or self.output_tokens < 0:
            raise ValueError("AIModelResponse.output_tokens must be a non-negative int")


def is_json_serialisable(value: Any) -> bool:
    """Return True iff ``value`` round-trips through :func:`json.dumps`
    with no ``default`` fallback."""
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return False
    return True
