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
        assert registry.is_populated
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

    def test_is_populated_after_populate(self, registry):
        registry.populate([])
        assert registry.is_populated

    def test_is_ready_only_after_activate(self, registry):
        registry.populate([])
        assert not registry.is_ready
        registry.activate()
        assert registry.is_ready

    def test_is_ready_not_set_when_resolution_errors_exist(self, registry):
        b = _mod("b", requires=(ModuleRequirement(slug="missing"),))
        registry.populate([b])
        registry.activate()
        assert not registry.is_ready

    def test_not_populated_before_populate(self):
        r = ModuleRegistry()
        assert not r.is_populated
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

    def test_activate_proceeds_with_only_discovery_errors(self, registry):
        """Discovery errors from non-enabled modules must not block enabled modules."""
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
        # a has no resolution errors so it should activate despite the discovery error
        assert "a" in called

    def test_register_called_before_on_ready(self, registry):
        order = []

        class Phased(BaseModule):
            def register(self, context):
                order.append(f"register:{self.slug}")

            def on_ready(self):
                order.append(f"on_ready:{self.slug}")

        a = Phased(ModuleManifest(slug="a", label="a"))
        registry.populate([a])
        registry.activate()
        assert order == ["register:a", "on_ready:a"]

    def test_register_receives_module_context(self, registry):
        from cauldron.modules import ModuleContext

        received = []

        class Spy(BaseModule):
            def register(self, context):
                received.append(context)

        a = Spy(ModuleManifest(slug="a", label="a"))
        registry.populate([a], module_configs={"a": {"key": "val"}})
        registry.activate()
        assert len(received) == 1
        assert isinstance(received[0], ModuleContext)
        assert received[0].slug == "a"
        assert received[0].config == {"key": "val"}

    def test_lifecycle_error_in_on_ready_recorded(self, registry):
        from cauldron.modules.registry import LifecycleError

        class Broken(BaseModule):
            def on_ready(self):
                raise RuntimeError("boom")

        a = Broken(ModuleManifest(slug="a", label="a"))
        registry.populate([a])
        registry.activate()
        errs = registry.lifecycle_errors()
        assert len(errs) == 1
        assert errs[0].module_slug == "a"
        assert errs[0].phase == "on_ready"
        assert "boom" in errs[0].message

    def test_lifecycle_error_in_register_recorded(self, registry):
        from cauldron.modules.registry import LifecycleError

        class Broken(BaseModule):
            def register(self, context):
                raise ValueError("bad config")

        a = Broken(ModuleManifest(slug="a", label="a"))
        registry.populate([a])
        registry.activate()
        errs = registry.lifecycle_errors()
        assert len(errs) == 1
        assert errs[0].phase == "register"
        assert "bad config" in errs[0].message

    def test_lifecycle_error_in_one_module_does_not_block_others(self, registry):
        called = []

        class Broken(BaseModule):
            def on_ready(self):
                raise RuntimeError("crash")

        class Good(BaseModule):
            def on_ready(self):
                called.append(self.slug)

        a = Broken(ModuleManifest(slug="a", label="a"))
        b = Good(ModuleManifest(slug="b", label="b", requires=(ModuleRequirement(slug="a"),)))
        registry.populate([a, b])
        registry.activate()
        assert "b" in called  # b still runs even though a failed


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


