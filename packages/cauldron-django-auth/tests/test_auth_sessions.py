"""Tests for session management in cauldron.django.auth."""
import pytest
from django.test import Client


@pytest.mark.django_db
def test_login_creates_session():
    from django.contrib.auth import get_user_model
    User = get_user_model()
    User.objects.create_user(username="sessuser", password="sesspass")
    client = Client()
    client.login(username="sessuser", password="sesspass")
    # The session should now contain the _auth_user_id key.
    assert "_auth_user_id" in client.session


@pytest.mark.django_db
def test_logout_clears_session():
    from django.contrib.auth import get_user_model
    User = get_user_model()
    User.objects.create_user(username="logoutuser", password="logoutpass")
    client = Client()
    client.login(username="logoutuser", password="logoutpass")
    assert "_auth_user_id" in client.session
    client.logout()
    assert "_auth_user_id" not in client.session


@pytest.mark.django_db
def test_unauthenticated_client_has_no_user_in_session():
    client = Client()
    # Access any page to initialize session.
    client.get("/auth/login/")
    assert "_auth_user_id" not in client.session


@pytest.mark.django_db
def test_login_via_post():
    from django.contrib.auth import get_user_model
    User = get_user_model()
    User.objects.create_user(username="postuser", password="postpass123")
    client = Client()
    response = client.post("/auth/login/", {
        "username": "postuser",
        "password": "postpass123",
    })
    # A successful login redirects.
    assert response.status_code in (301, 302)
