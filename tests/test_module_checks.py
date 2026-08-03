"""Tests for Django system checks emitted by the module runtime."""

import pytest

from django.core import checks as django_checks

from cauldron.modules import BaseModule, ModuleManifest, ModuleRequirement
from cauldron.modules.registry import ModuleRegistry


def _mod(slug, *, version="1.0.0", requires=(), provides=()):
    return BaseModule(ModuleManifest(
        slug=slug,
        label=slug,
        version=version,
        requires=requires,
        provides=provides,
    ))


@pytest.fixture(autouse=True)
def reset_global_registry():
    """Snapshot and restore the global registry around each test."""
    from cauldron.modules.registry import registry

    snap = {
        "_discovered": dict(registry._discovered),
        "_active": dict(registry._active),
        "_load_order": list(registry._load_order),
        "_capability_providers": dict(registry._capability_providers),
        "_capability_overrides": dict(registry._capability_overrides),
        "_module_configs": dict(registry._module_configs),
        "_errors": list(registry._errors),
        "_warnings": list(registry._warnings),
        "_discovery_errors": list(registry._discovery_errors),
        "_lifecycle_errors": list(registry._lifecycle_errors),
        "_enabled": set(registry._enabled),
        "_discovery_records": list(registry._discovery_records),
        "_unavailable": list(registry._unavailable),
        "_populated": registry._populated,
        "_ready": registry._ready,
    }
    yield
    for attr, value in snap.items():
        setattr(registry, attr, value)


def _inject_modules(modules, *, enabled=None, activate=True):
    """Populate (and optionally activate) the global registry for check testing.

    *enabled=None* activates all provided modules (test convenience default).
    *activate=True* runs registry.activate() so is_ready is set, which is
    required for cauldron.I002 to fire.  Pass activate=False for tests that
    deliberately check pre-activation state.
    """
    from cauldron.modules.registry import registry

    registry.populate(modules, enabled=enabled)
    if activate:
        registry.activate()


class TestFoundationCheck:
    def test_cauldron_i001_always_present(self):
        messages = django_checks.run_checks()
        ids = [m.id for m in messages]
        assert "cauldron.I001" in ids

    def test_no_cauldron_errors_with_clean_modules(self):
        _inject_modules([_mod("a")])
        messages = django_checks.run_checks()
        errors = [m for m in messages if m.id.startswith("cauldron.E")]
        assert errors == []


class TestModuleGraphCheck:
    def test_active_modules_reported_as_info(self):
        _inject_modules([_mod("a"), _mod("b")])
        messages = django_checks.run_checks()
        info_msgs = [m for m in messages if m.id == "cauldron.I002"]
        assert len(info_msgs) == 1
        assert "a" in info_msgs[0].msg
        assert "b" in info_msgs[0].msg

    def test_no_i002_when_no_modules_active(self):
        _inject_modules([])
        messages = django_checks.run_checks()
        i002 = [m for m in messages if m.id == "cauldron.I002"]
        assert i002 == []

    def test_missing_dep_emits_e010(self):
        b = _mod("b", requires=(ModuleRequirement(slug="missing"),))
        _inject_modules([b])
        messages = django_checks.run_checks()
        e010 = [m for m in messages if m.id == "cauldron.E010"]
        assert len(e010) == 1
        assert "missing" in e010[0].msg

    def test_missing_capability_emits_e011(self):
        b = _mod("b", requires=(ModuleRequirement(slug="no.cap", kind="capability"),))
        _inject_modules([b])
        messages = django_checks.run_checks()
        e011 = [m for m in messages if m.id == "cauldron.E011"]
        assert len(e011) == 1

    def test_version_constraint_failure_emits_e012(self):
        a = _mod("a", version="1.0.0")
        b = _mod("b", requires=(ModuleRequirement(slug="a", version=">=2.0.0"),))
        _inject_modules([a, b])
        messages = django_checks.run_checks()
        e012 = [m for m in messages if m.id == "cauldron.E012"]
        assert len(e012) == 1

    def test_circular_dep_emits_e014(self):
        a = _mod("a", requires=(ModuleRequirement(slug="b"),))
        b = _mod("b", requires=(ModuleRequirement(slug="a"),))
        _inject_modules([a, b])
        messages = django_checks.run_checks()
        e014 = [m for m in messages if m.id == "cauldron.E014"]
        assert len(e014) == 2

    def test_capability_conflict_emits_e015(self):
        p1 = _mod("p1", provides=("shared.cap",))
        p2 = _mod("p2", provides=("shared.cap",))
        consumer = _mod("consumer", requires=(ModuleRequirement(slug="shared.cap", kind="capability"),))
        _inject_modules([p1, p2, consumer])
        messages = django_checks.run_checks()
        e015 = [m for m in messages if m.id == "cauldron.E015"]
        assert len(e015) == 1

    def test_optional_version_mismatch_emits_w010(self):
        a = _mod("a", version="1.0.0")
        b = BaseModule(ModuleManifest(
            slug="b",
            label="b",
            optional=(ModuleRequirement(slug="a", version=">=2.0.0"),),
        ))
        _inject_modules([a, b])
        messages = django_checks.run_checks()
        w010 = [m for m in messages if m.id == "cauldron.W010"]
        assert len(w010) == 1