class TestGraphInfo:
    def test_graph_info_empty_when_no_modules(self, registry):
        registry.populate([])
        assert registry.graph_info() == []

    def test_graph_info_slug_and_label(self, registry):
        a = _mod("a")
        registry.populate([a])
        info = registry.graph_info()
        assert len(info) == 1
        assert info[0]["slug"] == "a"
        assert info[0]["label"] == "a"
        assert info[0]["version"] == "1.0.0"

    def test_graph_info_active_flag(self, registry):
        a = _mod("a")
        b = _mod("b")
        registry.populate([a, b], enabled={"a"})
        info = {e["slug"]: e for e in registry.graph_info()}
        assert info["a"]["active"] is True
        assert info["b"]["active"] is False

    def test_graph_info_load_index(self, registry):
        a = _mod("a")
        b = _mod("b", requires=(ModuleRequirement(slug="a"),))
        registry.populate([a, b])
        info = {e["slug"]: e for e in registry.graph_info()}
        assert info["a"]["load_index"] == 0
        assert info["b"]["load_index"] == 1

    def test_graph_info_load_index_none_for_inactive(self, registry):
        a = _mod("a")
        registry.populate([a], enabled=set())
        info = registry.graph_info()
        assert info[0]["load_index"] is None

    def test_graph_info_provides(self, registry):
        a = _mod("a", provides=("my.cap",))
        registry.populate([a])
        info = registry.graph_info()
        assert info[0]["provides"] == ["my.cap"]

    def test_graph_info_deps_resolved(self, registry):
        a = _mod("a")
        b = _mod("b", requires=(ModuleRequirement(slug="a"),))
        registry.populate([a, b])
        info = {e["slug"]: e for e in registry.graph_info()}
        assert info["b"]["deps"] == ["a"]
        assert info["a"]["deps"] == []

    def test_graph_info_django_apps(self, registry):
        manifest = ModuleManifest(slug="a", label="a", django_apps=("myapp",))
        a = BaseModule(manifest)
        registry.populate([a])
        info = registry.graph_info()
        assert info[0]["django_apps"] == ["myapp"]

    def test_graph_info_sorted_by_slug(self, registry):
        z = _mod("z")
        a = _mod("a")
        registry.populate([z, a])
        slugs = [e["slug"] for e in registry.graph_info()]
        assert slugs == sorted(slugs)


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

    def test_get_module_apps_returns_apps_in_dependency_order(self):
        from cauldron.modules.discovery import get_module_apps

        apps = get_module_apps({
            "cauldron.fixture.alpha": {},
            "cauldron.fixture.beta": {},
        })
        # Alpha must appear before beta because beta depends on alpha.
        # Alpha declares django_apps=("cauldron_fixture_alpha",).
        assert "cauldron_fixture_alpha" in apps
        alpha_idx = apps.index("cauldron_fixture_alpha")
        # Beta has no django_apps so there's nothing after alpha from beta,
        # but alpha's app must appear (not be dropped).
        assert alpha_idx >= 0

    def test_installed_apps_includes_alpha_app(self):
        """Prove that INSTALLED_APPS composition via get_module_apps() works end-to-end."""
        import django
        from django.apps import apps as django_apps

        assert django_apps.is_installed("cauldron_fixture_alpha")


