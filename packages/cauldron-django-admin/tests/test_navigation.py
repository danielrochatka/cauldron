"""Tests for the navigation registry."""
import pytest

from cauldron_django_admin.navigation import (
    AdminNavigationItem,
    AdminNavigationSection,
    NavigationRegistry,
)


def _make_registry():
    """Create a fresh registry for each test (don't use the global singleton)."""
    return NavigationRegistry()


def test_register_item_and_section():
    registry = _make_registry()
    section = AdminNavigationSection(key="overview", label="Overview", order=10)
    item = AdminNavigationItem(
        key="dashboard",
        label="Dashboard",
        url_name="cauldron:dashboard",
        section="overview",
        order=10,
        permission="",
    )
    registry.register_section(section)
    registry.register_item(item)

    sections = registry.get_sections()
    assert len(sections) == 1
    assert sections[0].key == "overview"

    items = registry.get_items_for_user(None)
    assert len(items) == 1
    assert items[0].key == "dashboard"


def test_duplicate_key_raises_on_conflict():
    """Re-registering with different attributes must raise; identical
    re-registration is a no-op (idempotent under Django autoreload)."""
    registry = _make_registry()
    section = AdminNavigationSection(key="overview", label="Overview", order=10)
    item = AdminNavigationItem(
        key="dashboard",
        label="Dashboard",
        url_name="cauldron:dashboard",
        section="overview",
        order=10,
        permission="",
    )
    registry.register_section(section)
    registry.register_item(item)

    # Same instance again — idempotent.
    registry.register_section(section)
    registry.register_item(item)

    # Conflicting section: same key, different label.
    with pytest.raises(ValueError, match="already registered"):
        registry.register_section(
            AdminNavigationSection(key="overview", label="Other", order=10),
        )

    # Conflicting item: same key, different label.
    with pytest.raises(ValueError, match="already registered"):
        registry.register_item(
            AdminNavigationItem(
                key="dashboard",
                label="Different",
                url_name="cauldron:dashboard",
                section="overview",
                order=10,
                permission="",
            ),
        )


def test_permission_filtered():
    registry = _make_registry()
    registry.register_section(AdminNavigationSection(key="admin", label="Admin", order=10))
    registry.register_item(AdminNavigationItem(
        key="free",
        label="Free",
        url_name="cauldron:dashboard",
        section="admin",
        order=10,
        permission="",
    ))
    registry.register_item(AdminNavigationItem(
        key="restricted",
        label="Restricted",
        url_name="cauldron:modules",
        section="admin",
        order=20,
        permission="auth.view_user",
    ))

    # User without permission
    class MockUser:
        def has_perm(self, perm):
            return False

    items = registry.get_items_for_user(MockUser())
    keys = [i.key for i in items]
    assert "free" in keys
    assert "restricted" not in keys

    # User with permission
    class SuperUser:
        def has_perm(self, perm):
            return True

    items = registry.get_items_for_user(SuperUser())
    keys = [i.key for i in items]
    assert "free" in keys
    assert "restricted" in keys

    # None user
    items = registry.get_items_for_user(None)
    keys = [i.key for i in items]
    assert "free" in keys
    assert "restricted" not in keys


def test_deterministic_ordering():
    registry = _make_registry()
    registry.register_section(AdminNavigationSection(key="a", label="A Section", order=10))
    registry.register_section(AdminNavigationSection(key="b", label="B Section", order=20))

    # Add items in non-sorted order
    registry.register_item(AdminNavigationItem(
        key="b2", label="B Two", url_name="cauldron:dashboard", section="b", order=20, permission="",
    ))
    registry.register_item(AdminNavigationItem(
        key="a1", label="A One", url_name="cauldron:dashboard", section="a", order=10, permission="",
    ))
    registry.register_item(AdminNavigationItem(
        key="b1", label="B One", url_name="cauldron:dashboard", section="b", order=10, permission="",
    ))
    registry.register_item(AdminNavigationItem(
        key="a2", label="A Two", url_name="cauldron:dashboard", section="a", order=20, permission="",
    ))

    items = registry.get_items_for_user(None)
    keys = [i.key for i in items]
    # Section "a" (order=10) comes before section "b" (order=20)
    # Within each section, items sorted by order
    assert keys.index("a1") < keys.index("a2")
    assert keys.index("b1") < keys.index("b2")
    assert keys.index("a2") < keys.index("b1")


