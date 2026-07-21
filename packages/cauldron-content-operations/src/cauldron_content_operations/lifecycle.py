"""Content change-request lifecycle state machine."""
from __future__ import annotations

from enum import Enum


class LifecycleState(str, Enum):
    PROPOSED = "proposed"
    VALIDATED = "validated"
    APPROVED = "approved"
    APPLYING = "applying"
    APPLIED = "applied"
    REJECTED = "rejected"
    APPLY_FAILED = "apply_failed"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"
    ROLLBACK_FAILED = "rollback_failed"
    RECONCILIATION_REQUIRED = "reconciliation_required"


VALID_TRANSITIONS: dict[LifecycleState, set[LifecycleState]] = {
    LifecycleState.PROPOSED: {
        LifecycleState.VALIDATED,
        LifecycleState.REJECTED,
    },
    LifecycleState.VALIDATED: {
        LifecycleState.APPROVED,
        LifecycleState.REJECTED,
    },
    LifecycleState.APPROVED: {
        LifecycleState.APPLYING,
    },
    LifecycleState.APPLYING: {
        LifecycleState.APPLIED,
        LifecycleState.APPLY_FAILED,
        LifecycleState.RECONCILIATION_REQUIRED,
    },
    LifecycleState.APPLIED: {
        LifecycleState.ROLLING_BACK,
    },
    LifecycleState.REJECTED: set(),
    LifecycleState.APPLY_FAILED: {
        LifecycleState.APPLYING,
        LifecycleState.RECONCILIATION_REQUIRED,
    },
    LifecycleState.ROLLING_BACK: {
        LifecycleState.ROLLED_BACK,
        LifecycleState.ROLLBACK_FAILED,
        LifecycleState.RECONCILIATION_REQUIRED,
    },
    LifecycleState.ROLLED_BACK: set(),
    LifecycleState.ROLLBACK_FAILED: {
        LifecycleState.ROLLING_BACK,
        LifecycleState.RECONCILIATION_REQUIRED,
    },
    LifecycleState.RECONCILIATION_REQUIRED: {
        LifecycleState.APPLIED,
        LifecycleState.ROLLED_BACK,
        LifecycleState.APPLY_FAILED,
        LifecycleState.ROLLBACK_FAILED,
    },
}

TERMINAL_STATES = frozenset({
    LifecycleState.APPLIED,
    LifecycleState.REJECTED,
    LifecycleState.ROLLED_BACK,
})

TRANSITIONAL_STATES = frozenset({
    LifecycleState.APPLYING,
    LifecycleState.ROLLING_BACK,
})


class LifecycleError(Exception):
    """Raised for invalid state transitions."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def assert_transition(current: LifecycleState, target: LifecycleState) -> None:
    """Raise LifecycleError if the transition is not valid."""
    allowed = VALID_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise LifecycleError(
            code="lifecycle.invalid_transition",
            message=(
                f"Cannot transition from {current.value!r} to {target.value!r}. "
                f"Allowed: {sorted(s.value for s in allowed) or 'none (terminal state)'}."
            ),
        )
