"""Tests for the human SiteChangeSet publish workflow in admin-content.

These tests verify the human authoring path routes through the shared
:class:`SiteChangeSetService` when Site Astro is available, falls back to
the direct validate+apply path when it is absent, and enforces CSRF +
permission checks on the review view.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


pytestmark = pytest.mark.django_db


def _get_perm(codename):
    from django.contrib.auth.models import Permission
    return Permission.objects.get(
        codename=codename,
        content_type__app_label="cauldron_content_operations",
    )


def _make_user(username, perms=()):
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user = User.objects.create_user(username=username, password="pw")
    for perm_codename in perms:
        try:
            user.user_permissions.add(_get_perm(perm_codename))
        except Exception:
            pass
    return User.objects.get(pk=user.pk)


def _make_result(ok=True, request_id=None, request_version=1, error_msg=None):
    result = MagicMock()
    result.ok = ok
    result.request_id = request_id or str(uuid.uuid4())
    result.request_version = request_version
    if error_msg:
        result.error = MagicMock()
        result.error.message = error_msg
    else:
        result.error = None
    result.meta = {}
    return result


def _make_prepare_result(ok=True, change_set_id=None, status="draft_ready", message=""):
    """Duck-typed PrepareResult — SimpleNamespace so tests don't need
    cauldron_site_astro's Django app registry."""
    return SimpleNamespace(
        ok=ok,
        change_set_id=change_set_id or str(uuid.uuid4()),
        status=status,
        pages_built=1,
        preview_url=f"/preview/{change_set_id or 'x'}/",
        message=message or ("draft_ready" if ok else "failed"),
        build_log_tail="",
    )


def _make_publish_result(ok=True, change_set_id=None, status="published", message=""):
    return SimpleNamespace(
        ok=ok,
        change_set_id=change_set_id or str(uuid.uuid4()),
        status=status,
        pages_built=1,
        live_url="/",
        applied_request_ids=[],
        message=message or "ok",
        build_log_tail="",
    )


def _make_inspect_result(change_set_id, status="draft_ready"):
    return SimpleNamespace(
        ok=True,
        change_set_id=str(change_set_id),
        status=status,
        pages_built=1,
        preview_url=f"/preview/{change_set_id}/",
        created_at="2024-01-01T00:00:00",
        publish_build_result={},
        content_request_ids=["req-abc"],
        message="",
    )


def _post_create_publish(client, user, service_mock, publication_service_mock):
    from django.test import override_settings
    from django.core import signing

    item_id = str(uuid.uuid4())
    token = signing.dumps(
        {"key": str(uuid.uuid4()), "item_id": item_id},
        salt="cauldron.page.submit",
    )
    data = {
        "title": "About Us",
        "slug": "about-us",
        "navigation_title": "About",
        "summary": "",
        "template": "page",
        "seo_title": "",
        "meta_description": "",
        "canonical_url": "",
        "robots_index": True,
        "robots_follow": True,
        "social_title": "",
        "social_description": "",
        "social_image": "",
        "body": "# About",
        "intended_status": "published",
        "change_description": "About page",
        "submission_token": token,
        "action": "publish",
    }
    client.force_login(user)
    with patch("cauldron_admin_content.views._get_service", return_value=service_mock):
        with patch(
            "cauldron_admin_content.views._get_publication_service",
            return_value=publication_service_mock,
        ):
            with override_settings(ROOT_URLCONF="tests.urls"):
                return client.post("/cauldron-admin/content/pages/new/", data=data)


# ---------------------------------------------------------------------------
# Human create → SiteChangeSet preview → review page → publish
# ---------------------------------------------------------------------------


def test_page_create_publish_routes_through_site_change_set(client):
    """When Site Astro is available, publish creates a SiteChangeSet + redirects to review."""
    user = _make_user("cs_create_user", [
        "propose_content_changes",
        "validate_content_changes",
        "apply_content_changes",
        "view_published_content",
    ])
    req_id = str(uuid.uuid4())
    cs_id = str(uuid.uuid4())

    service_mock = MagicMock()
    service_mock.create_change_request.return_value = _make_result(ok=True, request_id=req_id)
    service_mock.validate_change_request.return_value = _make_result(ok=True, request_version=1)

    pub_service_mock = MagicMock()
    pub_service_mock.prepare.return_value = _make_prepare_result(ok=True, change_set_id=cs_id)

    response = _post_create_publish(client, user, service_mock, pub_service_mock)

    assert response.status_code == 302
    assert f"/change-sets/{cs_id}/" in response["Location"]
    # Validate happens before the SiteChangeSet is created (to fail fast).
    service_mock.validate_change_request.assert_called_once()
    pub_service_mock.prepare.assert_called_once()
    # The direct apply path must NOT have been used.
    service_mock.apply_change_request.assert_not_called()


def test_page_create_publish_falls_back_when_site_astro_missing(client):
    """When Site Astro is unavailable, publish uses the direct validate+apply path."""
    user = _make_user("fallback_user", [
        "propose_content_changes",
        "validate_content_changes",
        "apply_content_changes",
        "view_published_content",
    ])
    req_id = str(uuid.uuid4())

    service_mock = MagicMock()
    service_mock.create_change_request.return_value = _make_result(ok=True, request_id=req_id)
    service_mock.validate_change_request.return_value = _make_result(ok=True, request_version=1)
    service_mock.apply_change_request.return_value = _make_result(ok=True, request_id=req_id)

    response = _post_create_publish(client, user, service_mock, None)  # no pub_service

    assert response.status_code == 302
    service_mock.validate_change_request.assert_called_once()
    service_mock.apply_change_request.assert_called_once()


