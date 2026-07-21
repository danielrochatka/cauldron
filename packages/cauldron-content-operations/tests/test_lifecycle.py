"""Tests for the lifecycle state machine."""
import pytest
from cauldron_content_operations.lifecycle import (
    LifecycleError,
    LifecycleState,
    assert_transition,
)


def test_valid_proposed_to_validated():
    assert_transition(LifecycleState.PROPOSED, LifecycleState.VALIDATED)


def test_valid_proposed_to_rejected():
    assert_transition(LifecycleState.PROPOSED, LifecycleState.REJECTED)


def test_invalid_proposed_to_applied():
    with pytest.raises(LifecycleError):
        assert_transition(LifecycleState.PROPOSED, LifecycleState.APPLIED)


def test_invalid_applied_to_validated():
    with pytest.raises(LifecycleError) as exc_info:
        assert_transition(LifecycleState.APPLIED, LifecycleState.VALIDATED)
    assert exc_info.value.code == "lifecycle.invalid_transition"


def test_terminal_states_have_no_transitions():
    for state in [LifecycleState.REJECTED, LifecycleState.ROLLED_BACK]:
        with pytest.raises(LifecycleError):
            # Any transition from terminal state should fail
            assert_transition(state, LifecycleState.PROPOSED)


def test_valid_full_happy_path():
    """Simulate the full happy path transitions."""
    assert_transition(LifecycleState.PROPOSED, LifecycleState.VALIDATED)
    assert_transition(LifecycleState.VALIDATED, LifecycleState.APPROVED)
    assert_transition(LifecycleState.APPROVED, LifecycleState.APPLYING)
    assert_transition(LifecycleState.APPLYING, LifecycleState.APPLIED)


def test_valid_rollback_path():
    assert_transition(LifecycleState.APPLIED, LifecycleState.ROLLING_BACK)
    assert_transition(LifecycleState.ROLLING_BACK, LifecycleState.ROLLED_BACK)
