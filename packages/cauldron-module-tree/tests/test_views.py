"""Tests for Django views in cauldron_module_tree."""
import json
import sys
import types
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from django.test import Client
from django.urls import reverse


# --------------------------------------------------------------------------- #
# Helpers (replicated locally — not imported from cauldron-django-admin)       #
# --------------------------------------------------------------------------- #

def _fake_registry_ctx(inventory_entries, *, errors=(), capabilities=None):
    """Context manager that patches cauldron.modules.registry with a fake."""

    @contextmanager
    def _ctx():
        fake_registry = MagicMock()
        fake_registry.inventory.return_value = list(inventory_entries)
        fake_registry.capabilities.return_value = capabilities or {}
        fake_registry.errors.return_value = list(errors)
        fake_registry.graph_info.return_value = list(inventory_entries)

        fake_module = types.ModuleType("cauldron.modules.registry")
        fake_module.registry = fake_registry

        original = sys.modules.get("cauldron.modules.registry")
        sys.modules["cauldron.modules.registry"] = fake_module
        try:
            yield fake_registry
        finally:
            if original is None:
                sys.modules.pop("cauldron.modules.registry", None)
            else:
                sys.modules["cauldron.modules.registry"] = original

    return _ctx()


def _make_inventory_entry(**kwargs):
    """Return a minimal valid inventory entry dict.

    Uses a safe default slug that satisfies both the Cauldron slug validator
    (dotted lowercase) and Django's URL slug converter ([-a-zA-Z0-9_]).
    When the caller needs a hyphenated slug for URL routing, they can pass
    ``manifest=None`` to skip manifest creation.
    """
    slug = kwargs.get("slug", "test.mod")
    # Build a manifest dict only when slug is a valid Cauldron dotted slug.
    try:
        from cauldron.modules import ModuleManifest
        manifest = ModuleManifest(slug=slug, label=slug.replace(".", " ").title())
        manifest_dict = manifest.to_dict()
    except (ValueError, Exception):
        manifest_dict = None

    defaults = {
        "slug": slug,
        "label": slug.replace(".", " ").replace("-", " ").title(),
        "version": "1.0.0",
        "state": "ready",
        "enabled": True,
        "active": True,
        "load_index": 0,
        "source_type": "package",
        "source": "test-package",
        "manifest": manifest_dict,
        "provides": [],
        "requires": [],
        "optional": [],
        "deps": [],
        "django_apps": [],
        "errors": [],
        "requires_restart": False,
        "cauldron_version_ok": True,
        "installed_cauldron_version": "0.1.0",
    }
    defaults.update(kwargs)
    return defaults


# --------------------------------------------------------------------------- #
# URL helpers                                                                  #
# --------------------------------------------------------------------------- #

def _tree_url():
    return reverse("cauldron_module_tree:tree")


def _graph_api_url():
    return reverse("cauldron_module_tree:graph_api")


def _preview_change_url(slug):
    return reverse("cauldron_module_tree:preview_change", kwargs={"module_slug": slug})


def _enable_url(slug):
    return reverse("cauldron_module_tree:enable_module", kwargs={"module_slug": slug})


def _disable_url(slug):
    return reverse("cauldron_module_tree:disable_module", kwargs={"module_slug": slug})


# --------------------------------------------------------------------------- #
# tree_view tests                                                              #
# --------------------------------------------------------------------------- #

@pytest.mark.django_db
def test_tree_view_requires_login():
    """Anonymous user is redirected to login."""
    client = Client()
    response = client.get(_tree_url())
    assert response.status_code in (301, 302)
    assert "login" in response["Location"] or "/auth/" in response["Location"]


@pytest.mark.django_db
def test_tree_view_authenticated_renders():
    """Logged-in user with view perm can access tree view."""
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    User = get_user_model()
    user = User.objects.create_user(username="tree_viewer", password="testpass123")
    try:
        perm = Permission.objects.get(codename="view_module_tree")
        user.user_permissions.add(perm)
    except Permission.DoesNotExist:
        pass
    # Re-fetch user to get fresh permission cache
    user = User.objects.get(pk=user.pk)

    client = Client()
    client.force_login(user)
    response = client.get(_tree_url())
    assert response.status_code == 200


