"""AI provider protocol and process-wide registry.

The registry is a plain in-memory singleton keyed by provider name.
Providers register themselves at import/AppConfig.ready() time and are
looked up by consumers (e.g. `cauldron.ai.admin`) at request time.
The registry is intentionally minimal: no configuration, no discovery,
no fallbacks. Sites that need multiple providers pick one by name.

Provider factories may also be registered alongside pre-built provider
instances. A factory is a callable object that builds a provider from
a configuration dict and a secrets dict, enabling dynamic provider
construction (e.g. from stored API credentials).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .contracts import AIModelRequest, AIModelResponse
from .provider_configuration import (
    AIModelProviderFactory,
    AIProviderConfigurationSpec,
    AIProviderConnectionResult,
)


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
    """Thread-safe registry of AI providers and provider factories.

    The registry is deliberately dumb: it does not resolve capabilities,
    parse configuration, or select a default automatically. Callers ask
    for a provider by name. ``default()`` is only meaningful when exactly
    one provider is registered.

    Provider factories may be registered alongside pre-built provider
    instances.  A factory is looked up by ``get_factory(name)`` and then
    called with ``build(config, secrets)`` to obtain a live provider.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._providers: dict[str, AIModelProvider] = {}
        self._descriptors: dict[str, AIModelProviderDescriptor] = {}
        self._factories: dict[str, AIModelProviderFactory] = {}

    def register(
        self,
        provider: AIModelProvider,
        *,
        descriptor: AIModelProviderDescriptor | None = None,
    ) -> None:
        # Validate EVERYTHING before touching either dict so a bad
        # descriptor cannot leave the registry in a half-registered state.
        name = getattr(provider, "name", None)
        if not isinstance(name, str) or not name:
            raise ValueError("Provider must expose a non-empty string 'name' attribute")
        if not callable(getattr(provider, "complete", None)):
            raise TypeError("Provider must implement complete(request)")
        if descriptor is not None and descriptor.name != name:
            raise ValueError(
                f"AIModelProviderDescriptor.name {descriptor.name!r} "
                f"does not match provider.name {name!r}"
            )
        # Materialise the descriptor before touching the dictionaries so
        # any construction failure aborts before we mutate state.
        effective_descriptor = descriptor or AIModelProviderDescriptor(
            name=name,
            display_name=getattr(provider, "display_name", "") or name,
            version=getattr(provider, "version", "") or "",
        )

        with self._lock:
            existing = self._providers.get(name)
            if existing is not None and existing is not provider:
                raise ProviderRegistryError(
                    f"AI provider {name!r} is already registered"
                )
            self._providers[name] = provider
            self._descriptors[name] = effective_descriptor

    def unregister(self, name: str) -> None:
        """Remove a provider. Silent no-op if it isn't registered."""
        with self._lock:
            self._providers.pop(name, None)
            self._descriptors.pop(name, None)

    def get(self, name: str) -> AIModelProvider:
        with self._lock:
            provider = self._providers.get(name)
        if provider is None:
            raise ProviderRegistryError(
                f"No AI provider registered with name {name!r}"
            )
        return provider

    def descriptor_for(self, name: str) -> AIModelProviderDescriptor:
        with self._lock:
            descriptor = self._descriptors.get(name)
            if descriptor is not None:
                return descriptor
            factory = self._factories.get(name)
        if factory is not None:
            # Synthesize a descriptor from the factory's configuration spec
            # so callers see a consistent shape regardless of whether the
            # provider was registered as a static instance or a factory.
            spec = factory.configuration_spec
            return AIModelProviderDescriptor(
                name=name,
                display_name=getattr(spec, "display_name", "") or name,
                version=getattr(spec, "version", "") or "",
            )
        raise ProviderRegistryError(
            f"No AI provider registered with name {name!r}"
        )

    def names(self) -> list[str]:
        """Return every provider name — instances AND factories, unified."""
        with self._lock:
            return sorted(set(self._providers) | set(self._factories))

    def instance_names(self) -> list[str]:
        """Return only names registered as pre-built provider instances."""
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

    def register_factory(self, factory: AIModelProviderFactory) -> None:
        """Register a provider factory.

        The factory's ``name`` must be unique across both provider instances
        and factories.  Registering a factory under a name already held by
        a pre-built provider instance raises ``ProviderRegistryError``.
        """
        name = getattr(factory, "name", None)
        if not isinstance(name, str) or not name:
            raise ValueError("Factory must expose a non-empty string 'name' attribute")
        if not callable(getattr(factory, "build", None)):
            raise TypeError("Factory must implement build(config, secrets)")
        if not callable(getattr(factory, "test_connection", None)):
            raise TypeError("Factory must implement test_connection(config, secrets)")
        with self._lock:
            if name in self._providers:
                raise ProviderRegistryError(
                    f"AI provider instance {name!r} is already registered; "
                    "cannot also register a factory under the same name"
                )
            existing_factory = self._factories.get(name)
            if existing_factory is not None and existing_factory is not factory:
                raise ProviderRegistryError(
                    f"AI provider factory {name!r} is already registered"
                )
            self._factories[name] = factory

    def unregister_factory(self, name: str) -> None:
        """Remove a factory. Silent no-op if it isn't registered."""
        with self._lock:
            self._factories.pop(name, None)

    def get_factory(self, name: str) -> AIModelProviderFactory:
        with self._lock:
            factory = self._factories.get(name)
        if factory is None:
            raise ProviderRegistryError(
                f"No AI provider factory registered with name {name!r}"
            )
        return factory

    def factory_names(self) -> list[str]:
        with self._lock:
            return sorted(self._factories)

    def configuration_spec(self, name: str) -> AIProviderConfigurationSpec:
        """Return the configuration spec for a factory registered under ``name``."""
        factory = self.get_factory(name)
        return factory.configuration_spec

    def build_provider(
        self,
        name: str,
        config: dict[str, Any],
        secrets: dict[str, str],
    ) -> AIModelProvider:
        """Return a live provider.

        Static registrations return the shared instance directly; factory
        registrations invoke ``factory.build(config, secrets)``.  Callers
        can therefore treat every name in ``names()`` uniformly.
        """
        with self._lock:
            provider = self._providers.get(name)
        if provider is not None:
            return provider
        factory = self.get_factory(name)
        return factory.build(config, secrets)

    def run_provider_connection_test(
        self,
        name: str,
        config: dict[str, Any],
        secrets: dict[str, str],
    ) -> AIProviderConnectionResult:
        """Run a connection test.

        For static instances the result is deterministic success — the
        provider is already available in-process, no network is involved.
        For factory-backed providers the call is delegated so the factory
        can perform its usual vendor-specific probe.
        """
        with self._lock:
            static = self._providers.get(name)
        if static is not None:
            return AIProviderConnectionResult(
                success=True,
                status="ok",
                message=(
                    f"Static provider {name!r} is registered and ready."
                ),
            )
        factory = self.get_factory(name)
        return factory.test_connection(config, secrets)

    def provider_descriptors(self) -> list[AIModelProviderDescriptor]:
        """Return descriptors for every registered provider.

        Both static instances and factories are included; factory-only
        entries synthesize a descriptor from ``configuration_spec`` so
        callers can iterate a single unified list.
        """
        with self._lock:
            out: list[AIModelProviderDescriptor] = list(
                self._descriptors.values()
            )
            static_names = set(self._descriptors)
            factory_items = list(self._factories.items())
        for name, factory in factory_items:
            if name in static_names:
                continue
            try:
                spec = factory.configuration_spec
                out.append(AIModelProviderDescriptor(
                    name=name,
                    display_name=getattr(spec, "display_name", "") or name,
                    version=getattr(spec, "version", "") or "",
                ))
            except Exception:
                # A misbehaving factory shouldn't take out the whole list.
                out.append(AIModelProviderDescriptor(
                    name=name, display_name=name, version="",
                ))
        return out

    def clear(self) -> None:
        """Test helper: remove every registered provider and factory."""
        with self._lock:
            self._providers.clear()
            self._descriptors.clear()
            self._factories.clear()