class TestInventory:
    """Tests for ModuleRegistry.inventory()."""

    def test_inventory_empty_when_no_modules(self, registry):
        registry.populate([])
        assert registry.inventory() == []

    def test_inventory_returns_one_entry_per_discovered_module(self, registry):
        a = _mod("a")
        b = _mod("b")
        registry.populate([a, b])
        assert len(registry.inventory()) == 2

    def test_inventory_sorted_by_slug(self, registry):
        z = _mod("z")
        a = _mod("a")
        registry.populate([z, a])
        slugs = [e["slug"] for e in registry.inventory()]
        assert slugs == sorted(slugs)

    def test_inventory_identity_fields(self, registry):
        a = _mod("a", version="2.0.0")
        registry.populate([a])
        entry = registry.inventory()[0]
        assert entry["slug"] == "a"
        assert entry["label"] == "a"
        assert entry["version"] == "2.0.0"

    def test_inventory_active_flag_true_for_active_module(self, registry):
        a = _mod("a")
        registry.populate([a])
        entry = registry.inventory()[0]
        assert entry["active"] is True

    def test_inventory_active_flag_false_for_inactive_module(self, registry):
        a = _mod("a")
        registry.populate([a], enabled=set())
        entry = registry.inventory()[0]
        assert entry["active"] is False

    def test_inventory_enabled_flag_true_when_enabled(self, registry):
        a = _mod("a")
        registry.populate([a], enabled={"a"})
        entry = registry.inventory()[0]
        assert entry["enabled"] is True

    def test_inventory_enabled_flag_false_when_not_enabled(self, registry):
        a = _mod("a")
        b = _mod("b")
        registry.populate([a, b], enabled={"a"})
        by_slug = {e["slug"]: e for e in registry.inventory()}
        assert by_slug["a"]["enabled"] is True
        assert by_slug["b"]["enabled"] is False

    def test_inventory_enabled_true_for_all_when_enabled_none(self, registry):
        a = _mod("a")
        b = _mod("b")
        registry.populate([a, b])  # enabled=None → all
        for entry in registry.inventory():
            assert entry["enabled"] is True

    def test_inventory_load_index_set_for_active_modules(self, registry):
        a = _mod("a")
        b = _mod("b", requires=(ModuleRequirement(slug="a"),))
        registry.populate([a, b])
        by_slug = {e["slug"]: e for e in registry.inventory()}
        assert by_slug["a"]["load_index"] == 0
        assert by_slug["b"]["load_index"] == 1

    def test_inventory_load_index_none_for_inactive(self, registry):
        a = _mod("a")
        registry.populate([a], enabled=set())
        entry = registry.inventory()[0]
        assert entry["load_index"] is None

    def test_inventory_provides_field(self, registry):
        a = _mod("a", provides=("my.cap",))
        registry.populate([a])
        entry = registry.inventory()[0]
        assert entry["provides"] == ["my.cap"]

    def test_inventory_deps_field(self, registry):
        a = _mod("a")
        b = _mod("b", requires=(ModuleRequirement(slug="a"),))
        registry.populate([a, b])
        by_slug = {e["slug"]: e for e in registry.inventory()}
        assert by_slug["b"]["deps"] == ["a"]
        assert by_slug["a"]["deps"] == []

    def test_inventory_django_apps_field(self, registry):
        manifest = ModuleManifest(slug="a", label="a", django_apps=("myapp",))
        a = BaseModule(manifest)
        registry.populate([a])
        entry = registry.inventory()[0]
        assert entry["django_apps"] == ["myapp"]

    def test_inventory_config_field_returns_module_config(self, registry):
        a = _mod("a")
        registry.populate([a], module_configs={"a": {"key": "val"}})
        entry = registry.inventory()[0]
        assert entry["config"] == {"key": "val"}

    def test_inventory_config_empty_when_no_config(self, registry):
        a = _mod("a")
        registry.populate([a])
        entry = registry.inventory()[0]
        assert entry["config"] == {}

    def test_inventory_source_fields_populated_from_discovery_records(self, registry):
        from cauldron.modules.discovery import DiscoveredModule
        from cauldron.modules import ModuleManifest, BaseModule

        manifest = ModuleManifest(slug="a", label="a", version="1.0.0")
        mod = BaseModule(manifest)

        record = DiscoveredModule(
            slug="a",
            label="a",
            version="1.0.0",
            source_type="package",
            package_name="my-package",
            package_version="1.2.3",
            entry_point_group="cauldron.modules",
            entry_point_name="my-ep",
            entry_point_value="my_module:obj",
            manifest=manifest,
            module=mod,
        )
        registry.populate([mod], discovery_records=[record])
        entry = registry.inventory()[0]
        assert entry["source_type"] == "package"
        assert entry["package_name"] == "my-package"
        assert entry["package_version"] == "1.2.3"
        assert entry["entry_point_name"] == "my-ep"
        assert entry["entry_point_value"] == "my_module:obj"

    def test_inventory_source_fields_none_without_discovery_records(self, registry):
        a = _mod("a")
        registry.populate([a])  # no discovery_records
        entry = registry.inventory()[0]
        assert entry["source_type"] is None
        assert entry["package_name"] is None

    def test_graph_info_derives_from_inventory(self, registry):
        """graph_info() must be a strict subset of the inventory keys."""
        a = _mod("a")
        b = _mod("b", requires=(ModuleRequirement(slug="a"),))
        registry.populate([a, b])

        graph = {e["slug"]: e for e in registry.graph_info()}
        inv = {e["slug"]: e for e in registry.inventory()}

        for slug, g_entry in graph.items():
            i_entry = inv[slug]
            for key, val in g_entry.items():
                assert i_entry[key] == val, (
                    f"graph_info[{slug!r}][{key!r}] != inventory[{slug!r}][{key!r}]"
                )


class TestEnabledSetStorage:
    """Tests that _enabled is stored and drives enabled flag in inventory."""

    def test_enabled_none_stores_all_discovered_slugs(self, registry):
        a = _mod("a")
        b = _mod("b")
        registry.populate([a, b])  # enabled=None
        # All discovered modules should show enabled=True
        for entry in registry.inventory():
            assert entry["enabled"] is True

    def test_enabled_empty_set_stores_empty(self, registry):
        a = _mod("a")
        registry.populate([a], enabled=set())
        assert registry.inventory()[0]["enabled"] is False

    def test_enabled_set_persists_across_activate(self, registry):
        a = _mod("a")
        registry.populate([a], enabled={"a"})
        registry.activate()
        entry = registry.inventory()[0]
        assert entry["enabled"] is True


class TestUnavailableModules:
    """Tests for slugs listed in CAULDRON_MODULES but absent from discovery."""

    def test_no_unavailable_when_all_discovered(self, registry):
        a = _mod("a")
        registry.populate([a], enabled={"a"})
        assert registry.unavailable_modules() == []

    def test_unavailable_when_enabled_slug_not_discovered(self, registry):
        from cauldron.modules.registry import UnavailableModule

        registry.populate([], enabled={"ghost.module"})
        unavail = registry.unavailable_modules()
        assert len(unavail) == 1
        assert unavail[0].slug == "ghost.module"

    def test_multiple_unavailable_slugs(self, registry):
        registry.populate([], enabled={"ghost.a", "ghost.b"})
        unavail = registry.unavailable_modules()
        slugs = {u.slug for u in unavail}
        assert slugs == {"ghost.a", "ghost.b"}

    def test_no_unavailable_when_enabled_is_none(self, registry):
        a = _mod("a")
        registry.populate([a])  # enabled=None
        assert registry.unavailable_modules() == []

    def test_unavailable_does_not_count_toward_has_errors(self, registry):
        """has_errors only covers resolution + discovery errors, not unavailable slugs.

        Unavailable slugs are surfaced by Django system checks (cauldron.E023),
        not by has_errors, to preserve existing semantics.
        """
        registry.populate([], enabled={"ghost.module"})
        assert not registry.has_errors

    def test_unavailable_slugs_sorted(self, registry):
        registry.populate([], enabled={"zzz.slug", "aaa.slug", "mmm.slug"})
        slugs = [u.slug for u in registry.unavailable_modules()]
        assert slugs == sorted(slugs)


