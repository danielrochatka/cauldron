"""Tests for factory registration in AIModelProviderRegistry."""
from __future__ import annotations

import pytest

from cauldron_ai.provider_configuration import (
    AIProviderConfigurationSpec,
    AIProviderConnectionResult,
)
from cauldron_ai.providers import (
    AIModelProviderRegistry,
    ProviderRegistryError,
    build_provider,
    factory_names,
    get_configuration_spec,
    get_provider_factory,
    provider_descriptors,
    register_provider_factory,
    run_provider_connection_test,
    unregister_provider_factory,
    _reset_registry_for_tests,
)


# ---------------------------------------------------------------------------
# Fixture: isolated registry via module-level singleton reset
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_registry():
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_factory(name: str = "testfactory"):
    class _Provider:
        def complete(self, req):
            return None

    class _Factory:
        def __init__(self, n):
            self.name = n

        @property
        def configuration_spec(self):
            return AIProviderConfigurationSpec(
                provider_name=self.name, display_name=self.name.title()
            )

        def build(self, config, secrets):
            p = _Provider()
            p.name = self.name
            return p

        def test_connection(self, config, secrets):
            return AIProviderConnectionResult(success=True, status="ok")

    return _Factory(name)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_register_and_get_factory():
    f = _make_factory("myfactory")
    register_provider_factory(f)
    assert get_provider_factory("myfactory") is f


def test_factory_names_includes_registered():
    register_provider_factory(_make_factory("fa"))
    register_provider_factory(_make_factory("fb"))
    names = factory_names()
    assert "fa" in names
    assert "fb" in names


def test_unregister_factory():
    f = _make_factory("tmp")
    register_provider_factory(f)
    unregister_provider_factory("tmp")
    with pytest.raises(ProviderRegistryError):
        get_provider_factory("tmp")


def test_unregister_nonexistent_is_noop():
    unregister_provider_factory("nonexistent")  # should not raise


def test_duplicate_factory_raises():
    f1 = _make_factory("dup")
    f2 = _make_factory("dup")
    register_provider_factory(f1)
    with pytest.raises(ProviderRegistryError, match="already registered"):
        register_provider_factory(f2)


def test_same_factory_instance_reregistration_ok():
    f = _make_factory("same")
    register_provider_factory(f)
    register_provider_factory(f)  # idempotent — no error


def test_factory_conflicts_with_provider_instance():
    from cauldron_ai.providers import register_provider

    class _P:
        name = "conflict"
        def complete(self, req): return None

    register_provider(_P())
    f = _make_factory("conflict")
    with pytest.raises(ProviderRegistryError, match="already registered"):
        register_provider_factory(f)


def test_factory_missing_name_raises():
    class _Bad:
        pass
    with pytest.raises(ValueError, match="name"):
        register_provider_factory(_Bad())  # type: ignore[arg-type]


def test_factory_missing_build_raises():
    class _NoBuild:
        name = "x"
        @property
        def configuration_spec(self): return None
        def test_connection(self, c, s): return None
    with pytest.raises(TypeError, match="build"):
        register_provider_factory(_NoBuild())  # type: ignore[arg-type]


def test_factory_missing_test_connection_raises():
    class _NoTest:
        name = "x"
        @property
        def configuration_spec(self): return None
        def build(self, c, s): return None
    with pytest.raises(TypeError, match="test_connection"):
        register_provider_factory(_NoTest())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# configuration_spec / build_provider / test_provider_connection
# ---------------------------------------------------------------------------

def test_get_configuration_spec():
    f = _make_factory("spectest")
    register_provider_factory(f)
    spec = get_configuration_spec("spectest")
    assert spec.provider_name == "spectest"


def test_build_provider_via_module():
    register_provider_factory(_make_factory("builder"))
    p = build_provider("builder", {}, {})
    assert p.name == "builder"


def test_run_provider_connection_test_via_module():
    register_provider_factory(_make_factory("testable"))
    result = run_provider_connection_test("testable", {}, {})
    assert result.success is True
    assert result.status == "ok"


def test_get_factory_unknown_raises():
    with pytest.raises(ProviderRegistryError, match="No AI provider factory"):
        get_provider_factory("unknown")


def test_build_provider_unknown_raises():
    with pytest.raises(ProviderRegistryError):
        build_provider("unknown", {}, {})


# ---------------------------------------------------------------------------
# provider_descriptors
# ---------------------------------------------------------------------------

def test_provider_descriptors_returns_registered_instances():
    from cauldron_ai.providers import register_provider, AIModelProviderDescriptor

    class _P:
        name = "inst"
        display_name = "Instance"
        version = "1"
        def complete(self, req): return None

    register_provider(
        _P(),
        descriptor=AIModelProviderDescriptor(name="inst", display_name="Instance", version="1"),
    )
    descs = provider_descriptors()
    names = [d.name for d in descs]
    assert "inst" in names


def test_registry_clear_removes_factories():
    register_provider_factory(_make_factory("toclean"))
    _reset_registry_for_tests()
    assert "toclean" not in factory_names()
