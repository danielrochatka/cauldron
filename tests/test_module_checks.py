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
        "_module_configs": dict(registry._module_configs),
        "_errors": list(registry._errors),
        "_warnings": list(registry._warnings),
        "_discovery_errors": list(registry._discovery_errors),
        "_ready": registry._ready,
    }
    yield
    for attr, value in snap.items():
        setattr(registry, attr, value)


def _inject_modules(modules, *, enabled=None):
    """Populate the global registry for check testing.

    *enabled=None* activates all provided modules (test convenience default).
    """
    from cauldron.modules.registry import registry

    registry.populate(modules, enabled=enabled)


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