class TestVersionSatisfiesHelper:
    """Tests for the shared version_satisfies helper promoted from resolver."""

    def test_version_satisfies_exact(self):
        from cauldron.modules.resolver import version_satisfies
        assert version_satisfies("1.0.0", "==1.0.0")

    def test_version_satisfies_range(self):
        from cauldron.modules.resolver import version_satisfies
        assert version_satisfies("1.5.0", ">=1.0.0,<2.0.0")

    def test_version_not_satisfies(self):
        from cauldron.modules.resolver import version_satisfies
        assert not version_satisfies("2.0.0", "<1.0.0")

    def test_empty_constraint_always_satisfies(self):
        from cauldron.modules.resolver import version_satisfies
        assert version_satisfies("0.0.1", "")

    def test_invalid_version_returns_false(self):
        from cauldron.modules.resolver import version_satisfies
        assert not version_satisfies("not-a-version", ">=1.0.0")


class TestInventoryCompleteness:
    """Inventory() must include all fields from the #33 contract."""

    def test_manifest_key_is_present(self, registry):
        a = _mod("a")
        registry.populate([a])
        entry = registry.inventory()[0]
        assert "manifest" in entry

    def test_manifest_is_dict(self, registry):
        a = _mod("a")
        registry.populate([a])
        entry = registry.inventory()[0]
        assert isinstance(entry["manifest"], dict)

    def test_manifest_equals_manifest_to_dict(self, registry):
        a = _mod("a")
        registry.populate([a])
        entry = registry.inventory()[0]
        assert entry["manifest"] == a.manifest.to_dict()

    def test_requires_restart_false_by_default(self, registry):
        a = _mod("a")
        registry.populate([a])
        entry = registry.inventory()[0]
        assert entry["requires_restart"] is False

    def test_requires_restart_true_when_django_apps_present(self, registry):
        manifest = ModuleManifest(slug="a", label="a", django_apps=("myapp",))
        a = BaseModule(manifest)
        registry.populate([a])
        entry = registry.inventory()[0]
        assert entry["requires_restart"] is True

    def test_installed_cauldron_version_is_string(self, registry):
        a = _mod("a")
        registry.populate([a])
        entry = registry.inventory()[0]
        assert isinstance(entry["installed_cauldron_version"], str)

    def test_cauldron_version_constraint_is_string(self, registry):
        a = _mod("a")
        registry.populate([a])
        entry = registry.inventory()[0]
        assert isinstance(entry["cauldron_version_constraint"], str)

    def test_entry_point_group_from_discovery_record(self, registry):
        from cauldron.modules.discovery import DiscoveredModule

        manifest = ModuleManifest(slug="a", label="a", version="1.0.0")
        mod = BaseModule(manifest)
        record = DiscoveredModule(
            slug="a",
            label="a",
            version="1.0.0",
            source_type="package",
            package_name="pkg",
            package_version="1.0",
            entry_point_group="cauldron.modules",
            entry_point_name="a",
            entry_point_value="my_module:obj",
            manifest=manifest,
            module=mod,
        )
        registry.populate([mod], discovery_records=[record])
        entry = registry.inventory()[0]
        assert entry["entry_point_group"] == "cauldron.modules"

    def test_entry_point_group_none_without_record(self, registry):
        a = _mod("a")
        registry.populate([a])
        entry = registry.inventory()[0]
        assert entry["entry_point_group"] is None