def test_get_grouped_nav():
    registry = _make_registry()
    registry.register_section(AdminNavigationSection(key="overview", label="Overview", order=10))
    registry.register_section(AdminNavigationSection(key="system", label="System", order=900))
    registry.register_item(AdminNavigationItem(
        key="dashboard",
        label="Dashboard",
        url_name="cauldron:dashboard",
        section="overview",
        order=10,
        permission="",
    ))
    registry.register_item(AdminNavigationItem(
        key="modules",
        label="Modules",
        url_name="cauldron:modules",
        section="system",
        order=10,
        permission="",
    ))

    grouped = registry.get_grouped_nav(None)
    assert len(grouped) == 2
    assert grouped[0]["section"].key == "overview"
    assert len(grouped[0]["items"]) == 1
    assert grouped[0]["items"][0].key == "dashboard"
    assert grouped[1]["section"].key == "system"
    assert len(grouped[1]["items"]) == 1
    assert grouped[1]["items"][0].key == "modules"


def test_register_exact_duplicate_idempotent():
    """Registering the exact same section or item twice is a no-op."""
    registry = _make_registry()
    section = AdminNavigationSection(key="s", label="S", order=10)
    item = AdminNavigationItem(
        key="i", label="I", url_name="cauldron:dashboard",
        section="s", order=10, permission="",
    )
    registry.register_section(section)
    registry.register_item(item)
    # Repeat — must NOT raise.
    registry.register_section(section)
    registry.register_item(item)
    assert len(registry.get_sections()) == 1
    assert len(registry.get_items_for_user(None)) == 1


def test_register_different_duplicate_raises():
    """A collision on the same key with different attributes must raise."""
    registry = _make_registry()
    registry.register_section(
        AdminNavigationSection(key="s", label="S", order=10),
    )
    registry.register_item(AdminNavigationItem(
        key="i", label="Original", url_name="cauldron:dashboard",
        section="s", order=10, permission="",
    ))
    with pytest.raises(ValueError, match="different attributes"):
        registry.register_item(AdminNavigationItem(
            key="i", label="Renamed", url_name="cauldron:dashboard",
            section="s", order=10, permission="",
        ))


def _make_request(path: str):
    from types import SimpleNamespace
    return SimpleNamespace(path=path)


def test_dashboard_active_only_at_exact_path():
    """Dashboard entry (url_prefix_exact=True) is NOT active on nested pages."""
    registry = _make_registry()
    registry.register_section(
        AdminNavigationSection(key="overview", label="Overview", order=10),
    )
    registry.register_section(
        AdminNavigationSection(key="content", label="Content", order=200),
    )
    registry.register_item(AdminNavigationItem(
        key="dashboard", label="Dashboard", url_name="cauldron:dashboard",
        section="overview", order=10, permission="",
        url_prefix="/cauldron/", url_prefix_exact=True,
    ))
    registry.register_item(AdminNavigationItem(
        key="content", label="Content", url_name="cauldron:dashboard",
        section="content", order=10, permission="",
        url_prefix="/cauldron/content/",
    ))
    grouped = registry.get_grouped_nav(None, _make_request("/cauldron/content/"))
    active = {
        entry.key: entry.is_active
        for group in grouped for entry in group["items"]
    }
    assert active["content"] is True
    assert active["dashboard"] is False

    grouped_root = registry.get_grouped_nav(None, _make_request("/cauldron/"))
    active_root = {
        entry.key: entry.is_active
        for group in grouped_root for entry in group["items"]
    }
    assert active_root["dashboard"] is True


