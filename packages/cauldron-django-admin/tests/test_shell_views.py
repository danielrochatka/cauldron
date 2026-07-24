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