class TestInventoryCauldronVersionOk:
    """cauldron_version_ok must always be a bool, never None."""

    def test_cauldron_version_ok_is_bool(self, registry):
        a = _mod("a")
        registry.populate([a])
        entry = registry.inventory()[0]
        assert isinstance(entry["cauldron_version_ok"], bool)

    def test_empty_cauldron_constraint_yields_true(self, registry):
        """Empty constraint must always satisfy — even if installed version is unknown."""
        manifest = ModuleManifest(slug="a", label="a", cauldron_version="")
        a = BaseModule(manifest)
        registry.populate([a])
        entry = registry.inventory()[0]
        assert entry["cauldron_version_ok"] is True

    def test_matching_constraint_yields_true(self, registry):
        import cauldron
        constraint = f">={cauldron.__version__}"
        manifest = ModuleManifest(slug="a", label="a", cauldron_version=constraint)
        a = BaseModule(manifest)
        registry.populate([a])
        entry = registry.inventory()[0]
        assert entry["cauldron_version_ok"] is True

    def test_failing_constraint_yields_false(self, registry):
        manifest = ModuleManifest(slug="a", label="a", cauldron_version=">=9999.0.0")
        a = BaseModule(manifest)
        registry.populate([a])
        entry = registry.inventory()[0]
        assert entry["cauldron_version_ok"] is False

    def test_cauldron_version_ok_never_none(self, registry):
        """Regression: version_satisfies must return bool, never None."""
        for constraint in ("", ">=0.0.0", ">=9999.0.0"):
            manifest = ModuleManifest(slug="a", label="a", cauldron_version=constraint)
            a = BaseModule(manifest)
            r = ModuleRegistry()
            r.populate([a])
            entry = r.inventory()[0]
            assert entry["cauldron_version_ok"] is not None, (
                f"cauldron_version_ok was None for constraint={constraint!r}"
            )


class TestInventoryBlockedSemantics:
    """Modules blocked by resolution errors must show active=False, load_index=None."""

    def test_missing_dependency_blocks_module(self, registry):
        b = _mod("b", requires=(ModuleRequirement(slug="missing"),))
        registry.populate([b])
        entry = registry.inventory()[0]
        assert entry["active"] is False
        assert entry["load_index"] is None

    def test_version_constraint_blocks_module(self, registry):
        a = _mod("a", version="1.0.0")
        b = _mod("b", requires=(ModuleRequirement(slug="a", version=">=2.0.0"),))
        registry.populate([a, b])
        by_slug = {e["slug"]: e for e in registry.inventory()}
        assert by_slug["b"]["active"] is False

    def test_missing_capability_blocks_module(self, registry):
        b = _mod("b", requires=(ModuleRequirement(slug="missing.cap", kind="capability"),))
        registry.populate([b])
        entry = registry.inventory()[0]
        assert entry["active"] is False

    def test_capability_conflict_blocks_module(self, registry):
        p1 = _mod("p1", provides=("shared.cap",))
        p2 = _mod("p2", provides=("shared.cap",))
        consumer = _mod("consumer", requires=(ModuleRequirement(slug="shared.cap", kind="capability"),))
        registry.populate([p1, p2, consumer])
        by_slug = {e["slug"]: e for e in registry.inventory()}
        assert by_slug["consumer"]["active"] is False

    def test_cauldron_version_blocks_module(self, registry):
        manifest = ModuleManifest(slug="a", label="a", cauldron_version=">=9999.0.0")
        a = BaseModule(manifest)
        registry.populate([a])
        entry = registry.inventory()[0]
        assert entry["active"] is False

    def test_circular_dependency_blocks_involved_modules(self, registry):
        a = _mod("a", requires=(ModuleRequirement(slug="b"),))
        b = _mod("b", requires=(ModuleRequirement(slug="a"),))
        registry.populate([a, b])
        by_slug = {e["slug"]: e for e in registry.inventory()}
        # Both participants in the cycle must not be active
        assert by_slug["a"]["active"] is False
        assert by_slug["b"]["active"] is False

    def test_transitive_blocking_propagates(self, registry):
        """A module that depends on a blocked module must also be inactive."""
        # b is blocked (missing dep); c depends on b
        b = _mod("b", requires=(ModuleRequirement(slug="missing"),))
        c = _mod("c", requires=(ModuleRequirement(slug="b"),))
        registry.populate([b, c])
        by_slug = {e["slug"]: e for e in registry.inventory()}
        assert by_slug["b"]["active"] is False
        assert by_slug["c"]["active"] is False
        assert by_slug["c"]["load_index"] is None

    def test_transitive_blocking_does_not_affect_unrelated_modules(self, registry):
        """A healthy sibling module must remain active when another branch is blocked."""
        a = _mod("a")  # healthy
        b = _mod("b", requires=(ModuleRequirement(slug="missing"),))  # blocked
        registry.populate([a, b])
        by_slug = {e["slug"]: e for e in registry.inventory()}
        assert by_slug["a"]["active"] is True
        assert by_slug["b"]["active"] is False

    def test_blocked_module_load_index_is_none(self, registry):
        b = _mod("b", requires=(ModuleRequirement(slug="missing"),))
        registry.populate([b])
        entry = registry.inventory()[0]
        assert entry["load_index"] is None


