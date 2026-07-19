"""Tests for the ModuleRegistry: populate, activate, query, and graph output."""

import pytest

from cauldron.modules import BaseModule, ModuleManifest, ModuleRequirement
from cauldron.modules.registry import ModuleRegistry


def _mod(slug, *, version="1.0.0", requires=(), optional=(), provides=()):
    manifest = ModuleManifest(
        slug=slug,
        label=slug,
        version=version,
        requires=requires,
        optional=optional,
        provides=provides,
    )
    return BaseModule(manifest)


@pytest.fixture
def registry():
    return ModuleRegistry()


class TestRegistryPopulate:
    def test_empty_populate(self, registry):
        registry.populate([])
        assert registry.is_ready
        assert registry.all_active() == []
        assert registry.all_discovered() == []

    def test_single_module_becomes_active(self, registry):
        a = _mod("a")
        registry.populate([a])
        assert registry.get("a") is a
        assert len(registry.all_active()) == 1

    def test_all_discovered_includes_disabled(self, registry):
        a = _mod("a")
        b = _mod("b")
        registry.populate([a, b], disabled={"b"})
        assert len(registry.all_discovered()) == 2
        assert len(registry.all_active()) == 1
        assert registry.get("b") is None

    def test_disabled_module_not_in_active(self, registry):
        a = _mod("a")
        registry.populate([a], disabled={"a"})
        assert registry.get("a") is None
        assert registry.all_active() == []

    def test_populate_resets_state(self, registry):
        a = _mod("a")
        registry.populate([a])
        assert registry.get("a") is a
        registry.populate([])
        assert registry.get("a") is None

    def test_is_ready_after_populate(self, registry):
        registry.populate([])
        assert registry.is_ready

    def test_not_ready_before_populate(self):
        r = ModuleRegistry()
        assert not r.is_ready


class TestLoadOrder:
    def test_dependency_loaded_before_dependent(self, registry):
        a = _mod("a")
        b = _mod("b", requires=(ModuleRequirement(slug="a"),))
        registry.populate([a, b])
        active = [m.slug for m in registry.all_active()]
        assert active.index("a") < active.index("b")

    def test_chain_loaded_in_order(self, registry):
        a = _mod("a")
        b = _mod("b", requires=(ModuleRequirement(slug="a"),))
        c = _mod("c", requires=(ModuleRequirement(slug="b"),))
        registry.populate([a, b, c])
        active = [m.slug for m in registry.all_active()]
        assert active.index("a") < active.index("b") < active.index("c")


class TestCapabilityRegistration:
    def test_capability_registered_from_provider(self, registry):
        a = _mod("a", provides=("my.capability",))
        registry.populate([a])
        caps = registry.capabilities()
        assert "my.capability" in caps
        assert "a" in caps["my.capability"]

    def test_disabled_module_does_not_provide_capabilities(self, registry):
        a = _mod("a", provides=("my.capability",))
        registry.populate([a], disabled={"a"})
        assert "my.capability" not in registry.capabilities()

    def test_multiple_providers_for_same_capability(self, registry):
        a = _mod("a", provides=("shared.cap",))
        b = _mod("b", provides=("shared.cap",))
        registry.populate([a, b])
        providers = registry.capabilities()["shared.cap"]
        assert set(providers) == {"a", "b"}


class TestDependencyGraph:
    def test_graph_shows_module_deps(self, registry):
        a = _mod("a")
        b = _mod("b", requires=(ModuleRequirement(slug="a"),))
        registry.populate([a, b])
        graph = registry.dependency_graph()
        assert graph["a"] == []
        assert graph["b"] == ["a"]

    def test_graph_includes_discovered_not_just_active(self, registry):
        a = _mod("a")
        b = _mod("b")
        registry.populate([a, b], disabled={"b"})
        graph = registry.dependency_graph()
        assert "a" in graph
        assert "b" in graph

    def test_graph_resolves_capability_deps_to_slugs(self, registry):
        provider = _mod("provider", provides=("cap",))
        consumer = _mod("consumer", requires=(ModuleRequirement(slug="cap", kind="capability"),))
        registry.populate([provider, consumer])
        graph = registry.dependency_graph()
        assert "provider" in graph["consumer"]

    def test_empty_graph_when_no_modules(self, registry):
        registry.populate([])
        assert registry.dependency_graph() == {}


class TestLifecycleActivation:
    def test_on_ready_called_in_load_order(self, registry):
        order = []

        class TrackedModule(BaseModule):
            def on_ready(self):
                order.append(self.slug)

        a = TrackedModule(ModuleManifest(slug="a", label="a"))
        b = TrackedModule(ModuleManifest(
            slug="b", label="b",
            requires=(ModuleRequirement(slug="a"),),
        ))
        registry.populate([a, b])
        registry.activate()
        assert order == ["a", "b"]

    def test_on_ready_not_called_for_disabled_module(self, registry):
        called = []

        class Spy(BaseModule):
            def on_ready(self):
                called.append(self.slug)

        a = Spy(ModuleManifest(slug="a", label="a"))
        registry.populate([a], disabled={"a"})
        registry.activate()
        assert called == []


class TestErrorReporting:
    def test_missing_dep_produces_error(self, registry):
        b = _mod("b", requires=(ModuleRequirement(slug="missing"),))
        registry.populate([b])
        assert registry.has_errors
        assert registry.errors()

    def test_clean_graph_has_no_errors(self, registry):
        a = _mod("a")
        registry.populate([a])
        assert not registry.has_errors
        assert registry.errors() == []


class TestFixtureModuleIntegration:
    """End-to-end tests using real installed fixture packages via discovery."""

    def test_alpha_and_beta_resolve_without_errors(self):
        from cauldron.modules.discovery import discover_modules

        r = ModuleRegistry()
        r.populate(discover_modules())
        assert not r.has_errors

    def test_beta_loads_after_alpha(self):
        from cauldron.modules.discovery import discover_modules

        r = ModuleRegistry()
        r.populate(discover_modules())
        active = [m.slug for m in r.all_active()]
        assert "cauldron.fixture.alpha" in active
        assert "cauldron.fixture.beta" in active
        assert active.index("cauldron.fixture.alpha") < active.index("cauldron.fixture.beta")

    def test_alpha_capability_registered(self):
        from cauldron.modules.discovery import discover_modules

        r = ModuleRegistry()
        r.populate(discover_modules())
        assert "test.capability.alpha" in r.capabilities()

    def test_disabling_alpha_causes_missing_dep_error(self):
        from cauldron.modules.discovery import discover_modules

        r = ModuleRegistry()
        r.populate(discover_modules(), disabled={"cauldron.fixture.alpha"})
        assert r.has_errors