@pytest.mark.django_db
def test_tree_view_without_permission_returns_403():
    """User without view_module_tree perm gets 403."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user = User.objects.create_user(username="tree_noperm", password="testpass123")
    client = Client()
    client.force_login(user)
    response = client.get(_tree_url())
    assert response.status_code == 403


# --------------------------------------------------------------------------- #
# graph_api tests                                                              #
# --------------------------------------------------------------------------- #

@pytest.mark.django_db
def test_graph_api_requires_login():
    """Anonymous user gets redirect (302) from graph API."""
    client = Client()
    response = client.get(_graph_api_url())
    assert response.status_code in (301, 302)


@pytest.mark.django_db
def test_graph_api_returns_json():
    """Authenticated user with perm gets JSON response with nodes/edges/metadata."""
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    User = get_user_model()
    user = User.objects.create_user(username="graph_viewer", password="testpass123")
    try:
        perm = Permission.objects.get(codename="view_module_tree")
        user.user_permissions.add(perm)
    except Permission.DoesNotExist:
        pass
    # Re-fetch to clear permission cache
    user = User.objects.get(pk=user.pk)

    entry = _make_inventory_entry(slug="api.test")
    with _fake_registry_ctx([entry]):
        client = Client()
        client.force_login(user)
        response = client.get(_graph_api_url())

    assert response.status_code == 200
    data = json.loads(response.content)
    assert "nodes" in data
    assert "edges" in data
    assert "metadata" in data


@pytest.mark.django_db
def test_graph_api_handles_registry_error_gracefully():
    """Registry.inventory() raises — returns error JSON, not an unhandled 500."""
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    User = get_user_model()
    user = User.objects.create_user(username="graph_err", password="testpass123")
    try:
        perm = Permission.objects.get(codename="view_module_tree")
        user.user_permissions.add(perm)
    except Permission.DoesNotExist:
        pass
    user = User.objects.get(pk=user.pk)

    @contextmanager
    def _error_registry_ctx():
        fake_registry = MagicMock()
        fake_registry.inventory.side_effect = RuntimeError("registry exploded")
        fake_module = types.ModuleType("cauldron.modules.registry")
        fake_module.registry = fake_registry
        original = sys.modules.get("cauldron.modules.registry")
        sys.modules["cauldron.modules.registry"] = fake_module
        try:
            yield
        finally:
            if original is None:
                sys.modules.pop("cauldron.modules.registry", None)
            else:
                sys.modules["cauldron.modules.registry"] = original

    with _error_registry_ctx():
        client = Client()
        client.force_login(user)
        response = client.get(_graph_api_url())

    # Should return JSON error, not raise an unhandled exception
    assert response.status_code in (200, 400, 500)
    data = json.loads(response.content)
    assert "error" in data or "nodes" in data


# --------------------------------------------------------------------------- #
# preview_change tests                                                         #
# --------------------------------------------------------------------------- #

@pytest.mark.django_db
def test_preview_change_requires_login():
    """Anonymous user is redirected from preview_change."""
    client = Client()
    response = client.post(
        _preview_change_url("mymodule"),
        data=json.dumps({"action": "disable"}),
        content_type="application/json",
    )
    assert response.status_code in (301, 302)


@pytest.mark.django_db
def test_preview_change_requires_post():
    """GET request to preview_change returns 405."""
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    User = get_user_model()
    user = User.objects.create_user(username="preview_get", password="testpass123")
    try:
        perm = Permission.objects.get(codename="change_module_state")
        user.user_permissions.add(perm)
    except Permission.DoesNotExist:
        pass
    user = User.objects.get(pk=user.pk)
    client = Client()
    client.force_login(user)
    response = client.get(_preview_change_url("mymodule"))
    assert response.status_code == 405


@pytest.mark.django_db
def test_preview_change_disable_shows_affected_modules():
    """POST with action=disable, registry has module that depends on target — affected_modules includes it."""
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    User = get_user_model()
    user = User.objects.create_user(username="preview_disable", password="testpass123")
    try:
        perm = Permission.objects.get(codename="change_module_state")
        user.user_permissions.add(perm)
    except Permission.DoesNotExist:
        pass
    user = User.objects.get(pk=user.pk)

    # Use slugs that are valid for Django's URL <slug:...> converter
    # ([-a-zA-Z0-9_]+ — no dots) and pass them directly in the inventory.
    # The _make_inventory_entry handles manifest creation gracefully.
    target = _make_inventory_entry(slug="targetmod")
    # dependent module lists targetmod in its deps
    dependent = _make_inventory_entry(slug="depmod", deps=["targetmod"])

    with _fake_registry_ctx([target, dependent]):
        client = Client()
        client.force_login(user)
        response = client.post(
            _preview_change_url("targetmod"),
            data=json.dumps({"action": "disable"}),
            content_type="application/json",
        )

    assert response.status_code == 200
    data = json.loads(response.content)
    assert "depmod" in data.get("affected_modules", [])


# --------------------------------------------------------------------------- #
# enable_module / disable_module tests                                         #
# --------------------------------------------------------------------------- #

@pytest.mark.django_db
def test_enable_module_requires_post():
    """GET to enable_module returns 405."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user = User.objects.create_user(username="enable_get", password="testpass123")
    client = Client()
    client.force_login(user)
    response = client.get(_enable_url("somemod"))
    assert response.status_code == 405


