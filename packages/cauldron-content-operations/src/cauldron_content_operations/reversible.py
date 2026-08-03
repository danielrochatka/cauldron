"""Compatibility re-export of the reversible adapter contract.

The canonical home for the provider-neutral reversible contract is
``cauldron_content.reversible``.  This module re-exports every public name
from there so that existing code that imports from
``cauldron_content_operations.reversible`` continues to work without change.

Because this module re-exports the *same objects* (not copies), identity
checks such as ``isinstance(obj, ReversibleMutationAdapter)`` and
``isinstance(result, PreparationResult)`` are unaffected regardless of which
import path was used.
"""
from cauldron_content.reversible import (  # noqa: F401
    REVERSIBLE_ADAPTER_VERSION,
    AdapterVersionMismatch,
    PreparationResult,
    ReversibleMutationAdapter,
    VerificationResult,
    get_adapter,
    register_adapter,
    reset_registry,
    unregister_adapter,
    validate_adapter_contract,
)
