"""Provider-neutral reversible-mutation adapter protocol and registry.

Providers that support rollback register a :class:`ReversibleMutationAdapter`
so that :class:`ContentOperationService` can safely roll back an applied
change request without having to know provider-specific details.

Adapter protocol versions
=========================

``REVERSIBLE_ADAPTER_VERSION = 2`` — current protocol.

* Version 1 (legacy) required:
    ``supports_rollback``, ``prepare``, ``record_applied`` (positional cs_id),
    ``rollback``, ``has_rollback_artifact``, ``inspect``,
    ``verify_applied_state(cs_id)``, ``verify_rolled_back_state(cs_id)``.
    Version 1 adapters are no longer supported by the service.

* Version 2 (current) additionally requires:
    ``record_rolled_back(cs_id)`` — durable provider completion marker
    written after canonical rollback mutations succeed.
    ``record_applied(cs_id, *, artifact_digest)`` — post-state must be
    bound to the trusted artifact digest recorded at prepare().
    ``rollback(cs_id, *, force, is_superuser, expected_artifact_digest)``
    — rollback must be bound to the trusted artifact digest.
    ``verify_applied_state(cs_id, *, expected_artifact_digest,
    expected_entry_count)`` and
    ``verify_rolled_back_state(cs_id, *, expected_artifact_digest,
    expected_entry_count)`` — verification is bound to trusted SQL evidence.

Providers advertising ``supports_rollback = True`` MUST implement the
current protocol; the service refuses to use adapters that only implement
a subset of the required members (see
``ContentOperationService._adapter_fully_supports_rollback``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


REVERSIBLE_ADAPTER_VERSION = 2


@dataclass(frozen=True)
class VerificationResult:
    """Provider-verification response used by reconciliation.

    Statuses:
      * ``"verified"`` — on-disk / provider state matches recorded state
      * ``"missing_evidence"`` — no artifact was found to verify against
      * ``"mismatch"`` — a real content divergence exists
      * ``"corrupt_evidence"`` — the artifact exists but is unreadable/malformed
      * ``"unsupported"`` — verification is not available for this cs_id
    """

    status: str
    reason: str = ""
    details: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "details", dict(self.details))


@runtime_checkable
class ReversibleMutationAdapter(Protocol):
    """Protocol describing provider-specific rollback support (version 2)."""

    @property
    def supports_rollback(self) -> bool: ...

    def prepare(self, cs_id: str, changeset: Any) -> Any:
        """Called just before a mutation to snapshot pre-application state.

        Implementations should return a typed result object with at least
        ``artifact_digest`` and ``entry_count`` attributes so the service can
        bind post-state to a trusted digest.
        """

    def record_applied(self, cs_id: str, *, artifact_digest: str) -> None:
        """Persist post-application state and bind it to ``artifact_digest``."""

    def record_rolled_back(self, cs_id: str) -> None:
        """Persist that a rollback completed durably on the provider side."""

    def rollback(
        self,
        cs_id: str,
        *,
        force: bool = False,
        is_superuser: bool = False,
        expected_artifact_digest: str = "",
    ) -> None:
        """Restore the pre-application state, bound to the trusted digest.

        Implementations should refuse to overwrite content that has diverged
        from the recorded post-application state unless ``force`` is True
        (which itself must require ``is_superuser`` when supplied by callers).
        """

    def has_application_result(self, cs_id: str) -> bool: ...

    def has_rollback_artifact(self, cs_id: str) -> bool: ...

    def inspect(self, cs_id: str) -> dict:
        """Return an inspection payload used by reconciliation and diagnostics."""

    def get_post_application_hashes(self, cs_id: str) -> dict[str, str]: ...

    def verify_applied_state(
        self,
        cs_id: str,
        *,
        expected_artifact_digest: str,
        expected_entry_count: int,
    ) -> "VerificationResult":
        """Confirm on-disk state matches the recorded post-application state.

        Both keyword arguments are trusted SQL evidence; without a digest or
        entry count the adapter MUST return ``"missing_evidence"``.
        """

    def verify_rolled_back_state(
        self,
        cs_id: str,
        *,
        expected_artifact_digest: str,
        expected_entry_count: int,
    ) -> "VerificationResult":
        """Confirm on-disk state matches the recorded pre-application state.

        Both keyword arguments are trusted SQL evidence; without a digest or
        entry count the adapter MUST return ``"missing_evidence"``.
        """


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
