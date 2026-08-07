"""Tests for cauldron.admin.E309-E312 — manifest navigation vs NavigationRegistry."""
import pytest

from cauldron.modules import BaseModule, ModuleManifest, ModuleNavigationDeclaration
from cauldron_django_admin.checks import check_manifest_navigation_registered
from cauldron_django_admin.navigation import (
    AdminNavigationItem,
    AdminNavigationSection,
    NavigationRegistry,
)


def _make_module(slug, *, navigation=()):
    return BaseModule(ModuleManifest(
        slug=slug,
        label=slug,
        navigation=tuple(navigation),
    ))


def _section_decl(key, label="Section"):
    return ModuleNavigationDeclaration(key=key, label=label, section="")


def _item_decl(key, label="Item", section="system"):
    return ModuleNavigationDeclaration(
        key=key,
        label=label,
        section=section,
        url_name="cauldron:dashboard",
    )


@pytest.fixture(autouse=True)
def reset_module_registry():
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
        "_module_states": dict(registry._module_states),
        "_populated": registry._populated,
        "_ready": registry._ready,
    }
    yield
    for attr, value in snap.items():
        setattr(registry, attr, value)


@pytest.fixture()
def fresh_nav_registry(monkeypatch):
    """Return a clean NavigationRegistry and patch get_navigation_registry to return it."""
    reg = NavigationRegistry()
    monkeypatch.setattr(
        "cauldron_django_admin.navigation.get_navigation_registry",
        lambda: reg,
    )
    return reg


def _inject_and_activate(modules):
    from cauldron.modules.registry import registry
    registry.populate(modules)
    registry.activate()