def test_single_active_item():
    """Only one item is active for a given nested path."""
    registry = _make_registry()
    registry.register_section(
        AdminNavigationSection(key="overview", label="Overview", order=10),
    )
    registry.register_section(
        AdminNavigationSection(key="system", label="System", order=900),
    )
    registry.register_item(AdminNavigationItem(
        key="dashboard", label="Dashboard", url_name="cauldron:dashboard",
        section="overview", order=10, permission="",
        url_prefix="/cauldron/", url_prefix_exact=True,
    ))
    registry.register_item(AdminNavigationItem(
        key="modules", label="Modules", url_name="cauldron:modules",
        section="system", order=10, permission="",
        url_prefix="/cauldron/modules/",
    ))
    grouped = registry.get_grouped_nav(None, _make_request("/cauldron/modules/"))
    active = [
        entry.key
        for group in grouped for entry in group["items"] if entry.is_active
    ]
    assert active == ["modules"]


def test_empty_sections_not_included_in_grouped_nav():
    registry = _make_registry()
    registry.register_section(AdminNavigationSection(key="empty", label="Empty", order=5))
    registry.register_section(AdminNavigationSection(key="overview", label="Overview", order=10))
    registry.register_item(AdminNavigationItem(
        key="dashboard",
        label="Dashboard",
        url_name="cauldron:dashboard",
        section="overview",
        order=10,
        permission="",
    ))

    grouped = registry.get_grouped_nav(None)
    section_keys = [g["section"].key for g in grouped]
    assert "overview" in section_keys
    assert "empty" not in section_keys


def test_owning_module_stored_on_section():
    registry = _make_registry()
    section = AdminNavigationSection(
        key="s", label="S", order=10, owning_module="cauldron.django.admin"
    )
    registry.register_section(section)
    stored = registry.get_sections()[0]
    assert stored.owning_module == "cauldron.django.admin"


def test_owning_module_stored_on_item():
    registry = _make_registry()
    registry.register_section(AdminNavigationSection(key="s", label="S", order=10))
    item = AdminNavigationItem(
        key="i", label="I", url_name="cauldron:dashboard",
        section="s", order=10, permission="",
        owning_module="cauldron.django.admin",
    )
    registry.register_item(item)
    stored = registry.get_items_for_user(None)[0]
    assert stored.owning_module == "cauldron.django.admin"


def test_owning_module_defaults_to_empty_string():
    registry = _make_registry()
    section = AdminNavigationSection(key="s", label="S", order=10)
    item = AdminNavigationItem(
        key="i", label="I", url_name="cauldron:dashboard",
        section="s", order=10, permission="",
    )
    registry.register_section(section)
    registry.register_item(item)
    assert registry.get_sections()[0].owning_module == ""
    assert registry.get_items_for_user(None)[0].owning_module == ""


def test_conflict_error_message_includes_owner():
    registry = _make_registry()
    registry.register_section(
        AdminNavigationSection(key="s", label="S", order=10, owning_module="cauldron.django.admin")
    )
    registry.register_item(AdminNavigationItem(
        key="i", label="Original", url_name="cauldron:dashboard",
        section="s", order=10, permission="",
        owning_module="cauldron.django.admin",
    ))

    with pytest.raises(ValueError, match="cauldron.django.admin"):
        registry.register_section(
            AdminNavigationSection(key="s", label="Different", order=10, owning_module="other.module")
        )

    with pytest.raises(ValueError, match="cauldron.django.admin"):
        registry.register_item(AdminNavigationItem(
            key="i", label="Renamed", url_name="cauldron:dashboard",
            section="s", order=10, permission="",
            owning_module="other.module",
        ))


def test_idempotent_with_owning_module():
    """Exact re-registration including owning_module is still idempotent."""
    registry = _make_registry()
    section = AdminNavigationSection(key="s", label="S", order=10, owning_module="cauldron.django.admin")
    item = AdminNavigationItem(
        key="i", label="I", url_name="cauldron:dashboard",
        section="s", order=10, permission="",
        owning_module="cauldron.django.admin",
    )
    registry.register_section(section)
    registry.register_item(item)
    # Exact re-registration must not raise.
    registry.register_section(section)
    registry.register_item(item)
    assert len(registry.get_sections()) == 1
    assert len(registry.get_items_for_user(None)) == 1
