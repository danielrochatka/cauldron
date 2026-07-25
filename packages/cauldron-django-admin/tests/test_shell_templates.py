"""Tests for Cauldron Admin Shell templates."""
import pytest
from django.template.loader import get_template
from django.template import Context, RequestContext
from django.test import RequestFactory, Client
from django.urls import reverse
from unittest.mock import patch


@pytest.mark.django_db
def test_base_template_loads():
    """Django can load the base shell template."""
    template = get_template("cauldron_admin/base.html")
    assert template is not None


@pytest.mark.django_db
def test_dashboard_template_extends_base():
    """dashboard.html extends cauldron_admin/base.html."""
    import os
    from django.apps import apps
    app = apps.get_app_config("cauldron_django_admin")
    template_dir = os.path.join(app.path, "templates")
    dashboard_path = os.path.join(template_dir, "cauldron_admin", "dashboard.html")
    with open(dashboard_path, encoding="utf-8") as f:
        content = f.read()
    assert 'extends "cauldron_admin/base.html"' in content


@pytest.mark.django_db
def test_modules_template_extends_base():
    """modules.html extends cauldron_admin/base.html."""
    import os
    from django.apps import apps
    app = apps.get_app_config("cauldron_django_admin")
    template_dir = os.path.join(app.path, "templates")
    modules_path = os.path.join(template_dir, "cauldron_admin", "modules.html")
    with open(modules_path, encoding="utf-8") as f:
        content = f.read()
    assert 'extends "cauldron_admin/base.html"' in content


@pytest.mark.django_db
def test_dashboard_template_renders(client):
    """Dashboard template renders without errors for authenticated user."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user = User.objects.create_user(username="tmpluser", password="pass")
    client.force_login(user)
    from django.urls import reverse
    response = client.get(reverse("cauldron:dashboard"))
    assert response.status_code == 200
    assert b"Dashboard" in response.content


@pytest.mark.django_db
def test_modules_template_renders(client):
    """Modules template renders without errors for authenticated user."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user = User.objects.create_user(username="tmpluser2", password="pass")
    client.force_login(user)
    from django.urls import reverse
    response = client.get(reverse("cauldron:modules"))
    assert response.status_code == 200
    assert b"Module Status" in response.content


@pytest.mark.django_db
def test_site_overrides_load_after_extra_css():
    """Site override CSS URLs are emitted AFTER the extra_css / extra_head
    blocks so overrides win the cascade against page-level customisations."""
    import os
    from django.apps import apps
    app = apps.get_app_config("cauldron_django_admin")
    template_dir = os.path.join(app.path, "templates")
    base_path = os.path.join(template_dir, "cauldron_admin", "base.html")
    with open(base_path, encoding="utf-8") as f:
        text = f.read()
    idx_extra_css = text.find("{% block extra_css")
    idx_extra_head = text.find("{% block extra_head")
    idx_overrides = text.find('get_override_css_urls "admin"')
    assert idx_extra_css > 0
    assert idx_extra_head > 0
    assert idx_overrides > 0
    # Overrides come AFTER both extra_css and extra_head.
    assert idx_overrides > idx_extra_css
    assert idx_overrides > idx_extra_head


# ---------------------------------------------------------------------------
# Dashboard card HTML structure
# ---------------------------------------------------------------------------

def _make_cards_with_and_without_url():
    """Return (card_with_url, card_without_url, card_with_status)."""
    from cauldron_django_admin.navigation import AdminDashboardCard
    return [
        AdminDashboardCard(
            key="alpha", label="Alpha", description="Has a link",
            url="/cauldron/", section="main", order=0
        ),
        AdminDashboardCard(
            key="beta", label="Beta", description="No link",
            url="", section="main", order=1
        ),
        AdminDashboardCard(
            key="gamma", label="Gamma", description="With status",
            url="/cauldron/modules/", section="main", order=2,
            status="active"
        ),
    ]


