"""AI provider protocol and process-wide registry.

The registry is a plain in-memory singleton keyed by provider name.
Providers register themselves at import/AppConfig.ready() time and are
looked up by consumers (e.g. `cauldron.ai.admin`) at request time.
The registry is intentionally minimal: no configuration, no discovery,
no fallbacks. Sites that need multiple providers pick one by name.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .contracts import AIModelRequest, AIModelResponse


class ProviderRegistryError(RuntimeError):
    """Raised for unrecoverable provider registry conditions."""


@runtime_checkable
class AIModelProvider(Protocol):
    """A concrete AI model provider.

    Implementations must be pure functions of ``AIModelRequest`` — no
    hidden state that depends on the caller's identity, and no side
    effects other than the provider API call and observability.
    ``name`` must be unique per registered provider process-wide.
    """

    name: str  # e.g. "anthropic-claude"

    def complete(self, request: AIModelRequest) -> AIModelResponse: ...


@dataclass(frozen=True)
class AIModelProviderDescriptor:
    """Static metadata describing a registered provider (for introspection)."""

    name: str
    display_name: str
    version: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("AIModelProviderDescriptor.name must be non-empty")
        if not isinstance(self.display_name, str):
            raise TypeError("AIModelProviderDescriptor.display_name must be a string")
        if not isinstance(self.version, str):
            raise TypeError("AIModelProviderDescriptor.version must be a string")


class AIModelProviderRegistry:
    """Thread-safe registry of AI providers.

    The registry is deliberately dumb: it does not resolve capabilities,
    parse configuration, or select a default automatically. Callers ask
    for a provider by name. ``default()`` is only meaningful when exactly
    one provider is registered.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._providers: dict[str, AIModelProvider] = {}

    def register(self, provider: AIModelProvider) -> None:
        if not isinstance(getattr(provider, "name", None), str) or not provider.name:
            raise ValueError("Provider must expose a non-empty string 'name' attribute")
        if not callable(getattr(provider, "complete", None)):
            raise TypeError("Provider must implement complete(request)")
        with self._lock:
            existing = self._providers.get(provider.name)
            if existing is not None and existing is not provider:
                raise ProviderRegistryError(
                    f"AI provider {provider.name!r} is already registered"
                )
            self._providers[provider.name] = provider

    def unregister(self, name: str) -> None:
        """Remove a provider. Silent no-op if it isn't registered."""
        with self._lock:
            self._providers.pop(name, None)

    def get(self, name: str) -> AIModelProvider:
        with self._lock:
            provider = self._providers.get(name)
        if provider is None:
            raise ProviderRegistryError(
                f"No AI provider registered with name {name!r}"
            )
        return provider

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._providers)

    def default(self) -> AIModelProvider:
        """Return the single registered provider.

        Raises ``ProviderRegistryError`` when the registry is empty or
        holds more than one provider; the caller must pick by name in
        the ambiguous case.
        """
        with self._lock:
            items = list(self._providers.values())
        if not items:
            raise ProviderRegistryError("No AI providers are registered")
        if len(items) > 1:
            raise ProviderRegistryError(
                "Default AI provider is ambiguous: "
                f"{sorted(p.name for p in items)}"
            )
        return items[0]

    def clear(self) -> None:
        """Test helper: remove every registered provider."""
        with self._lock:
            self._providers.clear()


# Module-level singleton used by consumers and tests.
_registry = AIModelProviderRegistry()


def register_provider(provider: AIModelProvider) -> None:
    _registry.register(provider)


def unregister_provider(name: str) -> None:
    _registry.unregister(name)


def get_provider(name: str) -> AIModelProvider:
    return _registry.get(name)


def get_default_provider() -> AIModelProvider:
    return _registry.default()


def provider_names() -> list[str]:
    return _registry.names()


def _reset_registry_for_tests() -> None:
    """Test-only hook to clear registry state between test runs."""
    _registry.clear()
