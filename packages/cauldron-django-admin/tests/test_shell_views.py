"""Tests for the Cauldron Admin Shell views."""
import pytest
from django.test import Client
from django.urls import reverse


@pytest.mark.django_db
def test_dashboard_requires_login():
    """Anonymous user is redirected to login."""
    client = Client()
    url = reverse("cauldron:dashboard")
    response = client.get(url)
    assert response.status_code in (301, 302)
    assert "login" in response["Location"] or "/auth/" in response["Location"]


@pytest.mark.django_db
def test_dashboard_authenticated():
    """Authenticated user can access the dashboard."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user = User.objects.create_user(username="testuser", password="testpass123")
    client = Client()
    client.force_login(user)
    url = reverse("cauldron:dashboard")
    response = client.get(url)
    assert response.status_code == 200


@pytest.mark.django_db
def test_modules_view_requires_login():
    """Anonymous user is redirected from modules view."""
    client = Client()
    url = reverse("cauldron:modules")
    response = client.get(url)
    assert response.status_code in (301, 302)


@pytest.mark.django_db
def test_modules_view_authenticated():
    """Authenticated user can access the modules view."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user = User.objects.create_user(username="moduser", password="testpass123")
    client = Client()
    client.force_login(user)
    url = reverse("cauldron:modules")
    response = client.get(url)
    assert response.status_code == 200


@pytest.mark.django_db
def test_dashboard_url_resolves():
    url = reverse("cauldron:dashboard")
    assert "/cauldron/" in url


@pytest.mark.django_db
def test_modules_url_resolves():
    url = reverse("cauldron:modules")
    assert "/cauldron/modules/" in url


def _fake_registry_ctx(inventory_entries, *, errors=(), discovery_errors=(), lifecycle_errors=()):
    """Context manager that patches cauldron.modules.registry with a fake."""
    import sys
    import types
    from contextlib import contextmanager
    from unittest.mock import MagicMock

    @contextmanager
    def _ctx():
        fake_registry = MagicMock()
        fake_registry.inventory.return_value = list(inventory_entries)
        fake_registry.capabilities.return_value = {}
        fake_registry.errors.return_value = list(errors)
        fake_registry.discovery_errors.return_value = list(discovery_errors)
        fake_registry.lifecycle_errors.return_value = list(lifecycle_errors)

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
    """Return a minimal valid inventory entry dict."""
    defaults = {
        "slug": "test.mod",
        "label": "Test",
        "version": "1.0.0",
        "state": "ready",
        "enabled": True,
        "active": True,
        "load_index": 0,
        "source_type": "package",
        "source": "test-package",
        "provides": [],
        "requires": [],
        "deps": [],
        "django_apps": [],
        "selected_providers": {},
        "errors": [],
    }
    defaults.update(kwargs)
    return defaults


@pytest.mark.django_db
def test_modules_page_shows_state_and_errors_inline():
    """A module with resolution errors must show its state and inline errors.

    The modules page row must carry the lifecycle ``state`` and the per-module
    ``errors`` list so operators can see failures without reading the diagnostics
    block separately.
    """
    from django.contrib.auth import get_user_model

    User = get_user_model()
    user = User.objects.create_user(username="modmoduser", password="pw123456")

    class _ResErr:
        module_slug = "cauldron.broken"

    broken_entry = _make_inventory_entry(
        slug="cauldron.broken",
        label="Broken",
        version="0.1.0",
        state="unavailable",
        active=False,
        load_index=None,
        errors=[{"kind": "missing_dependency", "message": "requires missing.dep"}],
    )

    with _fake_registry_ctx([broken_entry], errors=[_ResErr()]):
        client = Client()
        client.force_login(user)
        url = reverse("cauldron:modules")
        response = client.get(url)

    assert response.status_code == 200
    modules = response.context["modules"]
    assert len(modules) == 1
    assert modules[0]["slug"] == "cauldron.broken"
    assert modules[0]["state"] == "unavailable"
    assert modules[0]["errors"]
    # Diagnostics block also carries the error.
    errors = response.context["registry_errors"]
    assert any(
        e.get("module") == "cauldron.broken" for e in errors
    ), f"Expected diagnostic for cauldron.broken; got {errors}"


@pytest.mark.django_db
def test_modules_page_anonymous_redirects():
    """Anonymous request must be redirected, not served the page."""
    client = Client()
    url = reverse("cauldron:modules")
    response = client.get(url)
    assert response.status_code in (301, 302)
    assert "login" in response["Location"] or "/auth/" in response["Location"]


@pytest.mark.django_db
def test_modules_page_empty_inventory():
    """An empty registry must render without errors, not crash."""
    from django.contrib.auth import get_user_model

    User = get_user_model()
    user = User.objects.create_user(username="emptyuser", password="pw123456")

    with _fake_registry_ctx([]):
        client = Client()
        client.force_login(user)
        response = client.get(reverse("cauldron:modules"))

    assert response.status_code == 200
    assert response.context["modules"] == []


@pytest.mark.django_db
def test_modules_page_disabled_state_rendered():
    """A disabled module must have state='disabled' in context."""
    from django.contrib.auth import get_user_model

    User = get_user_model()
    user = User.objects.create_user(username="disableduser", password="pw123456")

    entry = _make_inventory_entry(
        slug="my.mod",
        state="disabled",
        enabled=False,
        active=False,
        load_index=None,
    )
    with _fake_registry_ctx([entry]):
        client = Client()
        client.force_login(user)
        response = client.get(reverse("cauldron:modules"))

    assert response.status_code == 200
    modules = response.context["modules"]
    assert modules[0]["state"] == "disabled"
    assert modules[0]["enabled"] is False


@pytest.mark.django_db
def test_modules_page_failed_state_has_lifecycle_error_in_errors():
    """A failed module must surface its lifecycle error inline."""
    from django.contrib.auth import get_user_model

    User = get_user_model()
    user = User.objects.create_user(username="faileduser", password="pw123456")

    entry = _make_inventory_entry(
        slug="my.mod",
        state="failed",
        active=False,
        errors=[{"kind": "lifecycle", "phase": "on_ready", "exception_type": "RuntimeError"}],
    )
    with _fake_registry_ctx([entry]):
        client = Client()
        client.force_login(user)
        response = client.get(reverse("cauldron:modules"))

    assert response.status_code == 200
    modules = response.context["modules"]
    assert modules[0]["state"] == "failed"
    lc_errors = [e for e in modules[0]["errors"] if e.get("kind") == "lifecycle"]
    assert lc_errors
    assert lc_errors[0]["exception_type"] == "RuntimeError"


@pytest.mark.django_db
def test_modules_page_source_type_and_source_passed_to_context():
    """source_type and source must be surfaced in the context for each module."""
    from django.contrib.auth import get_user_model

    User = get_user_model()
    user = User.objects.create_user(username="srcuser", password="pw123456")

    package_entry = _make_inventory_entry(
        slug="pkg.mod", source_type="package", source="my-package"
    )
    project_entry = _make_inventory_entry(
        slug="proj.mod", source_type="project", source="modules/proj_mod"
    )
    with _fake_registry_ctx([package_entry, project_entry]):
        client = Client()
        client.force_login(user)
        response = client.get(reverse("cauldron:modules"))

    assert response.status_code == 200
    by_slug = {m["slug"]: m for m in response.context["modules"]}
    assert by_slug["pkg.mod"]["source_type"] == "package"
    assert by_slug["pkg.mod"]["source"] == "my-package"
    assert by_slug["proj.mod"]["source_type"] == "project"
    assert by_slug["proj.mod"]["source"] == "modules/proj_mod"