class TestDiscoveryErrorChecks:
    def test_load_failure_emits_e020(self):
        from cauldron.modules.discovery import DiscoveryError
        from cauldron.modules.registry import registry

        err = DiscoveryError(
            entry_point_name="broken.ep",
            kind="load_failure",
            message="failed to import",
        )
        registry.populate([], discovery_errors=[err])
        messages = django_checks.run_checks()
        e020 = [m for m in messages if m.id == "cauldron.E020"]
        assert len(e020) == 1
        assert "broken.ep" in e020[0].obj

    def test_duplicate_slug_emits_e021(self):
        from cauldron.modules.discovery import DiscoveryError
        from cauldron.modules.registry import registry

        err = DiscoveryError(
            entry_point_name="dupe.ep",
            kind="duplicate_slug",
            message="slug conflict",
        )
        registry.populate([], discovery_errors=[err])
        messages = django_checks.run_checks()
        e021 = [m for m in messages if m.id == "cauldron.E021"]
        assert len(e021) == 1

    def test_discovery_errors_prevent_i002_for_errored_modules(self):
        from cauldron.modules.discovery import DiscoveryError
        from cauldron.modules.registry import registry

        err = DiscoveryError("ep", "load_failure", "failed")
        registry.populate([], discovery_errors=[err])
        messages = django_checks.run_checks()
        i002 = [m for m in messages if m.id == "cauldron.I002"]
        assert i002 == []  # no active modules when discovery failed


class TestSettingsCheck:
    def test_valid_cauldron_modules_produces_no_error(self):
        _inject_modules([_mod("a")])
        messages = django_checks.run_checks()
        e001 = [m for m in messages if m.id == "cauldron.E001"]
        assert e001 == []

    def test_cauldron_modules_non_dict_emits_e001(self, settings):
        settings.CAULDRON_MODULES = ["cauldron.content"]
        messages = django_checks.run_checks()
        e001 = [m for m in messages if m.id == "cauldron.E001"]
        assert len(e001) == 1

    def test_cauldron_modules_invalid_slug_key_emits_e001(self, settings):
        settings.CAULDRON_MODULES = {"Bad-Slug": {}}
        messages = django_checks.run_checks()
        e001 = [m for m in messages if m.id == "cauldron.E001"]
        assert len(e001) >= 1

    def test_cauldron_modules_non_dict_value_emits_e001(self, settings):
        settings.CAULDRON_MODULES = {"valid.slug": "not-a-dict"}
        messages = django_checks.run_checks()
        e001 = [m for m in messages if m.id == "cauldron.E001"]
        assert len(e001) >= 1

    def test_cauldron_capability_providers_non_dict_emits_e002(self, settings):
        settings.CAULDRON_CAPABILITY_PROVIDERS = "not-a-dict"
        messages = django_checks.run_checks()
        e002 = [m for m in messages if m.id == "cauldron.E002"]
        assert len(e002) == 1

    def test_cauldron_capability_providers_invalid_value_emits_e002(self, settings):
        settings.CAULDRON_CAPABILITY_PROVIDERS = {"valid.cap": 42}
        messages = django_checks.run_checks()
        e002 = [m for m in messages if m.id == "cauldron.E002"]
        assert len(e002) >= 1


