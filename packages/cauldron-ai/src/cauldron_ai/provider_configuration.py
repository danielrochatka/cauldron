"""Provider configuration contracts for Cauldron AI.

These types are provider-neutral and carry no Django dependency.  Concrete
provider adapters implement ``AIModelProviderFactory`` and declare their
configuration surface via ``AIProviderConfigurationSpec``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Field types
# ---------------------------------------------------------------------------

FIELD_TYPE_TEXT = "text"
FIELD_TYPE_PASSWORD = "password"
FIELD_TYPE_INTEGER = "integer"
FIELD_TYPE_BOOLEAN = "boolean"
FIELD_TYPE_URL = "url"

_ALLOWED_FIELD_TYPES = frozenset({
    FIELD_TYPE_TEXT,
    FIELD_TYPE_PASSWORD,
    FIELD_TYPE_INTEGER,
    FIELD_TYPE_BOOLEAN,
    FIELD_TYPE_URL,
})


# ---------------------------------------------------------------------------
# Configuration spec types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AIProviderConfigurationField:
    """Describes a single configurable parameter of a provider.

    The ``environment_variable`` hint allows operators to supply a value via
    the process environment rather than the settings UI; it is never read
    automatically — the config store or adapter must honour it explicitly.
    """

    name: str
    label: str
    field_type: str = FIELD_TYPE_TEXT
    required: bool = False
    default: Any = None
    help_text: str = ""
    max_length: int | None = None
    environment_variable: str | None = None
    advanced: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("AIProviderConfigurationField.name must be non-empty")
        if not isinstance(self.label, str):
            raise TypeError("AIProviderConfigurationField.label must be a string")
        if self.field_type not in _ALLOWED_FIELD_TYPES:
            raise ValueError(
                f"AIProviderConfigurationField.field_type must be one of "
                f"{sorted(_ALLOWED_FIELD_TYPES)}, got {self.field_type!r}"
            )
        if self.max_length is not None and (
            not isinstance(self.max_length, int) or self.max_length <= 0
        ):
            raise ValueError(
                "AIProviderConfigurationField.max_length must be a positive int or None"
            )


@dataclass(frozen=True)
class AIProviderConfigurationSpec:
    """Complete configuration surface declared by a provider factory."""

    provider_name: str
    display_name: str
    version: str = ""
    fields: tuple[AIProviderConfigurationField, ...] = field(default_factory=tuple)
    description: str = ""
    supports_connection_test: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.provider_name, str) or not self.provider_name:
            raise ValueError("AIProviderConfigurationSpec.provider_name must be non-empty")
        if not isinstance(self.display_name, str) or not self.display_name:
            raise ValueError("AIProviderConfigurationSpec.display_name must be non-empty")
        if not isinstance(self.fields, tuple):
            raise TypeError("AIProviderConfigurationSpec.fields must be a tuple")
        for f in self.fields:
            if not isinstance(f, AIProviderConfigurationField):
                raise TypeError(
                    "AIProviderConfigurationSpec.fields must contain "
                    "AIProviderConfigurationField instances"
                )
        names = [f.name for f in self.fields]
        if len(names) != len(set(names)):
            raise ValueError(
                "AIProviderConfigurationSpec.fields contains duplicate field names"
            )

    def field_by_name(self, name: str) -> AIProviderConfigurationField | None:
        for f in self.fields:
            if f.name == name:
                return f
        return None


@dataclass(frozen=True)
class AIProviderConnectionResult:
    """Result of a provider connection test."""

    success: bool
    status: str
    message: str = ""
    provider_request_id: str = ""
    latency_ms: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.success, bool):
            raise TypeError("AIProviderConnectionResult.success must be a bool")
        if not isinstance(self.status, str) or not self.status:
            raise ValueError("AIProviderConnectionResult.status must be non-empty")


# ---------------------------------------------------------------------------
# Provider factory protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class AIModelProviderFactory(Protocol):
    """Factory that builds a provider instance from configuration and secrets.

    Implementations are registered in the ``AIModelProviderRegistry``
    alongside — or instead of — pre-built ``AIModelProvider`` instances.
    The factory is called at request time when the service is constructed.
    """

    name: str  # matches the provider slug, e.g. "openai"

    @property
    def configuration_spec(self) -> AIProviderConfigurationSpec: ...

    def build(
        self, config: dict[str, Any], secrets: dict[str, str]
    ) -> Any:  # -> AIModelProvider
        ...

    def test_connection(
        self, config: dict[str, Any], secrets: dict[str, str]
    ) -> AIProviderConnectionResult:
        ...


# ---------------------------------------------------------------------------
# Provider-neutral exceptions
# ---------------------------------------------------------------------------

class AIProviderError(RuntimeError):
    """Base class for all AI provider errors.

    Optional keyword-only metadata is safe to record in audit logs —
    callers must never populate these fields with raw exception text,
    request bodies, credentials, or response headers.
    """

    def __init__(
        self,
        message: str = "",
        *,
        http_status: int | None = None,
        provider_request_id: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.http_status: int | None = http_status
        self.provider_request_id: str | None = provider_request_id
        self.retry_after: float | None = retry_after


class AIProviderConfigurationError(AIProviderError):
    """Raised when the provider cannot be built due to a configuration problem."""


class AIProviderAuthenticationError(AIProviderError):
    """Raised when the provider rejects credentials."""


class AIProviderConnectionError(AIProviderError):
    """Raised when a network error prevents reaching the provider."""


class AIProviderTimeoutError(AIProviderError):
    """Raised when the provider call times out before returning a response."""


class AIProviderRateLimitError(AIProviderError):
    """Raised when the provider returns a rate-limit response."""


class AIProviderResponseError(AIProviderError):
    """Raised when the provider returns an unexpected or unparseable response."""