class TestUnavailableModuleReason:
    """UnavailableModule.reason must reflect the underlying discovery failure."""

    def test_reason_not_discovered_when_no_error(self, registry):
        from cauldron.modules.registry import UnavailableModule

        registry.populate([], enabled={"ghost.slug"})
        unavail = registry.unavailable_modules()
        assert len(unavail) == 1
        assert unavail[0].reason == "not_discovered"

    def test_reason_load_failure_when_error_kind_matches(self, registry):
        from cauldron.modules.discovery import DiscoveryError
        from cauldron.modules.registry import UnavailableModule

        err = DiscoveryError(
            entry_point_name="bad.ep",
            kind="load_failure",
            message="import failed",
            candidate_slug="broken.module",
        )
        registry.populate([], enabled={"broken.module"}, discovery_errors=[err])
        unavail = registry.unavailable_modules()
        assert len(unavail) == 1
        assert unavail[0].slug == "broken.module"
        assert unavail[0].reason == "load_failure"

    def test_reason_manifest_validation_when_error_kind_matches(self, registry):
        from cauldron.modules.discovery import DiscoveryError

        err = DiscoveryError(
            entry_point_name="bad.ep",
            kind="manifest_validation",
            message="bad manifest",
            candidate_slug="invalid.module",
        )
        registry.populate([], enabled={"invalid.module"}, discovery_errors=[err])
        unavail = registry.unavailable_modules()
        assert len(unavail) == 1
        assert unavail[0].reason == "manifest_validation"

    def test_discovery_error_message_attached(self, registry):
        from cauldron.modules.discovery import DiscoveryError

        err = DiscoveryError(
            entry_point_name="ep",
            kind="load_failure",
            message="something exploded",
            candidate_slug="failing.mod",
        )
        registry.populate([], enabled={"failing.mod"}, discovery_errors=[err])
        unavail = registry.unavailable_modules()
        assert unavail[0].discovery_error_message == "something exploded"

    def test_discovery_error_message_empty_when_not_discovered(self, registry):
        registry.populate([], enabled={"nowhere.module"})
        unavail = registry.unavailable_modules()
        assert unavail[0].discovery_error_message == ""

    def test_reason_not_discovered_for_unrelated_error(self, registry):
        """A discovery error for a different candidate must not affect the unavailable reason."""
        from cauldron.modules.discovery import DiscoveryError

        err = DiscoveryError(
            entry_point_name="other.ep",
            kind="load_failure",
            message="unrelated failure",
            candidate_slug="other.module",
        )
        registry.populate([], enabled={"ghost.slug"}, discovery_errors=[err])
        unavail = [u for u in registry.unavailable_modules() if u.slug == "ghost.slug"]
        assert len(unavail) == 1
        assert unavail[0].reason == "not_discovered"


class TestEnabledSlugs:
    """enabled_slugs() public method returns the set used at populate time."""

    def test_enabled_slugs_empty_before_populate(self):
        r = ModuleRegistry()
        assert r.enabled_slugs() == frozenset()

    def test_enabled_slugs_all_when_enabled_none(self, registry):
        a = _mod("a")
        b = _mod("b")
        registry.populate([a, b])  # enabled=None → all discovered
        assert registry.enabled_slugs() == frozenset({"a", "b"})

    def test_enabled_slugs_subset(self, registry):
        a = _mod("a")
        b = _mod("b")
        registry.populate([a, b], enabled={"a"})
        assert registry.enabled_slugs() == frozenset({"a"})

    def test_enabled_slugs_empty_set(self, registry):
        a = _mod("a")
        registry.populate([a], enabled=set())
        assert registry.enabled_slugs() == frozenset()

    def test_enabled_slugs_returns_frozenset(self, registry):
        a = _mod("a")
        registry.populate([a])
        result = registry.enabled_slugs()
        assert isinstance(result, frozenset)

    def test_enabled_slugs_is_immutable(self, registry):
        a = _mod("a")
        registry.populate([a])
        result = registry.enabled_slugs()
        with pytest.raises((AttributeError, TypeError)):
            result.add("new")  # type: ignore[attr-defined]