class TestLifecycleErrorCheck:
    def test_lifecycle_error_emits_e030(self):
        from cauldron.modules.registry import LifecycleError, registry

        err = LifecycleError(
            module_slug="a",
            phase="on_ready",
            exception=RuntimeError("boom"),
            message="Module 'a' raised in on_ready(): boom",
        )
        registry._lifecycle_errors = [err]
        messages = django_checks.run_checks()
        e030 = [m for m in messages if m.id == "cauldron.E030"]
        assert len(e030) == 1
        assert "a" in e030[0].obj

    def test_no_e030_when_no_lifecycle_errors(self):
        _inject_modules([_mod("a")])
        messages = django_checks.run_checks()
        e030 = [m for m in messages if m.id == "cauldron.E030"]
        assert e030 == []


class TestUnavailableModuleChecks:
    """E023 must fire for slugs in CAULDRON_MODULES that were not discovered."""

    def test_e023_emitted_for_missing_slug(self):
        from cauldron.modules.registry import registry

        registry.populate([], enabled={"phantom.module"})
        messages = django_checks.run_checks()
        e023 = [m for m in messages if m.id == "cauldron.E023"]
        assert len(e023) == 1
        assert e023[0].obj == "phantom.module"

    def test_e023_includes_slug_in_message(self):
        from cauldron.modules.registry import registry

        registry.populate([], enabled={"missing.pkg"})
        messages = django_checks.run_checks()
        e023 = [m for m in messages if m.id == "cauldron.E023"]
        assert len(e023) == 1
        assert "missing.pkg" in e023[0].msg

    def test_no_e023_when_all_enabled_slugs_discovered(self):
        _inject_modules([_mod("a")], enabled={"a"})
        messages = django_checks.run_checks()
        e023 = [m for m in messages if m.id == "cauldron.E023"]
        assert e023 == []

    def test_no_e023_for_load_failure(self):
        """Load-failure already covered by E020; E023 must not fire."""
        from cauldron.modules.discovery import DiscoveryError
        from cauldron.modules.registry import registry

        err = DiscoveryError(
            entry_point_name="broken.ep",
            kind="load_failure",
            message="boom",
            candidate_slug="broken.mod",
        )
        registry.populate([], enabled={"broken.mod"}, discovery_errors=[err])
        messages = django_checks.run_checks()
        e023 = [m for m in messages if m.id == "cauldron.E023"]
        e020 = [m for m in messages if m.id == "cauldron.E020"]
        assert e023 == []
        assert len(e020) == 1

    def test_no_e023_for_manifest_validation_failure(self):
        """Manifest-validation already covered by E022; E023 must not fire."""
        from cauldron.modules.discovery import DiscoveryError
        from cauldron.modules.registry import registry

        err = DiscoveryError(
            entry_point_name="bad.ep",
            kind="manifest_validation",
            message="bad manifest",
            candidate_slug="bad.mod",
        )
        registry.populate([], enabled={"bad.mod"}, discovery_errors=[err])
        messages = django_checks.run_checks()
        e023 = [m for m in messages if m.id == "cauldron.E023"]
        e022 = [m for m in messages if m.id == "cauldron.E022"]
        assert e023 == []
        assert len(e022) == 1


