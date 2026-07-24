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


@pytest.mark.django_db
def test_modules_page_shows_error_for_failed_module():
    """A module reported in ``registry.errors()`` must appear on the
    modules page with status=``error`` / health=``degraded``, not just
    inside the diagnostics block. Operators scanning the module list
    should see the failure inline on the row.
    """
    import sys
    import types
    from unittest.mock import MagicMock
    from django.contrib.auth import get_user_model

    User = get_user_model()
    user = User.objects.create_user(username="modmoduser", password="pw123456")

    class _Err:
        module_slug = "cauldron.broken"

    fake_registry = MagicMock()
    fake_registry.graph_info.return_value = [{
        "slug": "cauldron.broken",
        "label": "Broken",
        "version": "0.1.0",
        "active": False,
        "provides": [],
        "requires": [],
        "deps": [],
        "django_apps": [],
        "load_index": None,
    }]
    fake_registry.capabilities.return_value = {}
    fake_registry.errors.return_value = [_Err()]
    fake_registry.discovery_errors.return_value = []
    fake_registry.lifecycle_errors.return_value = []

    fake_module = types.ModuleType("cauldron.modules.registry")
    fake_module.registry = fake_registry

    original_registry_mod = sys.modules.get("cauldron.modules.registry")
    sys.modules["cauldron.modules.registry"] = fake_module
    try:
        client = Client()
        client.force_login(user)
        url = reverse("cauldron:modules")
        response = client.get(url)
        assert response.status_code == 200
        modules = response.context["modules"]
        assert len(modules) == 1
        assert modules[0]["slug"] == "cauldron.broken"
        assert modules[0]["status"] == "error"
        assert modules[0]["health"] == "degraded"
        # Diagnostics block also carries the error.
        errors = response.context["registry_errors"]
        assert any(
            e.get("module") == "cauldron.broken" for e in errors
        ), f"Expected diagnostic for cauldron.broken; got {errors}"
    finally:
        if original_registry_mod is None:
            sys.modules.pop("cauldron.modules.registry", None)
        else:
            sys.modules["cauldron.modules.registry"] = original_registry_mod
