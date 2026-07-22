"""Provider-neutral reversible-mutation adapter protocol and registry.

Providers that support rollback register a :class:`ReversibleMutationAdapter`
so that :class:`ContentOperationService` can safely roll back an applied
change request without having to know provider-specific details.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ReversibleMutationAdapter(Protocol):
    """Protocol describing provider-specific rollback support."""

    @property
    def supports_rollback(self) -> bool: ...

    def prepare(self, cs_id: str, changeset: Any) -> None:
        """Called just before a mutation to snapshot pre-application state."""

    def record_applied(self, cs_id: str) -> None:
        """Persist post-application hashes by reading canonical files after mutation."""

    def record_rolled_back(self, cs_id: str) -> None:
        """Persist that a rollback succeeded."""

    def rollback(
        self,
        cs_id: str,
        *,
        force: bool = False,
        is_superuser: bool = False,
    ) -> None:
        """Restore the pre-application state.

        Implementations should refuse to overwrite content that has diverged
        from the recorded post-application hashes unless ``force`` is True
        (which itself must require ``is_superuser`` when supplied by callers).
        """

    def has_application_result(self, cs_id: str) -> bool: ...

    def has_rollback_artifact(self, cs_id: str) -> bool: ...

    def inspect(self, cs_id: str) -> dict:
        """Return an inspection payload used by reconciliation and diagnostics."""

    def get_post_application_hashes(self, cs_id: str) -> dict[str, str]: ...


_registry: dict[str, ReversibleMutationAdapter] = {}


def register_adapter(provider_name: str, adapter: ReversibleMutationAdapter) -> None:
    """Register a rollback adapter for a provider (typically at app startup)."""
    _registry[provider_name] = adapter


def get_adapter(provider_name: str) -> ReversibleMutationAdapter | None:
    """Return the adapter registered for ``provider_name`` or None."""
    return _registry.get(provider_name)


def unregister_adapter(provider_name: str) -> None:
    """Remove an adapter (used by tests to keep registration idempotent)."""
    _registry.pop(provider_name, None)


def reset_registry() -> None:
    """Remove all registered adapters (used by tests)."""
    _registry.clear()
