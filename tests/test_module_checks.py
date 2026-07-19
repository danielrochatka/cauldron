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
    """Restore the global registry after each test to avoid cross-test contamination."""
    from cauldron.modules.registry import registry

    original_discovered = dict(registry._discovered)
    original_active = dict(registry._active)
    original_order = list(registry._load_order)
    original_caps = dict(registry._capability_providers)
    original_errors = list(registry._errors)
    original_warnings = list(registry._warnings)
    original_ready = registry._ready

    yield

    registry._discovered = original_discovered
    registry._active = original_active
    registry._load_order = original_order
    registry._capability_providers = original_caps
    registry._errors = original_errors
    registry._warnings = original_warnings
    registry._ready = original_ready


def _inject_modules(modules, *, disabled=None):
    """Populate the global registry with given modules for check testing."""
    from cauldron.modules.registry import registry

    registry.populate(modules, disabled=disabled or set())


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

    def test_optional_version_mismatch_emits_w010(self):
        from cauldron.modules.registry import registry

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
