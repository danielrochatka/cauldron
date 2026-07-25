"""
Real-application route integration tests.

Uses cauldron_site.settings and cauldron_site.urls — the same configuration
that ./start deploys. These tests verify that every key URL returns the
expected status, template, and HTTP behaviour.
"""
import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse, NoReverseMatch


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def superuser(db):
    User = get_user_model()
    return User.objects.create_superuser(
        username="route_test_admin",
        email="admin@example.com",
        password="securepassword123",
    )


@pytest.fixture
def auth_client(superuser):
    c = Client()
    c.force_login(superuser)
    return c


# ---------------------------------------------------------------------------
# Anonymous-user behaviour
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_login_page_anonymous(client):
    """Cauldron login page returns 200 for anonymous users."""
    response = client.get("/accounts/login/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_dashboard_redirects_anonymous(client):
    """Anonymous user is redirected away from /cauldron/."""
    response = client.get("/cauldron/")
    assert response.status_code in (301, 302)
    location = response.headers.get("Location", "")
    assert "login" in location.lower() or "accounts" in location.lower()


@pytest.mark.django_db
def test_modules_redirects_anonymous(client):
    """Anonymous user is redirected away from /cauldron/modules/."""
    response = client.get("/cauldron/modules/")
    assert response.status_code in (301, 302)


@pytest.mark.django_db
def test_django_admin_redirects_anonymous(client):
    """Anonymous user is redirected from /admin/ to admin login."""
    response = client.get("/admin/")
    assert response.status_code in (301, 302)


@pytest.mark.django_db
def test_django_admin_login_anonymous(client):
    """Django admin login page returns 200 for anonymous users."""
    response = client.get("/admin/login/")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Authenticated superuser access
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_dashboard_superuser(auth_client):
    """Superuser can access the Cauldron dashboard without a server error."""
    response = auth_client.get("/cauldron/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_modules_superuser(auth_client):
    """Superuser can access /cauldron/modules/ without a server error."""
    response = auth_client.get("/cauldron/modules/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_django_admin_superuser(auth_client):
    """Superuser can access the Django admin index."""
    response = auth_client.get("/admin/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_django_admin_login_superuser(auth_client):
    """Authenticated superuser visiting /admin/login/ is redirected to index."""
    response = auth_client.get("/admin/login/")
    # Django redirects authenticated admin users away from the login page.
    assert response.status_code in (200, 301, 302)


# ---------------------------------------------------------------------------
# Template checks
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_dashboard_uses_cauldron_template(auth_client):
    """Dashboard renders via the Cauldron shell template."""
    response = auth_client.get("/cauldron/")
    template_names = [t.name for t in response.templates]
    assert "cauldron_admin/base.html" in template_names
    assert "cauldron_admin/dashboard.html" in template_names


@pytest.mark.django_db
def test_modules_uses_cauldron_template(auth_client):
    """Modules page renders via the Cauldron shell template."""
    response = auth_client.get("/cauldron/modules/")
    template_names = [t.name for t in response.templates]
    assert "cauldron_admin/base.html" in template_names


@pytest.mark.django_db
def test_admin_index_uses_bridge_template(auth_client):
    """Django admin index uses our base_site override."""
    response = auth_client.get("/admin/")
    template_names = [t.name for t in response.templates]
    assert "admin/base_site.html" in template_names


# ---------------------------------------------------------------------------
# No server errors on any page
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_no_500_on_key_routes(auth_client):
    """None of the key application routes return HTTP 500."""
    routes = [
        "/accounts/login/",
        "/cauldron/",
        "/cauldron/modules/",
        "/admin/",
        "/admin/login/",
    ]
    for route in routes:
        response = auth_client.get(route)
        assert response.status_code != 500, (
            f"Route {route} returned 500"
        )


# ---------------------------------------------------------------------------
# Navigation items — every safe GET destination must be reachable
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_registered_nav_items_reachable(auth_client, superuser):
    """Every navigation item registered by apps resolves and returns < 400."""
    from cauldron_django_admin.navigation import get_navigation_registry

    registry = get_navigation_registry()
    items = registry.get_items_for_user(superuser, None)

    errors = []
    for item in items:
        try:
            url = reverse(item.url_name)
        except NoReverseMatch:
            errors.append(f"{item.key}: could not reverse {item.url_name!r}")
            continue

        response = auth_client.get(url, follow=False)
        if response.status_code >= 400:
            errors.append(
                f"{item.key} ({url}): HTTP {response.status_code}"
            )

    assert not errors, "Navigation items have unreachable URLs:\n" + "\n".join(errors)


# ---------------------------------------------------------------------------
# URL reversal
# ---------------------------------------------------------------------------

def test_dashboard_url_reverses():
    assert reverse("cauldron:dashboard") == "/cauldron/"


def test_modules_url_reverses():
    assert reverse("cauldron:modules") == "/cauldron/modules/"


def test_admin_index_url_reverses():
    assert reverse("admin:index") == "/admin/"


# ---------------------------------------------------------------------------
# Static asset verification
# ---------------------------------------------------------------------------

def test_cauldron_admin_static_assets_exist():
    from django.contrib.staticfiles import finders

    assets = [
        "cauldron_admin/css/tokens.css",
        "cauldron_admin/css/reset.css",
        "cauldron_admin/css/base.css",
        "cauldron_admin/css/layout.css",
        "cauldron_admin/css/components.css",
        "cauldron_admin/css/forms.css",
        "cauldron_admin/css/tables.css",
        "cauldron_admin/css/utilities.css",
        "cauldron_admin/css/responsive.css",
        "cauldron_admin/css/django-admin-bridge.css",
        "cauldron_admin/js/shell.js",
    ]

    missing = [a for a in assets if not finders.find(a)]
    assert not missing, "Static assets not found:\n" + "\n".join(missing)
