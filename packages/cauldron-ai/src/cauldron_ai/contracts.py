"""Frozen contracts describing a single AI model turn.

These types are provider-neutral. Concrete provider adapters translate
between these contracts and the wire format used by the model vendor.
Everything is immutable — dicts are deep-frozen at construction so callers
cannot mutate what a consumer already observed, and every payload is
strictly checked for JSON compatibility (no NaN/Infinity, no non-string
keys, no non-primitive leaves).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping


_ALLOWED_ROLES = frozenset({"system", "user", "assistant", "tool"})
_ALLOWED_STOP_REASONS = frozenset({"", "end_turn", "tool_use", "max_tokens", "timeout"})


def _check_keys(value: Any) -> None:
    """Recursively verify every mapping key is a plain string."""
    if isinstance(value, Mapping):
        for k, v in value.items():
            if not isinstance(k, str):
                raise ValueError(f"Non-string mapping key: {k!r}")
            _check_keys(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _check_keys(v)


def _to_plain_for_check(value: Any) -> Any:
    """Return a plain ``dict``/``list`` projection for JSON-serialisation.

    ``json.dumps`` does not accept :class:`types.MappingProxyType` (which
    is what our deep-freeze produces). This helper walks the input and
    substitutes plain concrete types so the strict JSON check can run
    against both fresh and already-frozen values.
    """
    if isinstance(value, Mapping):
        return {str(k): _to_plain_for_check(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_plain_for_check(v) for v in value]
    return value


def _assert_json_compatible(value: Any) -> None:
    """Raise ``ValueError`` for values that are not strict-JSON safe.

    Rejects ``NaN`` / ``Infinity`` / ``-Infinity`` (via ``allow_nan=False``),
    non-string mapping keys, and any leaf that ``json.dumps`` cannot encode
    without a ``default`` fallback. This is called both at construction of
    the immutable contract types and again at argument-validation time so
    that non-JSON values can never enter the audit trail.
    """
    # Walk once for non-string keys BEFORE hitting json.dumps so the error
    # message is stable regardless of whether the key or the value fails
    # first.
    _check_keys(value)
    plain = _to_plain_for_check(value)
    try:
        json.dumps(plain, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Value is not JSON-compatible: {exc}") from exc


def _deep_freeze(value: Any) -> Any:
    """Return a deeply-immutable view of *value* after JSON validation.

    * ``dict`` → :class:`types.MappingProxyType` wrapping frozen children
    * ``list`` / ``tuple`` → ``tuple`` of frozen children
    * primitives → returned as-is
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return MappingProxyType({
            str(k): _deep_freeze(v) for k, v in value.items()
        })
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(v) for v in value)
    raise TypeError(
        f"Value of type {type(value).__name__!r} is not JSON-serialisable."
    )


def _deep_freeze_json_value(value: Any) -> Any:
    """Legacy alias kept for backwards compatibility."""
    return _deep_freeze(value)


@dataclass(frozen=True)
class AIModelToolCall:
    """A single tool invocation requested by the model."""

    id: str  # unique per request; model-supplied
    name: str
    arguments: Mapping[str, Any]  # JSON-decoded arguments (deep-frozen)

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("AIModelToolCall.id must be a non-empty string")
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("AIModelToolCall.name must be a non-empty string")
        if not isinstance(self.arguments, Mapping):
            raise TypeError("AIModelToolCall.arguments must be a mapping")
        # Strict JSON compatibility first (rejects NaN/Infinity/non-string
        # keys/non-primitive leaves before anything is exposed).
        _assert_json_compatible(dict(self.arguments))
        # Deep freeze so callers can't mutate the record after construction.
        object.__setattr__(self, "arguments", _deep_freeze(dict(self.arguments)))


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
    parameters: Mapping[str, Any]  # JSON Schema object — deep-frozen

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("AIModelToolDefinition.name must be a non-empty string")
        if not isinstance(self.description, str):
            raise TypeError("AIModelToolDefinition.description must be a string")
        if not isinstance(self.parameters, Mapping):
            raise TypeError("AIModelToolDefinition.parameters must be a mapping")
        _assert_json_compatible(dict(self.parameters))
        # Deep freeze so nested schema mutations don't leak in.
        object.__setattr__(
            self, "parameters", _deep_freeze(dict(self.parameters))
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
    with no ``default`` fallback.

    Accepts our deep-frozen views (MappingProxyType/tuple) by projecting
    them onto their concrete counterparts before serialisation.
    """
    try:
        json.dumps(_to_plain_for_check(value))
    except (TypeError, ValueError):
        return False
    return True
