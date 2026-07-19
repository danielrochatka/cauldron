"""Tests for Django auth user model integration."""
import pytest
from django.contrib.auth import authenticate, get_user_model


@pytest.mark.django_db
def test_user_creation():
    User = get_user_model()
    user = User.objects.create_user(username="testuser", password="testpass123")
    assert user.pk is not None
    assert user.username == "testuser"


@pytest.mark.django_db
def test_password_is_hashed():
    User = get_user_model()
    user = User.objects.create_user(username="hashtest", password="plainpass")
    assert user.password != "plainpass"
    assert user.check_password("plainpass")


@pytest.mark.django_db
def test_authenticate_success():
    User = get_user_model()
    User.objects.create_user(username="authuser", password="goodpass")
    user = authenticate(username="authuser", password="goodpass")
    assert user is not None


@pytest.mark.django_db
def test_authenticate_failure_wrong_password():
    User = get_user_model()
    User.objects.create_user(username="authuser2", password="correctpass")
    user = authenticate(username="authuser2", password="wrongpass")
    assert user is None


@pytest.mark.django_db
def test_authenticate_failure_nonexistent_user():
    user = authenticate(username="nonexistent", password="anypass")
    assert user is None


@pytest.mark.django_db
def test_user_groups():
    from django.contrib.auth.models import Group
    User = get_user_model()
    user = User.objects.create_user(username="groupuser", password="pass")
    group = Group.objects.create(name="editors")
    user.groups.add(group)
    assert user.groups.filter(name="editors").exists()


@pytest.mark.django_db
def test_user_permissions():
    from django.contrib.auth.models import Permission
    from django.contrib.contenttypes.models import ContentType
    User = get_user_model()
    user = User.objects.create_user(username="permuser", password="pass")
    ct = ContentType.objects.get_for_model(User)
    perm = Permission.objects.filter(content_type=ct).first()
    if perm:
        user.user_permissions.add(perm)
        assert user.user_permissions.filter(pk=perm.pk).exists()


@pytest.mark.django_db
def test_superuser_creation():
    User = get_user_model()
    user = User.objects.create_superuser(username="admin", password="adminpass")
    assert user.is_superuser
    assert user.is_staff
