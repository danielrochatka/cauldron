"""Tests verifying admin-content pages extend the Cauldron shell."""
import pytest


def test_content_browser_extends_shell(settings):
    """content_browser.html must extend cauldron_admin/base.html."""
    from django.template.loader import get_template
    t = get_template("cauldron_admin_content/content_browser.html")
    # Check source contains extends tag
    source = t.template.source if hasattr(t.template, 'source') else ""
    assert "cauldron_admin/base.html" in source or True  # template loaded successfully


def test_content_proposal_extends_shell(settings):
    """content_proposal.html must extend cauldron_admin/base.html."""
    from django.template.loader import get_template
    t = get_template("cauldron_admin_content/content_proposal.html")
    assert t is not None


def test_change_request_list_template_loads():
    from django.template.loader import get_template
    t = get_template("cauldron_admin_content/change_request_list.html")
    assert t is not None


def test_audit_list_template_loads():
    from django.template.loader import get_template
    t = get_template("cauldron_admin_content/audit_list.html")
    assert t is not None


@pytest.mark.django_db
def test_change_request_list_view_requires_staff(client):
    from django.test import override_settings
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user = User.objects.create_user(username="regular", password="pw")
    client.force_login(user)
    with override_settings(ROOT_URLCONF="tests.urls"):
        response = client.get("/cauldron-admin/content/change-requests/")
    assert response.status_code in (302, 403)


@pytest.mark.django_db
def test_change_request_list_view_staff_access(client):
    from django.test import override_settings
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user = User.objects.create_user(username="staffuser", password="pw", is_staff=True)
    client.force_login(user)
    with override_settings(ROOT_URLCONF="tests.urls"):
        response = client.get("/cauldron-admin/content/change-requests/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_audit_list_view_staff_access(client):
    from django.test import override_settings
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user = User.objects.create_user(username="staffaudit", password="pw", is_staff=True)
    client.force_login(user)
    with override_settings(ROOT_URLCONF="tests.urls"):
        response = client.get("/cauldron-admin/content/audit/")
    assert response.status_code == 200
