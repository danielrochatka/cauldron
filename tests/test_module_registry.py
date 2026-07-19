"""Tests for ModuleRegistry: populate, activate, query, config, and graph output."""

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

    def test_single_module_becomes_active_with_enabled_none(self, registry):
        a = _mod("a")
        registry.populate([a])  # enabled=None → all active
        assert registry.get("a") is a
        assert len(registry.all_active()) == 1

    def test_enabled_set_activates_only_listed_slugs(self, registry):
        a = _mod("a")
        b = _mod("b")
        registry.populate([a, b], enabled={"a"})
        assert len(registry.all_discovered()) == 2
        assert len(registry.all_active()) == 1
        assert registry.get("b") is None

    def test_empty_enabled_set_activates_nothing(self, registry):
        a = _mod("a")
        registry.populate([a], enabled=set())
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

    def test_all_discovered_returns_sorted(self, registry):
        z = _mod("z")
        a = _mod("a")
        registry.populate([z, a])
        slugs = [m.slug for m in registry.all_discovered()]
        assert slugs == sorted(slugs)


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

    def test_inactive_module_does_not_provide_capabilities(self, registry):
        a = _mod("a", provides=("my.capability",))
        registry.populate([a], enabled=set())
        assert "my.capability" not in registry.capabilities()

    def test_multiple_providers_for_same_capability(self, registry):
        a = _mod("a", provides=("shared.cap",))
        b = _mod("b", provides=("shared.cap",))
        registry.populate([a, b])
        providers = registry.capabilities()["shared.cap"]
        assert set(providers) == {"a", "b"}

    def test_capabilities_returns_sorted_providers(self, registry):
        z = _mod("z", provides=("cap",))
        a = _mod("a", provides=("cap",))
        registry.populate([z, a])
        assert registry.capabilities()["cap"] == ["a", "z"]


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
        registry.populate([a, b], enabled={"a"})
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

    def test_graph_keys_are_sorted(self, registry):
        z = _mod("z")
        a = _mod("a")
        registry.populate([z, a])
        keys = list(registry.dependency_graph().keys())
        assert keys == sorted(keys)


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

    def test_on_ready_not_called_for_inactive_module(self, registry):
        called = []

        class Spy(BaseModule):
            def on_ready(self):
                called.append(self.slug)

        a = Spy(ModuleManifest(slug="a", label="a"))
        registry.populate([a], enabled=set())
        registry.activate()
        assert called == []

    def test_activate_skipped_when_errors_exist(self, registry):
        called = []

        class Spy(BaseModule):
            def on_ready(self):
                called.append(self.slug)

        b = Spy(ModuleManifest(
            slug="b", label="b",
            requires=(ModuleRequirement(slug="missing"),),
        ))
        registry.populate([b])
        assert registry.has_errors
        registry.activate()
        assert called == []  # activation must be skipped

    def test_activate_skipped_when_discovery_errors_exist(self, registry):
        from cauldron.modules.discovery import DiscoveryError

        called = []

        class Spy(BaseModule):
            def on_ready(self):
                called.append(self.slug)

        a = Spy(ModuleManifest(slug="a", label="a"))
        err = DiscoveryError(
            entry_point_name="bad.ep",
            kind="load_failure",
            message="failed",
        )
        registry.populate([a], discovery_errors=[err])
        registry.activate()
        assert called == []


class TestModuleConfig:
    def test_get_module_config_returns_provided_config(self, registry):
        a = _mod("a")
        registry.populate([a], module_configs={"a": {"key": "value", "flag": True}})
        config = registry.get_module_config("a")
        assert config == {"key": "value", "flag": True}

    def test_get_module_config_returns_empty_dict_when_absent(self, registry):
        a = _mod("a")
        registry.populate([a])
        assert registry.get_module_config("a") == {}

    def test_get_module_config_returns_copy(self, registry):
        a = _mod("a")
        registry.populate([a], module_configs={"a": {"k": "v"}})
        config1 = registry.get_module_config("a")
        config1["k"] = "mutated"
        assert registry.get_module_config("a") == {"k": "v"}  # unchanged

    def test_config_available_for_inactive_module(self, registry):
        a = _mod("a")
        registry.populate([a], enabled=set(), module_configs={"a": {"debug": True}})
        assert registry.get_module_config("a") == {"debug": True}


class TestDiscoveryErrors:
    def test_discovery_errors_stored_in_registry(self, registry):
        from cauldron.modules.discovery import DiscoveryError

        err = DiscoveryError(
            entry_point_name="bad.ep",
            kind="load_failure",
            message="could not load",
        )
        registry.populate([], discovery_errors=[err])
        assert registry.discovery_errors() == [err]

    def test_discovery_errors_count_toward_has_errors(self, registry):
        from cauldron.modules.discovery import DiscoveryError

        registry.populate([], discovery_errors=[
            DiscoveryError("ep", "load_failure", "oops")
        ])
        assert registry.has_errors

    def test_no_discovery_errors_by_default(self, registry):
        registry.populate([])
        assert registry.discovery_errors() == []


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
        r.populate(discover_modules().modules)
        assert not r.has_errors

    def test_beta_loads_after_alpha(self):
        from cauldron.modules.discovery import discover_modules

        r = ModuleRegistry()
        r.populate(discover_modules().modules)
        active = [m.slug for m in r.all_active()]
        assert "cauldron.fixture.alpha" in active
        assert "cauldron.fixture.beta" in active
        assert active.index("cauldron.fixture.alpha") < active.index("cauldron.fixture.beta")

    def test_alpha_capability_registered(self):
        from cauldron.modules.discovery import discover_modules

        r = ModuleRegistry()
        r.populate(discover_modules().modules)
        assert "test.capability.alpha" in r.capabilities()

    def test_deactivating_alpha_causes_missing_dep_error(self):
        from cauldron.modules.discovery import discover_modules

        r = ModuleRegistry()
        r.populate(
            discover_modules().modules,
            enabled={"cauldron.fixture.beta"},  # alpha not enabled
        )
        assert r.has_errors

    def test_get_module_app_returns_apps_for_enabled(self):
        from cauldron.modules.discovery import get_module_apps

        apps = get_module_apps(["cauldron.fixture.alpha"])
        assert isinstance(apps, list)

    def test_get_module_apps_accepts_dict(self):
        from cauldron.modules.discovery import get_module_apps

        apps = get_module_apps({"cauldron.fixture.alpha": {}})
        assert isinstance(apps, list)
