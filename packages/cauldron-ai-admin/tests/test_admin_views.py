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
    from django.utils import timezone as _tz
    fake_run = AdminAIRun.objects.create(
        actor=user,
        status="completed",
        provider_name="fake",
        user_request="hello",
        final_response="hi back",
        completed_at=_tz.now(),
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


# ---------------------------------------------------------------------------
# Regression tests: issue #62 — structured JSON errors and HTML response guard
# ---------------------------------------------------------------------------


def test_admin_ai_post_unauthenticated_returns_json_401():
    """Unauthenticated POST receives structured JSON 401, not an HTML login page.

    This is the root-cause fix: before, login_required redirected the
    request to an HTML login page and response.json() threw
    SyntaxError: Unexpected token '<'.  Now the dispatch() method
    detects the unauthenticated POST and returns a structured JSON error.
    """
    client = Client()
    from django.urls import reverse
    url = reverse("cauldron_ai_admin:ai-page")
    response = client.post(
        url, data=json.dumps({"request": "build Lantern & Loom site"}),
        content_type="application/json",
    )
    assert response.status_code == 401
    ct = response.get("Content-Type", "")
    assert "application/json" in ct, f"Expected JSON content-type, got: {ct!r}"
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "auth_required"
    assert "log in" in body["error"]["message"].lower()


def test_admin_ai_post_no_permission_returns_json_403():
    """POST from a user without use_admin_ai gets structured JSON 403."""
    user = _make_user(username="noperm-post", perms=())
    client = Client()
    client.force_login(user)
    from django.urls import reverse
    url = reverse("cauldron_ai_admin:ai-page")
    response = client.post(
        url, data=json.dumps({"request": "hello"}),
        content_type="application/json",
    )
    assert response.status_code == 403
    ct = response.get("Content-Type", "")
    assert "application/json" in ct, f"Expected JSON content-type, got: {ct!r}"
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "permission_denied"


def test_admin_ai_post_success_response_has_ok_true():
    """Successful POST response includes ok=true alongside the run fields."""
    user = _make_user(
        username="ok-true-user",
        perms=("cauldron_ai_admin.use_admin_ai",),
    )
    client = Client()
    client.force_login(user)
    from cauldron_ai_admin.models import AdminAIRun
    from django.utils import timezone as _tz
    fake_run = AdminAIRun.objects.create(
        actor=user,
        status="completed",
        provider_name="fake",
        user_request="hello",
        final_response="hi",
        completed_at=_tz.now(),
    )
    fake_service = MagicMock()
    fake_service.run.return_value = fake_run
    from django.urls import reverse
    url = reverse("cauldron_ai_admin:ai-page")
    with patch("cauldron_ai_admin.views._get_service", return_value=fake_service):
        response = client.post(
            url, data=json.dumps({"request": "hello"}),
            content_type="application/json",
        )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert "run_id" in body
    assert "status" in body


def test_admin_ai_post_bad_request_returns_structured_error():
    """Missing 'request' field returns structured JSON error with code=bad_request."""
    user = _make_user(
        username="bad-req",
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
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "bad_request"
    assert "message" in body["error"]


def test_admin_ai_post_service_unavailable_returns_structured_error():
    """Service unavailable returns structured JSON error with code=service_unavailable."""
    user = _make_user(
        username="svc-down2",
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
            url, data=json.dumps({"request": "hi"}),
            content_type="application/json",
        )
    assert response.status_code == 503
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "service_unavailable"


def test_admin_ai_post_run_exception_returns_structured_error():
    """Unexpected exception from service.run() returns structured JSON 500."""
    user = _make_user(
        username="run-exc",
        perms=("cauldron_ai_admin.use_admin_ai",),
    )
    client = Client()
    client.force_login(user)
    fake_service = MagicMock()
    fake_service.run.side_effect = RuntimeError("unexpected boom")
    from django.urls import reverse
    url = reverse("cauldron_ai_admin:ai-page")
    with patch("cauldron_ai_admin.views._get_service", return_value=fake_service):
        response = client.post(
            url, data=json.dumps({"request": "hi"}),
            content_type="application/json",
        )
    assert response.status_code == 500
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "server_error"
    # Must not leak the raw exception message to the client
    assert "unexpected boom" not in body["error"]["message"]
    assert "server logs" in body["error"]["message"].lower()


def test_admin_ai_post_worker_timeout_returns_json_503():
    """SystemExit from a gunicorn worker timeout returns structured JSON 503.

    sys.exit() raises SystemExit(BaseException), not Exception, so a plain
    except-Exception clause would miss it and leave the client with a dropped
    connection.  The view must catch SystemExit specifically and return a tidy
    503 so the frontend can display a human-readable message.
    """
    user = _make_user(
        username="timeout-user",
        perms=("cauldron_ai_admin.use_admin_ai",),
    )
    client = Client()
    client.force_login(user)
    fake_service = MagicMock()
    fake_service.run.side_effect = SystemExit(1)
    from django.urls import reverse
    url = reverse("cauldron_ai_admin:ai-page")
    with patch("cauldron_ai_admin.views._get_service", return_value=fake_service):
        response = client.post(
            url, data=json.dumps({"request": "hi"}),
            content_type="application/json",
        )
    assert response.status_code == 503
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "service_unavailable"
    assert "timed out" in body["error"]["message"].lower()


def test_admin_ai_post_permission_denied_from_service_returns_json_403():
    """PermissionDenied raised during service.run() returns structured JSON 403."""
    from django.core.exceptions import PermissionDenied
    user = _make_user(
        username="perm-denied-svc",
        perms=("cauldron_ai_admin.use_admin_ai",),
    )
    client = Client()
    client.force_login(user)
    fake_service = MagicMock()
    fake_service.run.side_effect = PermissionDenied("tool not allowed")
    from django.urls import reverse
    url = reverse("cauldron_ai_admin:ai-page")
    with patch("cauldron_ai_admin.views._get_service", return_value=fake_service):
        response = client.post(
            url, data=json.dumps({"request": "hi"}),
            content_type="application/json",
        )
    assert response.status_code == 403
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "permission_denied"


def test_admin_ai_post_wrong_content_type_returns_structured_error():
    """POST with form content type returns structured JSON 400."""
    user = _make_user(
        username="form-post",
        perms=("cauldron_ai_admin.use_admin_ai",),
    )
    client = Client()
    client.force_login(user)
    from django.urls import reverse
    url = reverse("cauldron_ai_admin:ai-page")
    response = client.post(url, data={"request": "hello"})  # form-encoded
    assert response.status_code == 400
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "bad_request"


def test_admin_ai_post_response_never_contains_html():
    """All POST error responses have Content-Type: application/json, never text/html."""
    from django.urls import reverse
    url = reverse("cauldron_ai_admin:ai-page")

    # Unauthenticated
    r1 = Client().post(url, data=json.dumps({"request": "x"}), content_type="application/json")
    assert "application/json" in r1.get("Content-Type", ""), "401 must be JSON"
    assert b"<html" not in r1.content

    # No permission
    noperm = _make_user(username="html-guard-noperm", perms=())
    c2 = Client()
    c2.force_login(noperm)
    r2 = c2.post(url, data=json.dumps({"request": "x"}), content_type="application/json")
    assert "application/json" in r2.get("Content-Type", ""), "403 must be JSON"
    assert b"<html" not in r2.content

    # Service unavailable
    user = _make_user(
        username="html-guard-svc",
        perms=("cauldron_ai_admin.use_admin_ai",),
    )
    c3 = Client()
    c3.force_login(user)
    with patch("cauldron_ai_admin.views._get_service", side_effect=RuntimeError("down")):
        r3 = c3.post(url, data=json.dumps({"request": "x"}), content_type="application/json")
    assert "application/json" in r3.get("Content-Type", ""), "503 must be JSON"
    assert b"<html" not in r3.content
