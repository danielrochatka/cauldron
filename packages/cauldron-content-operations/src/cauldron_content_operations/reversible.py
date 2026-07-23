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
    ``rollback(cs_id, *, force, is_superuser, expected_artifact_digest,
    expected_entry_count)`` — rollback must be bound to trusted evidence.
    ``verify_applied_state(cs_id, *, expected_artifact_digest,
    expected_entry_count)`` and
    ``verify_rolled_back_state(cs_id, *, expected_artifact_digest,
    expected_entry_count)`` — verification is bound to trusted SQL evidence.
    ``load_rollback_completion(cs_id)`` — durable provider completion marker.

Providers advertising ``supports_rollback = True`` MUST implement the
current protocol; the service refuses to use adapters that only implement
a subset of the required members (see :func:`validate_adapter_contract`).
"""
from __future__ import annotations

import inspect
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


@dataclass(frozen=True)
class PreparationResult:
    """Typed result returned by ``ReversibleMutationAdapter.prepare()``.

    Fields:
      * ``artifact_digest`` — SHA-256 hex digest of the written rollback
        artifact bytes. Must be a 64-char lowercase hex string.
      * ``entry_count`` — total number of entries recorded in the artifact.
        Must be a positive, non-bool ``int``.

    The application layer treats this as trusted evidence — anything that
    is not an actual :class:`PreparationResult` instance is rejected. Adapter
    implementations MUST return this concrete type; ad-hoc namedtuples or
    dicts are not accepted.
    """

    artifact_digest: str
    entry_count: int


@runtime_checkable
class ReversibleMutationAdapter(Protocol):
    """Protocol describing provider-specific rollback support (version 2)."""

    # Item 2: adapters MUST declare a protocol version. The service refuses
    # adapters whose ``reversible_adapter_version`` does not match
    # ``REVERSIBLE_ADAPTER_VERSION``.
    reversible_adapter_version: int

    @property
    def supports_rollback(self) -> bool: ...

    def prepare(self, cs_id: str, changeset: Any) -> PreparationResult:
        """Called just before a mutation to snapshot pre-application state.

        MUST return a :class:`PreparationResult` bound to the trusted artifact
        digest and entry count.
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
        expected_entry_count: int = 0,
    ) -> None:
        """Restore pre-application state, bound to trusted digest and count."""

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

    def load_rollback_completion(self, cs_id: str) -> dict | None:
        """Return the durable provider rollback completion marker (Item 7).

        The marker must include ``result_type='rolled_back'``, ``cs_id``,
        ``artifact_digest``, ``entry_count`` and ``adapter_version``. Return
        ``None`` if missing or malformed.
        """


# Item 2: strict protocol requirements for a v2 reversible adapter.
# Providers advertising ``supports_rollback=True`` MUST implement every one
# of these members.
_REQUIRED_ADAPTER_METHODS = (
    "prepare",
    "record_applied",
    "record_rolled_back",
    "rollback",
    "has_rollback_artifact",
    "verify_applied_state",
    "verify_rolled_back_state",
    "inspect",
    "load_rollback_completion",
)


def _signature_accepts(
    adapter: Any,
    method_name: str,
    args: tuple,
    kwargs: dict,
) -> bool:
    """Return True iff ``adapter.method_name`` accepts ``args``/``kwargs``.

    Uses ``inspect.signature().bind()`` to verify the method signature is
    compatible with the required call shape. MagicMock and any object whose
    signature cannot be introspected passes (we cannot enforce signatures on
    dynamic mocks — the runtime behavior is checked elsewhere).
    """
    method = getattr(adapter, method_name, None)
    if not callable(method):
        return False
    try:
        sig = inspect.signature(method)
    except (TypeError, ValueError):
        # Cannot introspect — assume compatible (e.g. MagicMock, C ext).
        return True
    try:
        sig.bind(*args, **kwargs)
    except TypeError:
        return False
    return True


def validate_adapter_contract(adapter: Any) -> list[str]:
    """Return the list of contract violations for ``adapter``.

    An empty list means the adapter satisfies the v2 protocol. This is the
    single source of truth used at registration and at every apply/rollback/
    reconciliation call site.
    """
    violations: list[str] = []
    if adapter is None:
        return ["adapter is None"]

    # Version match.
    version = getattr(adapter, "reversible_adapter_version", None)
    if version != REVERSIBLE_ADAPTER_VERSION:
        violations.append(
            f"reversible_adapter_version={version!r}, expected "
            f"{REVERSIBLE_ADAPTER_VERSION}"
        )

    # supports_rollback must be truthy True.
    supports = getattr(adapter, "supports_rollback", False)
    if supports is not True:
        violations.append("supports_rollback is not True")

    # Presence + callability of every required method.
    for name in _REQUIRED_ADAPTER_METHODS:
        member = getattr(adapter, name, None)
        if not callable(member):
            violations.append(f"missing or non-callable method {name!r}")

    # Signature compatibility for critical methods.
    critical_calls: list[tuple[str, tuple, dict]] = [
        ("record_applied", ("cs",), {"artifact_digest": "d"}),
        (
            "rollback",
            ("cs",),
            {
                "force": False,
                "is_superuser": False,
                "expected_artifact_digest": "d",
                "expected_entry_count": 1,
            },
        ),
        (
            "verify_applied_state",
            ("cs",),
            {"expected_artifact_digest": "d", "expected_entry_count": 1},
        ),
        (
            "verify_rolled_back_state",
            ("cs",),
            {"expected_artifact_digest": "d", "expected_entry_count": 1},
        ),
    ]
    for name, args, kwargs in critical_calls:
        if not callable(getattr(adapter, name, None)):
            continue  # already reported above
        if not _signature_accepts(adapter, name, args, kwargs):
            violations.append(
                f"{name} signature incompatible with required v2 call shape"
            )
    return violations


_registry: dict[str, ReversibleMutationAdapter] = {}


class AdapterVersionMismatch(Exception):
    """Raised when a registered adapter's version does not match the protocol."""


def register_adapter(provider_name: str, adapter: ReversibleMutationAdapter) -> None:
    """Register a rollback adapter for a provider (typically at app startup).

    Uses :func:`validate_adapter_contract` to enforce the full v2 protocol.
    Any violation raises :class:`AdapterVersionMismatch` with the details.
    """
    violations = validate_adapter_contract(adapter)
    if violations:
        raise AdapterVersionMismatch(
            f"Adapter for {provider_name!r} does not satisfy v"
            f"{REVERSIBLE_ADAPTER_VERSION} contract: {'; '.join(violations)}"
        )
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
