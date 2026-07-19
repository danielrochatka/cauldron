"""Tests for password reset tokens."""
import pytest
from django.contrib.auth.tokens import default_token_generator


@pytest.mark.django_db
def test_token_validates_for_user():
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user = User.objects.create_user(username="tokenuser", password="oldpass")
    token = default_token_generator.make_token(user)
    assert default_token_generator.check_token(user, token)


@pytest.mark.django_db
def test_token_invalid_after_password_change():
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user = User.objects.create_user(username="tokenuser2", password="oldpass")
    token = default_token_generator.make_token(user)
    # Change the password.
    user.set_password("newpass")
    user.save()
    # Token must be invalid now.
    assert not default_token_generator.check_token(user, token)


@pytest.mark.django_db
def test_invalid_token_fails_check():
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user = User.objects.create_user(username="tokenuser3", password="pass")
    assert not default_token_generator.check_token(user, "invalid-token")


@pytest.mark.django_db
def test_different_users_get_different_tokens():
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user1 = User.objects.create_user(username="token_u1", password="pass1")
    user2 = User.objects.create_user(username="token_u2", password="pass2")
    token1 = default_token_generator.make_token(user1)
    token2 = default_token_generator.make_token(user2)
    # Tokens should not be the same (highly unlikely if correct)
    assert not default_token_generator.check_token(user1, token2)
    assert not default_token_generator.check_token(user2, token1)