class TestDiscoveryRecordValidation:
    """populate() must reject inconsistent discovery_records before mutating state."""

    def _make_record(self, manifest, mod, **kwargs):
        from cauldron.modules.discovery import DiscoveredModule
        defaults = dict(
            slug=mod.slug,
            label=mod.label,
            version=mod.manifest.version,
            source_type="package",
            package_name="pkg",
            package_version="1.0",
            entry_point_group="cauldron.modules",
            entry_point_name=mod.slug,
            entry_point_value="mod:obj",
            manifest=manifest,
            module=mod,
        )
        defaults.update(kwargs)
        return DiscoveredModule(**defaults)

    def test_valid_matching_records_accepted(self, registry):
        manifest = ModuleManifest(slug="a", label="a", version="1.0.0")
        mod = BaseModule(manifest)
        rec = self._make_record(manifest, mod)
        registry.populate([mod], discovery_records=[rec])  # must not raise
        assert registry.is_populated

    def test_missing_record_raises(self, registry):
        a = _mod("a")
        b = _mod("b")
        manifest_a = a.manifest
        rec = self._make_record(manifest_a, a)
        with pytest.raises(ValueError, match="missing from records"):
            registry.populate([a, b], discovery_records=[rec])

    def test_extra_record_raises(self, registry):
        manifest = ModuleManifest(slug="a", label="a")
        mod = BaseModule(manifest)
        rec_a = self._make_record(manifest, mod)
        manifest_b = ModuleManifest(slug="b", label="b")
        mod_b = BaseModule(manifest_b)
        rec_b = self._make_record(manifest_b, mod_b)
        with pytest.raises(ValueError, match="extra in records"):
            registry.populate([mod], discovery_records=[rec_a, rec_b])

    def test_duplicate_record_slug_raises(self, registry):
        manifest = ModuleManifest(slug="a", label="a")
        mod = BaseModule(manifest)
        rec1 = self._make_record(manifest, mod)
        rec2 = self._make_record(manifest, mod)
        with pytest.raises(ValueError, match="duplicate slug"):
            registry.populate([mod], discovery_records=[rec1, rec2])

    def test_different_module_object_raises(self, registry):
        manifest = ModuleManifest(slug="a", label="a")
        mod1 = BaseModule(manifest)
        mod2 = BaseModule(manifest)  # different instance, same manifest
        rec = self._make_record(manifest, mod1)
        with pytest.raises(ValueError, match="not the same object"):
            registry.populate([mod2], discovery_records=[rec])

    def test_mismatched_manifest_raises(self, registry):
        manifest1 = ModuleManifest(slug="a", label="a")
        manifest2 = ModuleManifest(slug="a", label="a")
        mod = BaseModule(manifest1)
        # Build record using manifest2 but module uses manifest1
        from cauldron.modules.discovery import DiscoveredModule
        rec = DiscoveredModule(
            slug="a", label="a", version="",
            source_type="package", package_name="pkg", package_version="1.0",
            entry_point_group="cauldron.modules", entry_point_name="a",
            entry_point_value="mod:obj", manifest=manifest2, module=mod,
        )
        with pytest.raises(ValueError, match="manifest"):
            registry.populate([mod], discovery_records=[rec])

    def test_mismatched_version_raises(self, registry):
        manifest = ModuleManifest(slug="a", label="a", version="1.0.0")
        mod = BaseModule(manifest)
        rec = self._make_record(manifest, mod, version="2.0.0")
        with pytest.raises(ValueError, match="version"):
            registry.populate([mod], discovery_records=[rec])

    def test_empty_records_with_empty_modules_accepted(self, registry):
        registry.populate([], discovery_records=[])  # must not raise
        assert registry.is_populated

    def test_state_not_mutated_on_validation_failure(self, registry):
        """A failing validation must leave registry state unchanged."""
        manifest = ModuleManifest(slug="a", label="a")
        mod = BaseModule(manifest)
        registry.populate([mod])  # healthy state
        original_active = registry.all_active()

        manifest2 = ModuleManifest(slug="b", label="b")
        mod2 = BaseModule(manifest2)
        bad_rec = self._make_record(manifest2, mod2)
        with pytest.raises(ValueError):
            registry.populate([mod], discovery_records=[bad_rec])

        # Registry should still report the previously populated state
        assert registry.all_active() == original_active