# Module-level singleton used by consumers and tests.
_registry = AIModelProviderRegistry()


def register_provider(
    provider: AIModelProvider,
    *,
    descriptor: AIModelProviderDescriptor | None = None,
) -> None:
    _registry.register(provider, descriptor=descriptor)


def unregister_provider(name: str) -> None:
    _registry.unregister(name)


def get_provider(name: str) -> AIModelProvider:
    return _registry.get(name)


def descriptor_for(name: str) -> AIModelProviderDescriptor:
    return _registry.descriptor_for(name)


def get_default_provider() -> AIModelProvider:
    return _registry.default()


def provider_names() -> list[str]:
    return _registry.names()


def register_provider_factory(factory: AIModelProviderFactory) -> None:
    _registry.register_factory(factory)


def unregister_provider_factory(name: str) -> None:
    _registry.unregister_factory(name)


def get_provider_factory(name: str) -> AIModelProviderFactory:
    return _registry.get_factory(name)


def factory_names() -> list[str]:
    return _registry.factory_names()


def get_configuration_spec(name: str) -> AIProviderConfigurationSpec:
    return _registry.configuration_spec(name)


def build_provider(
    name: str,
    config: dict[str, Any],
    secrets: dict[str, str],
) -> AIModelProvider:
    return _registry.build_provider(name, config, secrets)


def run_provider_connection_test(
    name: str,
    config: dict[str, Any],
    secrets: dict[str, str],
) -> AIProviderConnectionResult:
    return _registry.run_provider_connection_test(name, config, secrets)


def provider_descriptors() -> list[AIModelProviderDescriptor]:
    return _registry.provider_descriptors()


def _reset_registry_for_tests() -> None:
    """Test-only hook to clear registry state between test runs."""
    _registry.clear()
