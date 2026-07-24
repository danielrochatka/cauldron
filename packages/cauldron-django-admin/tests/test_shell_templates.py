"""Tests for Cauldron Admin Shell templates."""
import pytest
from django.template.loader import get_template
from django.template import Context, RequestContext
from django.test import RequestFactory


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