class TestScopedDiscoveryChecks:
    """Discovery errors for disabled modules must be downgraded to W020/W021/W022."""

    def test_load_failure_for_enabled_module_is_error(self):
        from cauldron.modules.discovery import DiscoveryError
        from cauldron.modules.registry import registry

        err = DiscoveryError(
            entry_point_name="en.ep",
            kind="load_failure",
            message="failed",
            candidate_slug="enabled.mod",
        )
        registry.populate([], enabled={"enabled.mod"}, discovery_errors=[err])
        messages = django_checks.run_checks()
        e020 = [m for m in messages if m.id == "cauldron.E020"]
        assert len(e020) == 1

    def test_load_failure_for_disabled_module_is_warning(self):
        from cauldron.modules.discovery import DiscoveryError
        from cauldron.modules.registry import registry

        err = DiscoveryError(
            entry_point_name="dis.ep",
            kind="load_failure",
            message="failed",
            candidate_slug="disabled.mod",
        )
        # enabled={"other"} means "disabled.mod" is not enabled
        registry.populate([], enabled={"other.mod"}, discovery_errors=[err])
        messages = django_checks.run_checks()
        w020 = [m for m in messages if m.id == "cauldron.W020"]
        e020 = [m for m in messages if m.id == "cauldron.E020"]
        assert len(w020) == 1
        assert e020 == []

    def test_manifest_validation_for_disabled_module_is_warning(self):
        from cauldron.modules.discovery import DiscoveryError
        from cauldron.modules.registry import registry

        err = DiscoveryError(
            entry_point_name="val.ep",
            kind="manifest_validation",
            message="bad manifest",
            candidate_slug="bad.mod",
        )
        registry.populate([], enabled=set(), discovery_errors=[err])
        messages = django_checks.run_checks()
        w022 = [m for m in messages if m.id == "cauldron.W022"]
        e022 = [m for m in messages if m.id == "cauldron.E022"]
        assert len(w022) == 1
        assert e022 == []

    def test_error_without_candidate_slug_always_blocking(self):
        """When candidate_slug is None the error kind is unknown; always emit Error."""
        from cauldron.modules.discovery import DiscoveryError
        from cauldron.modules.registry import registry

        err = DiscoveryError(
            entry_point_name="anon.ep",
            kind="load_failure",
            message="load failure before slug known",
            candidate_slug=None,
        )
        # Even with an empty enabled set the error must be E020, not W020.
        registry.populate([], enabled=set(), discovery_errors=[err])
        messages = django_checks.run_checks()
        e020 = [m for m in messages if m.id == "cauldron.E020"]
        assert len(e020) == 1

    def test_empty_enabled_set_known_load_failure_is_warning(self):
        """Empty enabled set + known candidate → W020, not E020."""
        from cauldron.modules.discovery import DiscoveryError
        from cauldron.modules.registry import registry

        err = DiscoveryError(
            entry_point_name="ep",
            kind="load_failure",
            message="failed",
            candidate_slug="known.mod",
        )
        registry.populate([], enabled=set(), discovery_errors=[err])
        messages = django_checks.run_checks()
        w020 = [m for m in messages if m.id == "cauldron.W020"]
        e020 = [m for m in messages if m.id == "cauldron.E020"]
        assert len(w020) == 1
        assert e020 == []

    def test_duplicate_slug_for_disabled_module_is_w021(self):
        from cauldron.modules.discovery import DiscoveryError
        from cauldron.modules.registry import registry

        err = DiscoveryError(
            entry_point_name="dupe.ep",
            kind="duplicate_slug",
            message="slug conflict",
            candidate_slug="dupe.mod",
        )
        registry.populate([], enabled=set(), discovery_errors=[err])
        messages = django_checks.run_checks()
        w021 = [m for m in messages if m.id == "cauldron.W021"]
        e021 = [m for m in messages if m.id == "cauldron.E021"]
        assert len(w021) == 1
        assert e021 == []

    def test_disabled_load_failure_no_e023(self):
        """Disabled load failure should be W020, not E023."""
        from cauldron.modules.discovery import DiscoveryError
        from cauldron.modules.registry import registry

        err = DiscoveryError(
            entry_point_name="ep",
            kind="load_failure",
            message="failed",
            candidate_slug="disabled.mod",
        )
        registry.populate([], enabled=set(), discovery_errors=[err])
        messages = django_checks.run_checks()
        e023 = [m for m in messages if m.id == "cauldron.E023"]
        assert e023 == []
