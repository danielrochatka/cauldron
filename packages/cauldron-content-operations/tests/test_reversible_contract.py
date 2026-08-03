"""Regression tests for the reversible adapter contract relocation.

Verifies:
 1. Canonical contract lives in cauldron_content.reversible.
 2. cauldron_content_operations.reversible re-exports identical objects.
 3. Registry is shared across both import paths.
"""
import pytest


class TestCanonicalContractImport:
    def test_can_import_from_cauldron_content(self):
        from cauldron_content.reversible import (
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
        assert REVERSIBLE_ADAPTER_VERSION == 2
        assert PreparationResult is not None

    def test_preparation_result_is_dataclass(self):
        from cauldron_content.reversible import PreparationResult
        r = PreparationResult(artifact_digest="a" * 64, entry_count=3)
        assert r.artifact_digest == "a" * 64
        assert r.entry_count == 3

    def test_verification_result_defaults(self):
        from cauldron_content.reversible import VerificationResult
        r = VerificationResult(status="verified")
        assert r.status == "verified"
        assert r.reason == ""
        assert r.details == {}


class TestCompatibilityShim:
    def test_shim_exports_same_objects(self):
        import cauldron_content.reversible as canonical
        import cauldron_content_operations.reversible as shim

        assert shim.REVERSIBLE_ADAPTER_VERSION is canonical.REVERSIBLE_ADAPTER_VERSION
        assert shim.PreparationResult is canonical.PreparationResult
        assert shim.VerificationResult is canonical.VerificationResult
        assert shim.ReversibleMutationAdapter is canonical.ReversibleMutationAdapter
        assert shim.AdapterVersionMismatch is canonical.AdapterVersionMismatch
        assert shim.validate_adapter_contract is canonical.validate_adapter_contract
        assert shim.register_adapter is canonical.register_adapter
        assert shim.get_adapter is canonical.get_adapter
        assert shim.unregister_adapter is canonical.unregister_adapter
        assert shim.reset_registry is canonical.reset_registry

    def test_isinstance_check_works_across_paths(self):
        from cauldron_content.reversible import PreparationResult as Canonical
        from cauldron_content_operations.reversible import PreparationResult as Shim

        assert Canonical is Shim
        obj = Canonical(artifact_digest="b" * 64, entry_count=1)
        assert isinstance(obj, Shim)

    def test_registry_is_shared(self):
        from cauldron_content.reversible import (
            get_adapter as canonical_get,
            reset_registry,
            unregister_adapter,
        )
        from cauldron_content_operations.reversible import (
            get_adapter as shim_get,
            register_adapter as shim_register,
        )

        reset_registry()
        try:
            adapter = _make_valid_adapter()
            shim_register("test_provider", adapter)
            # Registered via shim, readable via canonical path.
            assert canonical_get("test_provider") is adapter
            # Readable via shim path too.
            assert shim_get("test_provider") is adapter
        finally:
            unregister_adapter("test_provider")
            reset_registry()


class TestValidateAdapterContract:
    def test_valid_adapter_returns_no_violations(self):
        from cauldron_content.reversible import validate_adapter_contract
        adapter = _make_valid_adapter()
        assert validate_adapter_contract(adapter) == []

    def test_none_adapter_returns_violation(self):
        from cauldron_content.reversible import validate_adapter_contract
        violations = validate_adapter_contract(None)
        assert violations == ["adapter is None"]

    def test_wrong_version_is_reported(self):
        from cauldron_content.reversible import validate_adapter_contract
        adapter = _make_valid_adapter()
        adapter.reversible_adapter_version = 1
        violations = validate_adapter_contract(adapter)
        assert any("reversible_adapter_version" in v for v in violations)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_valid_adapter():
    """Return a minimal object that satisfies the v2 adapter contract."""
    from cauldron_content.reversible import REVERSIBLE_ADAPTER_VERSION

    class _Adapter:
        reversible_adapter_version = REVERSIBLE_ADAPTER_VERSION
        supports_rollback = True

        def prepare(self, cs_id, changeset): ...
        def record_applied(self, cs_id, *, artifact_digest=""): ...
        def record_rolled_back(self, cs_id): ...
        def rollback(self, cs_id, *, force=False, is_superuser=False,
                     expected_artifact_digest="", expected_entry_count=0): ...
        def has_application_result(self, cs_id): ...
        def has_rollback_artifact(self, cs_id): ...
        def inspect(self, cs_id): ...
        def get_post_application_hashes(self, cs_id): ...
        def verify_applied_state(self, cs_id, *, expected_artifact_digest,
                                 expected_entry_count): ...
        def verify_rolled_back_state(self, cs_id, *, expected_artifact_digest,
                                     expected_entry_count): ...
        def load_rollback_completion(self, cs_id): ...

    return _Adapter()