@pytest.mark.django_db
def test_dashboard_card_with_url_renders_as_anchor():
    """A card with a URL must render as <a class="cui-card cui-card--link">."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user = User.objects.create_user(username="cardanchor", password="pw")

    with patch("cauldron_django_admin.navigation.get_navigation_registry") as mock_reg:
        mock_reg.return_value.get_dashboard_cards.return_value = _make_cards_with_and_without_url()
        client = Client()
        client.force_login(user)
        response = client.get(reverse("cauldron:dashboard"))

    assert response.status_code == 200
    content = response.content.decode()
    assert 'href="/cauldron/"' in content
    assert "cui-card--link" in content


@pytest.mark.django_db
def test_dashboard_card_without_url_renders_as_div():
    """A card with no URL must render as a non-anchor element."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user = User.objects.create_user(username="carddiv", password="pw")

    with patch("cauldron_django_admin.navigation.get_navigation_registry") as mock_reg:
        mock_reg.return_value.get_dashboard_cards.return_value = _make_cards_with_and_without_url()
        client = Client()
        client.force_login(user)
        response = client.get(reverse("cauldron:dashboard"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Beta" in content
    # Cards with a URL (Alpha, Gamma) carry cui-card--link; the Beta card
    # (url="") must not.  Verify the rendered non-link card is a <div>.
    import re
    # Count cui-card--link occurrences: should equal number of cards with URLs (2).
    link_count = len(re.findall(r"cui-card--link", content))
    assert link_count == 2, (
        f"Expected 2 cui-card--link cards (Alpha + Gamma), found {link_count}"
    )
    # The plain card for Beta must appear as a <div class="cui-card">.
    assert '<div class="cui-card">' in content


@pytest.mark.django_db
def test_dashboard_card_has_no_view_button():
    """No separate 'View' button text must appear alongside card content."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user = User.objects.create_user(username="cardnoview", password="pw")

    with patch("cauldron_django_admin.navigation.get_navigation_registry") as mock_reg:
        mock_reg.return_value.get_dashboard_cards.return_value = _make_cards_with_and_without_url()
        client = Client()
        client.force_login(user)
        response = client.get(reverse("cauldron:dashboard"))

    assert response.status_code == 200
    content = response.content.decode()
    assert ">View<" not in content


@pytest.mark.django_db
def test_dashboard_card_status_indicator_renders():
    """Cards with a status must render the status dot and label."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user = User.objects.create_user(username="cardstatus", password="pw")

    with patch("cauldron_django_admin.navigation.get_navigation_registry") as mock_reg:
        mock_reg.return_value.get_dashboard_cards.return_value = _make_cards_with_and_without_url()
        client = Client()
        client.force_login(user)
        response = client.get(reverse("cauldron:dashboard"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "cui-card__status-dot--active" in content
    assert "Active" in content


# ---------------------------------------------------------------------------
# Template comment — must not render visibly
# ---------------------------------------------------------------------------

def test_base_template_comment_not_visible():
    """The template comment in base.html must not appear in rendered output."""
    import os
    from django.apps import apps
    app = apps.get_app_config("cauldron_django_admin")
    template_dir = os.path.join(app.path, "templates")
    base_path = os.path.join(template_dir, "cauldron_admin", "base.html")
    with open(base_path, encoding="utf-8") as f:
        text = f.read()
    # Must use Django block comment syntax, not bare {# multi-line #}.
    assert "{% comment %}" in text, "base.html must use {% comment %} for multi-line comments"
    # The old inline {# ... #} spanning multiple lines must be gone.
    assert "Site-owned overrides load LAST" not in text, (
        "Old multi-line {# #} comment text found; it may render as visible HTML"
    )


# ---------------------------------------------------------------------------
# Action tokens present in tokens.css
# ---------------------------------------------------------------------------

def test_action_tokens_defined_in_tokens_css():
    """All required action color tokens must be defined in tokens.css."""
    import os
    from django.apps import apps
    app = apps.get_app_config("cauldron_django_admin")
    tokens_path = os.path.join(
        app.path, "static", "cauldron_admin", "css", "tokens.css"
    )
    with open(tokens_path, encoding="utf-8") as f:
        css = f.read()

    required = [
        "--cui-color-action:",
        "--cui-color-action-hover:",
        "--cui-color-action-text:",
        "--cui-color-action-subtle:",
        "--cui-color-action-border:",
    ]
    missing = [t for t in required if t not in css]
    assert not missing, f"Action tokens missing from tokens.css: {missing}"

    # Primary brand tokens must remain unchanged.
    assert "--cui-color-primary: #365f63" in css
    assert "--cui-color-primary-hover: #294c50" in css