class TestManifestNavigationCheck:

    def test_no_errors_when_no_navigation_declared(self, fresh_nav_registry):
        m = _make_module("mod.a")
        _inject_and_activate([m])
        messages = check_manifest_navigation_registered(None)
        assert messages == []

    def test_no_errors_when_registry_not_ready(self, fresh_nav_registry):
        from cauldron.modules.registry import registry
        m = _make_module("mod.a", navigation=[_section_decl("mod.a")])
        registry.populate([m])
        # Do NOT activate — is_ready remains False
        messages = check_manifest_navigation_registered(None)
        assert messages == []

    # --- E309: missing section ---

    def test_e309_when_declared_section_missing(self, fresh_nav_registry):
        m = _make_module("mod.a", navigation=[_section_decl("mod.a")])
        _inject_and_activate([m])
        messages = check_manifest_navigation_registered(None)
        e309 = [msg for msg in messages if msg.id == "cauldron.admin.E309"]
        assert len(e309) == 1
        assert "mod.a" in e309[0].msg

    def test_no_e309_when_section_registered_by_correct_owner(self, fresh_nav_registry):
        fresh_nav_registry.register_section(AdminNavigationSection(
            key="mod.a",
            label="Mod A",
            order=10,
            owning_module="mod.a",
        ))
        m = _make_module("mod.a", navigation=[_section_decl("mod.a")])
        _inject_and_activate([m])
        messages = check_manifest_navigation_registered(None)
        assert not any(msg.id == "cauldron.admin.E309" for msg in messages)

    # --- E310: missing item ---

    def test_e310_when_declared_item_missing(self, fresh_nav_registry):
        fresh_nav_registry.register_section(AdminNavigationSection(
            key="system", label="System", order=0, owning_module="mod.a",
        ))
        m = _make_module("mod.a", navigation=[_item_decl("mod.a.dashboard")])
        _inject_and_activate([m])
        messages = check_manifest_navigation_registered(None)
        e310 = [msg for msg in messages if msg.id == "cauldron.admin.E310"]
        assert len(e310) == 1
        assert "mod.a.dashboard" in e310[0].msg

    def test_no_e310_when_item_registered_by_correct_owner(self, fresh_nav_registry):
        fresh_nav_registry.register_section(AdminNavigationSection(
            key="system", label="System", order=0, owning_module="mod.a",
        ))
        fresh_nav_registry.register_item(AdminNavigationItem(
            key="mod.a.dashboard",
            label="Dashboard",
            url_name="cauldron:dashboard",
            section="system",
            order=0,
            permission="",
            owning_module="mod.a",
        ))
        m = _make_module("mod.a", navigation=[_item_decl("mod.a.dashboard")])
        _inject_and_activate([m])
        messages = check_manifest_navigation_registered(None)
        assert not any(msg.id == "cauldron.admin.E310" for msg in messages)

    # --- E311: wrong or absent section owner ---

    def test_e311_when_section_owned_by_different_module(self, fresh_nav_registry):
        fresh_nav_registry.register_section(AdminNavigationSection(
            key="mod.a",
            label="Mod A",
            order=10,
            owning_module="some.other.module",
        ))
        m = _make_module("mod.a", navigation=[_section_decl("mod.a")])
        _inject_and_activate([m])
        messages = check_manifest_navigation_registered(None)
        e311 = [msg for msg in messages if msg.id == "cauldron.admin.E311"]
        assert len(e311) == 1
        assert "some.other.module" in e311[0].msg

    def test_e311_when_section_has_no_owner(self, fresh_nav_registry):
        """owning_module='' in the runtime registration must also produce E311."""
        fresh_nav_registry.register_section(AdminNavigationSection(
            key="mod.a",
            label="Mod A",
            order=10,
            owning_module="",
        ))
        m = _make_module("mod.a", navigation=[_section_decl("mod.a")])
        _inject_and_activate([m])
        messages = check_manifest_navigation_registered(None)
        e311 = [msg for msg in messages if msg.id == "cauldron.admin.E311"]
        assert len(e311) == 1
        assert "no owning_module" in e311[0].msg

    # --- E312: wrong or absent item owner ---

    def test_e312_when_item_owned_by_different_module(self, fresh_nav_registry):
        fresh_nav_registry.register_section(AdminNavigationSection(
            key="system", label="System", order=0, owning_module="mod.a",
        ))
        fresh_nav_registry.register_item(AdminNavigationItem(
            key="mod.a.dashboard",
            label="Dashboard",
            url_name="cauldron:dashboard",
            section="system",
            order=0,
            permission="",
            owning_module="some.other.module",
        ))
        m = _make_module("mod.a", navigation=[_item_decl("mod.a.dashboard")])
        _inject_and_activate([m])
        messages = check_manifest_navigation_registered(None)
        e312 = [msg for msg in messages if msg.id == "cauldron.admin.E312"]
        assert len(e312) == 1
        assert "some.other.module" in e312[0].msg

    def test_e312_when_item_has_no_owner(self, fresh_nav_registry):
        """owning_module='' in the runtime registration must also produce E312."""
        fresh_nav_registry.register_section(AdminNavigationSection(
            key="system", label="System", order=0, owning_module="mod.a",
        ))
        fresh_nav_registry.register_item(AdminNavigationItem(
            key="mod.a.dashboard",
            label="Dashboard",
            url_name="cauldron:dashboard",
            section="system",
            order=0,
            permission="",
            owning_module="",
        ))
        m = _make_module("mod.a", navigation=[_item_decl("mod.a.dashboard")])
        _inject_and_activate([m])
        messages = check_manifest_navigation_registered(None)
        e312 = [msg for msg in messages if msg.id == "cauldron.admin.E312"]
        assert len(e312) == 1
        assert "no owning_module" in e312[0].msg

    # --- disabled module ignored ---

    def test_disabled_module_ignored(self, fresh_nav_registry):
        m_enabled = _make_module("mod.enabled")
        m_disabled = _make_module(
            "mod.disabled",
            navigation=[_section_decl("mod.disabled")],
        )
        from cauldron.modules.registry import registry
        # Only enable mod.enabled
        registry.populate([m_enabled, m_disabled], enabled={"mod.enabled"})
        registry.activate()
        messages = check_manifest_navigation_registered(None)
        # E309 must not fire for mod.disabled since it is not active
        assert not any(msg.id == "cauldron.admin.E309" for msg in messages)

    # --- stock configuration (no navigation declared) produces no new errors ---

    def test_stock_config_no_errors(self, fresh_nav_registry):
        m = _make_module("clean.module")
        _inject_and_activate([m])
        messages = check_manifest_navigation_registered(None)
        assert messages == []
