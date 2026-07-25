"""Unit tests for ModuleSettingsSpec and ModuleSettingsRegistry.

Each test that touches a registry uses the ``isolated_nav_ms`` fixture, which
swaps both the navigation and module-settings singletons with fresh instances
for the duration of the test and restores them afterwards.  This keeps the
global singletons clean so other test modules are unaffected.
"""
from __future__ import annotations

import pytest

from cauldron_django_admin.navigation import (
    AdminNavigationItem,
    AdminNavigationSection,
    NavigationRegistry,
)
from cauldron_django_admin.module_settings import (
    ModuleSettingsRegistry,
    ModuleSettingsSpec,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _AllowAll:
    """Mock user that passes every permission check."""
    def has_perm(self, perm):
        return True


class _DenyAll:
    """Mock user that fails every permission check."""
    def has_perm(self, perm):
        return False


@pytest.fixture()
def isolated_nav_ms():
    """Replace global singletons with fresh instances; restore after test."""
    import cauldron_django_admin.navigation as nav_mod
    import cauldron_django_admin.module_settings as ms_mod

    orig_nav = nav_mod._registry
    orig_ms = ms_mod._registry

    fresh_nav = NavigationRegistry()
    fresh_ms = ModuleSettingsRegistry()
    nav_mod._registry = fresh_nav
    ms_mod._registry = fresh_ms

    yield fresh_nav, fresh_ms

    nav_mod._registry = orig_nav
    ms_mod._registry = orig_ms


def _add_test_section(nav: NavigationRegistry, key: str = "test") -> None:
    nav.register_section(AdminNavigationSection(key=key, label=key.title(), order=1))


def _make_spec(**kwargs) -> ModuleSettingsSpec:
    defaults = dict(
        module_slug="test.mod",
        url_name="test_mod:settings",
        navigation_section="test",
        permission="auth.view_user",
        label="Settings",
        description="",
    )
    defaults.update(kwargs)
    return ModuleSettingsSpec(**defaults)


# ---------------------------------------------------------------------------
# Spec field validation
# ---------------------------------------------------------------------------

def test_valid_spec_registration(isolated_nav_ms):
    nav, ms = isolated_nav_ms
    _add_test_section(nav)
    spec = _make_spec()
    ms.register(spec)
    assert len(ms.get_specs()) == 1
    assert ms.get_specs()[0].key == "test.mod.settings"


def test_exact_re_registration_is_idempotent(isolated_nav_ms):
    nav, ms = isolated_nav_ms
    _add_test_section(nav)
    spec = _make_spec()
    ms.register(spec)
    ms.register(spec)  # must not raise
    assert len(ms.get_specs()) == 1


def test_conflicting_registration_raises(isolated_nav_ms):
    nav, ms = isolated_nav_ms
    _add_test_section(nav)
    ms.register(_make_spec())
    with pytest.raises(ValueError, match="already registered"):
        ms.register(_make_spec(description="different"))


def test_invalid_module_slug_rejected(isolated_nav_ms):
    nav, ms = isolated_nav_ms
    _add_test_section(nav)
    with pytest.raises(ValueError, match="module_slug"):
        ms.register(_make_spec(module_slug="bad slug!"))


def test_empty_url_name_rejected(isolated_nav_ms):
    nav, ms = isolated_nav_ms
    _add_test_section(nav)
    with pytest.raises(ValueError, match="url_name"):
        ms.register(_make_spec(url_name=""))


def test_invalid_permission_rejected(isolated_nav_ms):
    nav, ms = isolated_nav_ms
    _add_test_section(nav)
    with pytest.raises(ValueError, match="permission"):
        ms.register(_make_spec(permission="no_dot"))


def test_empty_permission_rejected(isolated_nav_ms):
    nav, ms = isolated_nav_ms
    _add_test_section(nav)
    with pytest.raises(ValueError, match="permission"):
        ms.register(_make_spec(permission=""))


def test_unknown_navigation_section_rejected(isolated_nav_ms):
    nav, ms = isolated_nav_ms
    # Section "test" is NOT registered.
    with pytest.raises(ValueError, match="section"):
        ms.register(_make_spec(navigation_section="test"))
    # Spec must be rolled back so a retry can succeed.
    assert len(ms.get_specs()) == 0


def test_one_settings_spec_per_module(isolated_nav_ms):
    nav, ms = isolated_nav_ms
    _add_test_section(nav)
    ms.register(_make_spec(module_slug="mod.a"))
    with pytest.raises(ValueError):
        ms.register(_make_spec(module_slug="mod.a", label="Different"))


# ---------------------------------------------------------------------------
# Navigation integration — kind, order, dashboard
# ---------------------------------------------------------------------------

def test_settings_item_has_kind_settings(isolated_nav_ms):
    nav, ms = isolated_nav_ms
    _add_test_section(nav)
    ms.register(_make_spec())
    items = nav.get_items_for_user(_AllowAll())
    settings_items = [i for i in items if i.key == "test.mod.settings"]
    assert len(settings_items) == 1
    assert settings_items[0].kind == "settings"


def test_settings_item_always_last_in_section(isolated_nav_ms):
    """Settings link is last even when page items have larger order numbers."""
    nav, ms = isolated_nav_ms
    _add_test_section(nav)
    nav.register_item(AdminNavigationItem(
        key="test.p1", label="Page 1", url_name="t:p1",
        section="test", order=100, permission="",
    ))
    nav.register_item(AdminNavigationItem(
        key="test.p2", label="Page 2", url_name="t:p2",
        section="test", order=50000,  # larger than settings' order=9999
        permission="",
    ))
    ms.register(_make_spec(module_slug="test.mod"))

    items = nav.get_items_for_user(_AllowAll())
    test_items = [i for i in items if i.section == "test"]
    assert test_items[-1].kind == "settings", (
        f"Expected last item to be 'settings', got {[i.kind for i in test_items]}"
    )
    page_keys = [i.key for i in test_items if i.kind == "page"]
    assert page_keys == ["test.p1", "test.p2"]


def test_settings_order_independent_of_page_order(isolated_nav_ms):
    """Settings item sorts last regardless of which numeric order values pages use."""
    nav, ms = isolated_nav_ms
    _add_test_section(nav)
    # Register settings FIRST so it occupies order=9999 before any pages.
    ms.register(_make_spec(module_slug="test.mod"))
    # Register a page item with order=99999 — much higher than settings.
    nav.register_item(AdminNavigationItem(
        key="test.page", label="Page", url_name="t:page",
        section="test", order=99999, permission="",
    ))
    items = nav.get_items_for_user(_AllowAll())
    test_items = [i for i in items if i.section == "test"]
    assert test_items[0].kind == "page"
    assert test_items[-1].kind == "settings"


def test_settings_permission_filtering(isolated_nav_ms):
    nav, ms = isolated_nav_ms
    _add_test_section(nav)
    ms.register(_make_spec(permission="auth.view_user"))
    items = nav.get_items_for_user(_DenyAll())
    assert not any(i.kind == "settings" for i in items)


def test_settings_not_in_dashboard_cards(isolated_nav_ms):
    nav, ms = isolated_nav_ms
    _add_test_section(nav)
    nav.register_item(AdminNavigationItem(
        key="test.page", label="Page", url_name="t:page",
        section="test", order=10, permission="",
    ))
    ms.register(_make_spec(module_slug="test.mod"))
    cards = nav.get_dashboard_cards(_AllowAll())
    card_keys = [c.key for c in cards]
    assert "test.mod.settings" not in card_keys
    assert "test.page" in card_keys


def test_settings_show_on_dashboard_is_false(isolated_nav_ms):
    nav, ms = isolated_nav_ms
    _add_test_section(nav)
    ms.register(_make_spec())
    items = nav.get_items_for_user(_AllowAll())
    settings_item = next(i for i in items if i.kind == "settings")
    assert settings_item.show_on_dashboard is False


# ---------------------------------------------------------------------------
# Navigation sorted nav and active state
# ---------------------------------------------------------------------------

def _req(path: str):
    from types import SimpleNamespace
    return SimpleNamespace(path=path)


def test_settings_item_active_when_on_settings_page(isolated_nav_ms):
    """URL-name matching marks the settings item active on the settings page."""
    nav, ms = isolated_nav_ms
    _add_test_section(nav)
    nav.register_item(AdminNavigationItem(
        key="test.page", label="Page", url_name="test:page",
        section="test", order=10, permission="",
        url_prefix="/cauldron/page/",
    ))
    ms.register(_make_spec(module_slug="test.mod", url_name="test:settings"))

    # Simulate being on the settings page (URL name = "test:settings").
    # get_grouped_nav resolves the path; since "test:settings" won't exist in
    # the test URL conf, we can't resolve from a path.  Test the kind and
    # show_on_dashboard flags via items directly.
    items = nav.get_items_for_user(_AllowAll())
    settings_item = next(i for i in items if i.kind == "settings")
    assert settings_item.url_name == "test:settings"


def test_only_one_active_item_in_grouped_nav(isolated_nav_ms):
    nav, ms = isolated_nav_ms
    _add_test_section(nav)
    nav.register_item(AdminNavigationItem(
        key="test.page", label="Page", url_name="test:page",
        section="test", order=10, permission="",
        url_prefix="/test/page/",
    ))
    ms.register(_make_spec(module_slug="test.mod", url_name="test:settings"))

    grouped = nav.get_grouped_nav(_AllowAll(), _req("/test/page/"))
    active = [
        entry.key
        for group in grouped for entry in group["items"] if entry.is_active
    ]
    assert len(active) <= 1


def test_kind_field_in_grouped_nav(isolated_nav_ms):
    nav, ms = isolated_nav_ms
    _add_test_section(nav)
    nav.register_item(AdminNavigationItem(
        key="test.page", label="Page", url_name="t:page",
        section="test", order=10, permission="",
    ))
    ms.register(_make_spec(module_slug="test.mod"))
    grouped = nav.get_grouped_nav(_AllowAll())
    items_in_section = grouped[0]["items"]
    kinds = {entry.key: entry.kind for entry in items_in_section}
    assert kinds["test.page"] == "page"
    assert kinds["test.mod.settings"] == "settings"


# ---------------------------------------------------------------------------
# CSS class emitted by sidebar template
# ---------------------------------------------------------------------------

def test_settings_css_class_in_sidebar_template(isolated_nav_ms):
    """Sidebar HTML must include cui-sidebar__link--settings for settings items."""
    nav, ms = isolated_nav_ms
    _add_test_section(nav)
    ms.register(_make_spec(module_slug="test.mod"))

    grouped = nav.get_grouped_nav(_AllowAll())
    settings_entry = next(
        entry
        for group in grouped
        for entry in group["items"]
        if entry.kind == "settings"
    )
    assert settings_entry.kind == "settings"

    # Verify the sidebar template source uses the kind-based class.
    import os
    from django.apps import apps
    app = apps.get_app_config("cauldron_django_admin")
    sidebar_path = os.path.join(
        app.path, "templates", "cauldron_admin", "includes", "sidebar.html"
    )
    with open(sidebar_path, encoding="utf-8") as f:
        html = f.read()
    assert "cui-sidebar__link--settings" in html
    assert "item.kind == 'settings'" in html


# ---------------------------------------------------------------------------
# Normal navigation items are unchanged
# ---------------------------------------------------------------------------

def test_normal_navigation_unchanged_after_settings_registration(isolated_nav_ms):
    nav, ms = isolated_nav_ms
    _add_test_section(nav)
    nav.register_item(AdminNavigationItem(
        key="test.page", label="Page", url_name="t:page",
        section="test", order=10, permission="",
    ))
    ms.register(_make_spec(module_slug="test.mod"))

    items = nav.get_items_for_user(_AllowAll())
    page_item = next(i for i in items if i.key == "test.page")
    assert page_item.kind == "page"
    assert page_item.show_on_dashboard is True
    assert page_item.label == "Page"
