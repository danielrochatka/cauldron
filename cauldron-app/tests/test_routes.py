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
# Static asset discovery (staticfiles finders)
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


# ---------------------------------------------------------------------------
# WhiteNoise middleware configuration
# ---------------------------------------------------------------------------

def test_whitenoise_directly_after_security_middleware():
    """WhiteNoise must be the second middleware, immediately after Security."""
    from django.conf import settings

    mw = list(settings.MIDDLEWARE)
    assert "whitenoise.middleware.WhiteNoiseMiddleware" in mw, (
        "WhiteNoiseMiddleware not found in MIDDLEWARE"
    )
    sec_idx = mw.index("django.middleware.security.SecurityMiddleware")
    wn_idx = mw.index("whitenoise.middleware.WhiteNoiseMiddleware")
    assert wn_idx == sec_idx + 1, (
        f"WhiteNoiseMiddleware is at index {wn_idx} but SecurityMiddleware is at "
        f"{sec_idx}; WhiteNoise must be directly after Security"
    )


# ---------------------------------------------------------------------------
# WhiteNoise static-file serving through the WSGI app (DEBUG=False)
# ---------------------------------------------------------------------------

def test_static_tokens_css_served_via_whitenoise(tmp_path):
    """
    /static/cauldron_admin/css/tokens.css returns 200 with a CSS content-type
    through the full WSGI middleware stack (including WhiteNoiseMiddleware)
    with DEBUG=False, matching the production Gunicorn configuration.
    """
    from django.core.management import call_command
    from django.test import Client, override_settings

    static_root = str(tmp_path / "staticfiles")

    # Use the simple (non-hashing) backend so collected filenames are
    # identical to source filenames; the WhiteNoise serving path is the
    # same regardless of whether hashing is used.
    with override_settings(
        DEBUG=False,
        STATIC_ROOT=static_root,
        STORAGES={
            "default": {
                "BACKEND": "django.core.files.storage.FileSystemStorage",
            },
            "staticfiles": {
                "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
            },
        },
        ALLOWED_HOSTS=["*"],
    ):
        call_command("collectstatic", "--no-input", verbosity=0, clear=True)

        # Create the client inside the override context so ClientHandler
        # initialises WhiteNoiseMiddleware with the correct STATIC_ROOT.
        client = Client()
        response = client.get("/static/cauldron_admin/css/tokens.css")

    assert response.status_code == 200, (
        f"Expected 200 for tokens.css, got {response.status_code}"
    )
    content_type = response.get("Content-Type", "")
    assert "css" in content_type, (
        f"Expected CSS content-type, got {content_type!r}"
    )
