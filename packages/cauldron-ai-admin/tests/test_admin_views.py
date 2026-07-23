"""Admin Django-admin registrations and AI page view tests."""
import json
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.test import Client, RequestFactory

pytestmark = pytest.mark.django_db


def _make_user(*, username="viewuser", password="pw", perms=(), is_staff=False):
    User = get_user_model()
    user, _ = User.objects.get_or_create(username=username)
    user.set_password(password)
    user.is_staff = is_staff
    user.save()
    for spec in perms:
        app_label, codename = spec.split(".", 1)
        try:
            perm = Permission.objects.get(
                codename=codename, content_type__app_label=app_label,
            )
        except Permission.DoesNotExist:
            continue
        user.user_permissions.add(perm)
    return User.objects.get(pk=user.pk)


# ---------------------------------------------------------------------------
# Django-admin registration is read-only
# ---------------------------------------------------------------------------


def test_admin_registrations_readonly():
    from django.contrib import admin as _admin
    import cauldron_ai_admin.admin  # noqa: F401
    from cauldron_ai_admin.models import AdminAIRun, AdminAIToolInvocation
    for model in (AdminAIRun, AdminAIToolInvocation):
        entry = _admin.site._registry[model]
        req = type("R", (), {"user": None})()
        assert not entry.has_add_permission(req)
        assert not entry.has_change_permission(req)
        assert not entry.has_delete_permission(req)


# ---------------------------------------------------------------------------
# Admin AI page view
# ---------------------------------------------------------------------------


def test_admin_ai_page_unauthenticated_redirects():
    client = Client()
    from django.urls import reverse
    url = reverse("cauldron_ai_admin:ai-page")
    response = client.get(url)
    # Login-required decorator redirects (302) or returns 403 depending on config.
    assert response.status_code in (302, 401, 403)


def test_admin_ai_page_no_permission_forbidden():
    user = _make_user(username="no-perm", perms=())
    client = Client()
    client.force_login(user)
    from django.urls import reverse
    url = reverse("cauldron_ai_admin:ai-page")
    response = client.get(url)
    assert response.status_code == 403


def test_admin_ai_page_renders_when_permitted():
    user = _make_user(
        username="with-ai",
        perms=("cauldron_ai_admin.use_admin_ai",),
    )
    client = Client()
    client.force_login(user)
    from django.urls import reverse
    url = reverse("cauldron_ai_admin:ai-page")
    response = client.get(url)
    assert response.status_code == 200
    assert b"Cauldron Admin AI" in response.content


def test_admin_ai_post_csrf_enforced():
    """A POST without a CSRF cookie/header must be blocked."""
    user = _make_user(
        username="csrf-user",
        perms=("cauldron_ai_admin.use_admin_ai",),
    )
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)
    from django.urls import reverse
    url = reverse("cauldron_ai_admin:ai-page")
    response = client.post(
        url, data=json.dumps({"request": "hi"}), content_type="application/json",
    )
    assert response.status_code == 403


def test_admin_ai_post_calls_service():
    """A valid POST calls AdminAIService.run() and returns a JSON summary."""
    user = _make_user(
        username="post-user",
        perms=("cauldron_ai_admin.use_admin_ai",),
    )
    client = Client()
    client.force_login(user)
    from cauldron_ai_admin.models import AdminAIRun
    fake_run = AdminAIRun.objects.create(
        actor=user,
        status="completed",
        provider_name="fake",
        user_request="hello",
        final_response="hi back",
    )
    fake_service = MagicMock()
    fake_service.run.return_value = fake_run
    from django.urls import reverse
    url = reverse("cauldron_ai_admin:ai-page")
    with patch("cauldron_ai_admin.views._get_service", return_value=fake_service):
        response = client.post(
            url,
            data=json.dumps({"request": "hello"}),
            content_type="application/json",
        )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["final_response"] == "hi back"
    assert body["run_id"] == str(fake_run.run_id)


def test_admin_ai_post_missing_request_returns_400():
    user = _make_user(
        username="badpost",
        perms=("cauldron_ai_admin.use_admin_ai",),
    )
    client = Client()
    client.force_login(user)
    from django.urls import reverse
    url = reverse("cauldron_ai_admin:ai-page")
    response = client.post(
        url, data=json.dumps({}), content_type="application/json",
    )
    assert response.status_code == 400


def test_admin_ai_post_service_unavailable_returns_503():
    user = _make_user(
        username="svc-down",
        perms=("cauldron_ai_admin.use_admin_ai",),
    )
    client = Client()
    client.force_login(user)
    from django.urls import reverse
    url = reverse("cauldron_ai_admin:ai-page")
    with patch(
        "cauldron_ai_admin.views._get_service",
        side_effect=RuntimeError("no provider"),
    ):
        response = client.post(
            url,
            data=json.dumps({"request": "hi"}),
            content_type="application/json",
        )
    assert response.status_code == 503


def test_admin_ai_page_shows_only_permitted_tools_in_hint():
    """The rendered page enumerates tools the user has permission to see."""
    from cauldron_ai_admin.builtin_tools import register_builtin_tools
    register_builtin_tools()
    user = _make_user(
        username="viewer",
        perms=("cauldron_ai_admin.use_admin_ai",),
    )
    client = Client()
    client.force_login(user)
    from django.urls import reverse
    url = reverse("cauldron_ai_admin:ai-page")
    response = client.get(url)
    assert response.status_code == 200
    # system.django_checks is gated on use_admin_ai only.
    assert b"system.django_checks" in response.content
    # content.list_collections requires view_published_content, which the
    # user doesn't have — it must not appear in the hint.
    assert b"content.list_collections" not in response.content
