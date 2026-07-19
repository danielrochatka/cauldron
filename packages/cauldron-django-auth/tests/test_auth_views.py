"""Tests for cauldron_django_auth URL routing and views."""
import pytest
from django.test import Client
from django.urls import reverse, resolve as url_resolve


def test_login_url_resolves():
    url = reverse("cauldron_auth:login")
    assert url == "/auth/login/"


def test_logout_url_resolves():
    url = reverse("cauldron_auth:logout")
    assert url == "/auth/logout/"


def test_password_change_url_resolves():
    url = reverse("cauldron_auth:password_change")
    assert url == "/auth/password-change/"


def test_password_change_done_url_resolves():
    url = reverse("cauldron_auth:password_change_done")
    assert url == "/auth/password-change/done/"


def test_password_reset_url_resolves():
    url = reverse("cauldron_auth:password_reset")
    assert url == "/auth/password-reset/"


def test_password_reset_done_url_resolves():
    url = reverse("cauldron_auth:password_reset_done")
    assert url == "/auth/password-reset/sent/"


def test_password_reset_complete_url_resolves():
    url = reverse("cauldron_auth:password_reset_complete")
    assert url == "/auth/password-reset/complete/"


@pytest.mark.django_db
def test_anonymous_redirected_to_login():
    """Anonymous access to password change redirects to login."""
    client = Client()
    response = client.get("/auth/password-change/")
    assert response.status_code in (301, 302)


@pytest.mark.django_db
def test_login_page_accessible_anonymously():
    client = Client()
    response = client.get("/auth/login/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_authenticated_can_access_password_change():
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user = User.objects.create_user(username="viewuser", password="viewpass123")
    client = Client()
    client.login(username="viewuser", password="viewpass123")
    response = client.get("/auth/password-change/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_password_reset_page_accessible_anonymously():
    client = Client()
    response = client.get("/auth/password-reset/")
    assert response.status_code == 200