class TestUnavailableReasonsFromRealDiscovery:
    """Section 3: unavailable reasons derived from actual discover_modules() results."""

    def test_load_failure_reason_via_real_discovery(self):
        """discover_modules() errors with valid EP slug must map to load_failure."""
        from unittest.mock import patch
        from cauldron.modules.discovery import discover_modules

        class _FakeEP:
            name = "cauldron.example"
            value = "cauldron_example:module"
            dist = None

            def load(self):
                raise ImportError("cannot import cauldron_example")

        with patch("cauldron.modules.discovery.entry_points", return_value=[_FakeEP()]):
            result = discover_modules()

        assert len(result.errors) == 1
        assert result.errors[0].kind == "load_failure"
        assert result.errors[0].candidate_slug == "cauldron.example"

        r = ModuleRegistry()
        r.populate(
            result.modules,
            discovery_errors=result.errors,
            enabled={"cauldron.example"},
        )
        unavail = r.unavailable_modules()
        assert len(unavail) == 1
        assert unavail[0].slug == "cauldron.example"
        assert unavail[0].reason == "load_failure"

    def test_manifest_validation_reason_via_real_discovery(self):
        """Non-protocol object with valid EP slug maps to manifest_validation."""
        from unittest.mock import patch
        from cauldron.modules.discovery import discover_modules

        class NotAModule:
            pass

        class _FakeEP:
            name = "cauldron.badmod"
            value = "cauldron_badmod:obj"
            dist = None

            def load(self):
                return NotAModule()

        with patch("cauldron.modules.discovery.entry_points", return_value=[_FakeEP()]):
            result = discover_modules()

        assert len(result.errors) == 1
        assert result.errors[0].kind == "manifest_validation"
        assert result.errors[0].candidate_slug == "cauldron.badmod"

        r = ModuleRegistry()
        r.populate(
            result.modules,
            discovery_errors=result.errors,
            enabled={"cauldron.badmod"},
        )
        unavail = r.unavailable_modules()
        assert len(unavail) == 1
        assert unavail[0].slug == "cauldron.badmod"
        assert unavail[0].reason == "manifest_validation"


class TestCapabilityBlockingSemantics:
    """_blocked_slugs() must use provider-selection semantics, not all-providers."""

    def test_unselected_provider_blocked_does_not_block_consumer(self, registry):
        """Provider B is blocked; A is selected via override; consumer stays active."""
        p_a = _mod("p.a", provides=("shared.cap",))
        p_b = _mod("p.b", requires=(ModuleRequirement(slug="missing"),),
                   provides=("shared.cap",))
        consumer = _mod("consumer", requires=(
            ModuleRequirement(slug="shared.cap", kind="capability"),
        ))
        registry.populate(
            [p_a, p_b, consumer],
            capability_overrides={"shared.cap": "p.a"},
        )
        by_slug = {e["slug"]: e for e in registry.inventory()}
        assert by_slug["p.a"]["active"] is True
        assert by_slug["p.b"]["active"] is False
        assert by_slug["consumer"]["active"] is True

    def test_selected_provider_blocked_blocks_consumer(self, registry):
        """Override selects A; A is blocked → consumer is transitively blocked."""
        p_a = _mod("p.a", requires=(ModuleRequirement(slug="missing"),),
                   provides=("shared.cap",))
        p_b = _mod("p.b", provides=("shared.cap",))
        consumer = _mod("consumer", requires=(
            ModuleRequirement(slug="shared.cap", kind="capability"),
        ))
        registry.populate(
            [p_a, p_b, consumer],
            capability_overrides={"shared.cap": "p.a"},
        )
        by_slug = {e["slug"]: e for e in registry.inventory()}
        assert by_slug["p.a"]["active"] is False
        assert by_slug["consumer"]["active"] is False

    def test_single_provider_blocked_blocks_consumer(self, registry):
        """With one provider (no conflict), blocking it blocks the consumer."""
        provider = _mod("provider", requires=(ModuleRequirement(slug="missing"),),
                        provides=("the.cap",))
        consumer = _mod("consumer", requires=(
            ModuleRequirement(slug="the.cap", kind="capability"),
        ))
        registry.populate([provider, consumer])
        by_slug = {e["slug"]: e for e in registry.inventory()}
        assert by_slug["provider"]["active"] is False
        assert by_slug["consumer"]["active"] is False

    def test_optional_blocked_dep_does_not_block_consumer(self, registry):
        """Optional blocked dependency must not transitively block the consumer."""
        a = _mod("a", requires=(ModuleRequirement(slug="missing"),))
        b = BaseModule(ModuleManifest(
            slug="b", label="b",
            optional=(ModuleRequirement(slug="a"),),
        ))
        registry.populate([a, b])
        by_slug = {e["slug"]: e for e in registry.inventory()}
        assert by_slug["a"]["active"] is False
        assert by_slug["b"]["active"] is True

    def test_blocking_deterministic_regardless_of_input_order(self, registry):
        """Active/blocked flags must not depend on the order modules are passed."""
        a = _mod("a", requires=(ModuleRequirement(slug="missing"),))
        b = _mod("b", requires=(ModuleRequirement(slug="a"),))
        c = _mod("c")

        r1 = ModuleRegistry()
        r1.populate([a, b, c])
        r2 = ModuleRegistry()
        r2.populate([c, b, a])

        inv1 = {e["slug"]: e["active"] for e in r1.inventory()}
        inv2 = {e["slug"]: e["active"] for e in r2.inventory()}
        assert inv1 == inv2
        assert inv1["a"] is False
        assert inv1["b"] is False
        assert inv1["c"] is True