# ---------------------------------------------------------------------------
# ChangeSetReviewView
# ---------------------------------------------------------------------------


def _review_url(cs_id):
    return f"/cauldron-admin/content/change-sets/{cs_id}/"


def test_review_get_shows_page(client):
    from django.test import override_settings

    user = _make_user("cs_review_user", ["view_content_change_requests"])
    cs_id = str(uuid.uuid4())
    pub_service_mock = MagicMock()
    pub_service_mock.inspect.return_value = _make_inspect_result(cs_id)

    client.force_login(user)
    with patch(
        "cauldron_admin_content.views._get_publication_service",
        return_value=pub_service_mock,
    ):
        with override_settings(ROOT_URLCONF="tests.urls"):
            response = client.get(_review_url(cs_id))

    assert response.status_code == 200
    content = response.content.decode()
    assert cs_id in content
    pub_service_mock.inspect.assert_called_once_with(cs_id)


def test_review_get_returns_404_when_site_astro_missing(client):
    from django.test import override_settings

    user = _make_user("cs_no_astro", ["view_content_change_requests"])
    cs_id = str(uuid.uuid4())

    client.force_login(user)
    with patch(
        "cauldron_admin_content.views._get_publication_service",
        return_value=None,
    ):
        with override_settings(ROOT_URLCONF="tests.urls"):
            response = client.get(_review_url(cs_id))

    assert response.status_code == 404


def test_review_publish_requires_post_not_get(client):
    """GET must never publish — only POST triggers the publish action."""
    from django.test import override_settings

    user = _make_user("cs_get_user", [
        "view_content_change_requests",
        "apply_content_changes",
    ])
    cs_id = str(uuid.uuid4())

    pub_service_mock = MagicMock()
    pub_service_mock.inspect.return_value = _make_inspect_result(cs_id)

    client.force_login(user)
    with patch(
        "cauldron_admin_content.views._get_publication_service",
        return_value=pub_service_mock,
    ):
        with override_settings(ROOT_URLCONF="tests.urls"):
            response = client.get(_review_url(cs_id))

    assert response.status_code == 200
    pub_service_mock.publish.assert_not_called()


def test_review_post_publish_requires_apply_permission(client):
    from django.test import override_settings

    # User has view_content_change_requests (to pass dispatch) but NOT apply.
    user = _make_user("cs_no_apply", ["view_content_change_requests"])
    cs_id = str(uuid.uuid4())

    pub_service_mock = MagicMock()

    client.force_login(user)
    with patch(
        "cauldron_admin_content.views._get_publication_service",
        return_value=pub_service_mock,
    ):
        with override_settings(ROOT_URLCONF="tests.urls"):
            response = client.post(_review_url(cs_id), data={"action": "publish"})

    # Redirect back to the review page with an error message; publish not called.
    assert response.status_code == 302
    pub_service_mock.publish.assert_not_called()


def test_review_post_publish_calls_publication_service(client):
    from django.test import override_settings

    user = _make_user("cs_pub_user", [
        "view_content_change_requests",
        "apply_content_changes",
        "view_published_content",
    ])
    cs_id = str(uuid.uuid4())

    pub_service_mock = MagicMock()
    pub_service_mock.publish.return_value = _make_publish_result(
        ok=True, change_set_id=cs_id,
    )

    client.force_login(user)
    with patch(
        "cauldron_admin_content.views._get_publication_service",
        return_value=pub_service_mock,
    ):
        with override_settings(ROOT_URLCONF="tests.urls"):
            response = client.post(_review_url(cs_id), data={"action": "publish"})

    assert response.status_code == 302
    pub_service_mock.publish.assert_called_once()
    call_kwargs = pub_service_mock.publish.call_args.kwargs
    assert call_kwargs["actor"] == user
    assert call_kwargs["change_set_id"] == cs_id


def test_review_post_publish_failure_shows_retry(client):
    from django.test import override_settings

    user = _make_user("cs_fail_user", [
        "view_content_change_requests",
        "apply_content_changes",
    ])
    cs_id = str(uuid.uuid4())

    pub_service_mock = MagicMock()
    pub_service_mock.publish.return_value = _make_publish_result(
        ok=False, change_set_id=cs_id, status="publish_failed", message="build broke",
    )

    client.force_login(user)
    with patch(
        "cauldron_admin_content.views._get_publication_service",
        return_value=pub_service_mock,
    ):
        with override_settings(ROOT_URLCONF="tests.urls"):
            response = client.post(_review_url(cs_id), data={"action": "publish"})

    assert response.status_code == 302
    # Redirected back to the review page for a retry.
    assert f"/change-sets/{cs_id}/" in response["Location"]


def test_review_page_requires_login(client):
    from django.test import override_settings

    with override_settings(ROOT_URLCONF="tests.urls"):
        response = client.get(_review_url(str(uuid.uuid4())))

    assert response.status_code == 302
    assert "login" in response["Location"].lower()
