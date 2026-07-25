"""Integration tests for the Admin AI settings shell.

Covers the settings URL, permission enforcement, navigation integration,
and the content contract for Phase 1 of the settings page.
"""
import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

pytestmark = pytest.mark.django_db


def _make_user(*, username, perms=()):
    User = get_user_model()
    user, _ = User.objects.get_or_create(username=username)
    user.set_password("pw")
    user.save()
    for spec in perms:
        app_label, codename = spec.split(".", 1)
        try:
            perm = Permission.objects.get(
                codename=codename, content_type__app_label=app_label,
            )
        except Permission.DoesNotExist:
            continue
        user.user_permissions.add(perm)
    return User.objects.get(pk=user.pk)


# ---------------------------------------------------------------------------
# URL reversal
# ---------------------------------------------------------------------------

def test_settings_url_reverses():
    url = reverse("cauldron_ai_admin:settings")
    assert url == "/admin/ai/settings/"


# ---------------------------------------------------------------------------
# Permission enforcement
# ---------------------------------------------------------------------------

def test_settings_unauthenticated_redirects():
    client = Client()
    response = client.get(reverse("cauldron_ai_admin:settings"))
    assert response.status_code in (302, 401, 403)


def test_settings_no_permission_returns_403():
    user = _make_user(username="settings-noperm", perms=())
    client = Client()
    client.force_login(user)
    response = client.get(reverse("cauldron_ai_admin:settings"))
    assert response.status_code == 403


def test_settings_authorized_user_gets_200():
    user = _make_user(
        username="settings-admin",
        perms=("cauldron_ai_admin.manage_admin_ai_settings",),
    )
    client = Client()
    client.force_login(user)
    response = client.get(reverse("cauldron_ai_admin:settings"))
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Page content contract
# ---------------------------------------------------------------------------

def test_settings_page_shows_provider_name():
    user = _make_user(
        username="settings-content",
        perms=("cauldron_ai_admin.manage_admin_ai_settings",),
    )
    client = Client()
    client.force_login(user)
    response = client.get(reverse("cauldron_ai_admin:settings"))
    assert response.status_code == 200
    assert b"fake" in response.content


def test_settings_page_shows_provider_status():
    user = _make_user(
        username="settings-status",
        perms=("cauldron_ai_admin.manage_admin_ai_settings",),
    )
    client = Client()
    client.force_login(user)
    response = client.get(reverse("cauldron_ai_admin:settings"))
    assert b"Demo provider active" in response.content


def test_settings_page_has_no_api_key_input():
    user = _make_user(
        username="settings-nokey",
        perms=("cauldron_ai_admin.manage_admin_ai_settings",),
    )
    client = Client()
    client.force_login(user)
    response = client.get(reverse("cauldron_ai_admin:settings"))
    content = response.content.decode()
    # No credential inputs in Phase 1
    assert 'type="password"' not in content
    assert 'api_key' not in content
    assert 'api-key' not in content


def test_settings_page_shows_module_slug():
    user = _make_user(
        username="settings-slug",
        perms=("cauldron_ai_admin.manage_admin_ai_settings",),
    )
    client = Client()
    client.force_login(user)
    response = client.get(reverse("cauldron_ai_admin:settings"))
    assert b"cauldron.ai.admin" in response.content


# ---------------------------------------------------------------------------
# Navigation integration
# ---------------------------------------------------------------------------

def test_settings_spec_registered_in_navigation():
    """ModuleSettingsSpec for cauldron.ai.admin is in the nav registry."""
    from cauldron_django_admin.module_settings import get_module_settings_registry
    specs = {s.module_slug: s for s in get_module_settings_registry().get_specs()}
    assert "cauldron.ai.admin" in specs
    spec = specs["cauldron.ai.admin"]
    assert spec.url_name == "cauldron_ai_admin:settings"
    assert spec.navigation_section == "ai"


def test_settings_nav_item_has_kind_settings():
    """The projected navigation item has kind='settings'."""
    from cauldron_django_admin.navigation import get_navigation_registry

    class _AllowAll:
        def has_perm(self, perm):
            return True

    items = get_navigation_registry().get_items_for_user(_AllowAll())
    settings_item = next(
        (i for i in items if i.key == "cauldron.ai.admin.settings"), None
    )
    assert settings_item is not None
    assert settings_item.kind == "settings"


def test_settings_nav_item_is_last_in_ai_section():
    """Settings item sorts after all other ai-section items."""
    from cauldron_django_admin.navigation import get_navigation_registry

    class _AllowAll:
        def has_perm(self, perm):
            return True

    items = get_navigation_registry().get_items_for_user(_AllowAll())
    ai_items = [i for i in items if i.section == "ai"]
    assert ai_items, "No items in 'ai' section"
    assert ai_items[-1].kind == "settings", (
        f"Last ai item should be 'settings', got {[i.key for i in ai_items]}"
    )


def test_settings_not_in_dashboard_cards():
    """cauldron.ai.admin.settings must not appear as a dashboard card."""
    from cauldron_django_admin.navigation import get_navigation_registry

    class _AllowAll:
        def has_perm(self, perm):
            return True

    cards = get_navigation_registry().get_dashboard_cards(_AllowAll())
    card_keys = [c.key for c in cards]
    assert "cauldron.ai.admin.settings" not in card_keys


def test_settings_css_class_in_grouped_nav():
    """get_grouped_nav entries for the settings item carry kind='settings'."""
    from cauldron_django_admin.navigation import get_navigation_registry
    from types import SimpleNamespace

    class _AllowAll:
        def has_perm(self, perm):
            return True

    grouped = get_navigation_registry().get_grouped_nav(_AllowAll(), SimpleNamespace(path="/"))
    settings_entry = next(
        (
            entry
            for group in grouped
            for entry in group["items"]
            if entry.key == "cauldron.ai.admin.settings"
        ),
        None,
    )
    assert settings_entry is not None
    assert settings_entry.kind == "settings"


# ---------------------------------------------------------------------------
# Existing pages are unaffected
# ---------------------------------------------------------------------------

def test_ai_page_still_works():
    user = _make_user(
        username="settings-ai-page",
        perms=("cauldron_ai_admin.use_admin_ai",),
    )
    client = Client()
    client.force_login(user)
    response = client.get(reverse("cauldron_ai_admin:ai-page"))
    assert response.status_code == 200


def test_run_list_still_works():
    user = _make_user(
        username="settings-runs",
        perms=("cauldron_ai_admin.view_admin_ai_runs",),
    )
    client = Client()
    client.force_login(user)
    response = client.get(reverse("cauldron_ai_admin:run-list"))
    assert response.status_code == 200


def test_style_list_still_works():
    user = _make_user(
        username="settings-styles",
        perms=("cauldron_ai_admin.view_ui_styles",),
    )
    client = Client()
    client.force_login(user)
    response = client.get(reverse("cauldron_ai_admin:style-list"))
    assert response.status_code == 200
