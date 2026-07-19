"""Tests for Django admin view access."""
import pytest
from django.test import Client
from django.urls import reverse


@pytest.mark.django_db
def test_admin_url_resolves():
    url = reverse("admin:index")
    assert "/admin/" in url


@pytest.mark.django_db
def test_anonymous_redirected_from_admin():
    client = Client()
    response = client.get("/admin/")
    assert response.status_code in (301, 302)


@pytest.mark.django_db
def test_superuser_can_access_admin_index():
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user = User.objects.create_superuser(
        username="superadmin", password="superpass123"
    )
    client = Client()
    client.force_login(user)
    response = client.get("/admin/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_non_staff_cannot_access_admin():
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user = User.objects.create_user(username="regular", password="regularpass")
    client = Client()
    client.force_login(user)
    response = client.get("/admin/")
    assert response.status_code in (301, 302, 403)


@pytest.mark.django_db
def test_user_admin_available():
    from django.contrib.auth import get_user_model
    User = get_user_model()
    admin_user = User.objects.create_superuser(
        username="useradmin", password="adminpass123"
    )
    client = Client()
    client.force_login(admin_user)
    # The auth.user admin page should exist.
    response = client.get("/admin/auth/user/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_group_admin_available():
    from django.contrib.auth import get_user_model
    User = get_user_model()
    admin_user = User.objects.create_superuser(
        username="groupadmin", password="adminpass123"
    )
    client = Client()
    client.force_login(admin_user)
    # The auth.group admin page should exist.
    response = client.get("/admin/auth/group/")
    assert response.status_code == 200