@pytest.mark.django_db
def test_enable_module_stores_override():
    """POST to enable_module creates ModuleEnabledOverride with enabled=True."""
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    from cauldron_module_tree.models import ModuleEnabledOverride
    User = get_user_model()
    user = User.objects.create_user(username="enable_post", password="testpass123")
    try:
        perm = Permission.objects.get(codename="change_module_state")
        user.user_permissions.add(perm)
    except Permission.DoesNotExist:
        pass
    user = User.objects.get(pk=user.pk)

    entry = _make_inventory_entry(slug="enabletarget")
    with _fake_registry_ctx([entry]):
        client = Client()
        client.force_login(user)
        response = client.post(
            _enable_url("enabletarget"),
            data=json.dumps({"reason": "testing enable"}),
            content_type="application/json",
        )

    assert response.status_code == 200
    override = ModuleEnabledOverride.objects.get(slug="enabletarget")
    assert override.enabled is True


@pytest.mark.django_db
def test_disable_module_stores_override():
    """POST to disable_module creates ModuleEnabledOverride with enabled=False."""
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    from cauldron_module_tree.models import ModuleEnabledOverride
    User = get_user_model()
    user = User.objects.create_user(username="disable_post", password="testpass123")
    try:
        perm = Permission.objects.get(codename="change_module_state")
        user.user_permissions.add(perm)
    except Permission.DoesNotExist:
        pass
    user = User.objects.get(pk=user.pk)

    entry = _make_inventory_entry(slug="disabletarget")
    with _fake_registry_ctx([entry]):
        client = Client()
        client.force_login(user)
        response = client.post(
            _disable_url("disabletarget"),
            data=json.dumps({"reason": "testing disable"}),
            content_type="application/json",
        )

    assert response.status_code == 200
    override = ModuleEnabledOverride.objects.get(slug="disabletarget")
    assert override.enabled is False


@pytest.mark.django_db
def test_enable_module_requires_permission():
    """User without change_module_state gets 403."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user = User.objects.create_user(username="enable_noperm", password="testpass123")
    client = Client()
    client.force_login(user)
    response = client.post(
        _enable_url("somemod"),
        data=json.dumps({}),
        content_type="application/json",
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_response_includes_restart_required():
    """Module has requires_restart=True — restart_required=True in response."""
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    User = get_user_model()
    user = User.objects.create_user(username="restart_user", password="testpass123")
    try:
        perm = Permission.objects.get(codename="change_module_state")
        user.user_permissions.add(perm)
    except Permission.DoesNotExist:
        pass
    user = User.objects.get(pk=user.pk)

    # The enable/disable views always respond with restart_required=True
    # (overrides always require a restart to take effect).
    entry = _make_inventory_entry(slug="restartmod", requires_restart=True)
    with _fake_registry_ctx([entry]):
        client = Client()
        client.force_login(user)
        response = client.post(
            _enable_url("restartmod"),
            data=json.dumps({}),
            content_type="application/json",
        )

    assert response.status_code == 200
    data = json.loads(response.content)
    assert data.get("restart_required") is True
