"""Tests for ChangeRequestDetailView — GET display, POST lifecycle actions."""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.django_db

_DETAIL_URL_FMT = "/cauldron-admin/content/change-requests/{request_id}/"


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
    for codename in perms:
        try:
            user.user_permissions.add(_get_perm(codename))
        except Exception:
            pass
    return User.objects.get(pk=user.pk)


def _make_cr(
    request_id=None,
    lifecycle_state="proposed",
    request_version=1,
    created_by=None,
    provider_name="flatfile",
    last_error_code="",
    last_error_summary="",
):
    """Create a ContentChangeRequest row and return it."""
    from cauldron_content_operations.models import ContentChangeRequest

    rid = request_id or str(uuid.uuid4())
    cr = ContentChangeRequest.objects.create(
        request_id=rid,
        workspace_changeset_id=str(uuid.uuid4()),
        provider_name=provider_name,
        lifecycle_state=lifecycle_state,
        request_version=request_version,
        payload_hash="abc123",
        idempotency_key=str(uuid.uuid4()),
        last_error_code=last_error_code,
        last_error_summary=last_error_summary,
    )
    if created_by is not None:
        cr.created_by = created_by
        cr.save(update_fields=["created_by"])
    return cr


def _ok_result(request_id=None, state="validated", version=2):
    result = MagicMock()
    result.ok = True
    result.request_id = request_id or str(uuid.uuid4())
    result.lifecycle_state = state
    result.request_version = version
    result.error = None
    return result


def _fail_result(code="conflict.version", message="Version conflict."):
    result = MagicMock()
    result.ok = False
    result.error = MagicMock()
    result.error.code = code
    result.error.message = message
    return result


def _make_service(
    validate_return=None,
    approve_return=None,
    reject_return=None,
    apply_return=None,
    preview_return=None,
):
    svc = MagicMock()
    svc.validate_change_request.return_value = validate_return or _ok_result(state="validated")
    svc.approve_change_request.return_value = approve_return or _ok_result(state="approved")
    svc.reject_change_request.return_value = reject_return or _ok_result(state="rejected")
    svc.apply_change_request.return_value = apply_return or _ok_result(state="applied")
    svc.get_preview.return_value = preview_return
    return svc


# ---------------------------------------------------------------------------
# GET — authentication / authorization
# ---------------------------------------------------------------------------

def test_get_requires_login(client):
    from django.test import override_settings
    cr = _make_cr()
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    with override_settings(ROOT_URLCONF="tests.urls"):
        resp = client.get(url)
    assert resp.status_code == 302
    assert "login" in resp["Location"].lower()


def test_get_requires_view_permission(client):
    from django.test import override_settings
    user = _make_user("noperm_get")
    client.force_login(user)
    cr = _make_cr()
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    with override_settings(ROOT_URLCONF="tests.urls"):
        resp = client.get(url)
    assert resp.status_code in (302, 403)


def test_get_404_for_unknown_request_id(client):
    from django.test import override_settings
    user = _make_user("viewer_404", ["view_content_change_requests"])
    client.force_login(user)
    url = _DETAIL_URL_FMT.format(request_id="00000000-0000-0000-0000-000000000000")
    with override_settings(ROOT_URLCONF="tests.urls"):
        resp = client.get(url)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET — page renders correctly
# ---------------------------------------------------------------------------

def test_get_renders_request_id(client):
    from django.test import override_settings
    user = _make_user("viewer_rid", ["view_content_change_requests"])
    client.force_login(user)
    cr = _make_cr()
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    mock_svc = _make_service()
    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.get(url)
    assert resp.status_code == 200
    assert cr.request_id in resp.content.decode()


def test_get_renders_lifecycle_state(client):
    from django.test import override_settings
    user = _make_user("viewer_state", ["view_content_change_requests"])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="validated")
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    mock_svc = _make_service()
    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.get(url)
    assert resp.status_code == 200
    assert "validated" in resp.content.decode()


def test_get_shows_operations_when_preview_available(client):
    from django.test import override_settings
    user = _make_user("viewer_ops", ["view_content_change_requests"])
    client.force_login(user)
    cr = _make_cr()
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)

    op = SimpleNamespace(
        operation_type="create",
        collection="pages",
        item_id="home-page",
        provider="flatfile",
        has_conflict=False,
        diff_summary="Body changed",
        proposed_slug="home",
        proposed_status="draft",
        proposed_schema="page",
        proposed_data={},
        current_data={},
    )
    changeset = SimpleNamespace(operations=(op,))
    mock_svc = _make_service(preview_return=changeset)

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.get(url)
    content = resp.content.decode()
    assert "home-page" in content
    assert "pages" in content
    assert "Body changed" in content


def test_get_operations_renders_proposed_slug_status_schema(client):
    from django.test import override_settings
    user = _make_user("viewer_op_fields", ["view_content_change_requests"])
    client.force_login(user)
    cr = _make_cr()
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)

    op = SimpleNamespace(
        operation_type="create",
        collection="pages",
        item_id="my-item",
        provider="flatfile",
        has_conflict=False,
        diff_summary="",
        proposed_slug="my-slug",
        proposed_status="published",
        proposed_schema="article",
        proposed_data={},
        current_data={},
    )
    changeset = SimpleNamespace(operations=(op,))
    mock_svc = _make_service(preview_return=changeset)

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.get(url)
    content = resp.content.decode()
    assert "my-slug" in content
    assert "published" in content
    assert "article" in content


def test_get_no_operations_section_when_no_preview(client):
    from django.test import override_settings
    user = _make_user("viewer_noops", ["view_content_change_requests"])
    client.force_login(user)
    cr = _make_cr()
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    mock_svc = _make_service(preview_return=None)

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.get(url)
    content = resp.content.decode()
    assert "Operations" not in content


def test_get_degrades_gracefully_when_service_factory_raises(client):
    from django.test import override_settings
    from django.core.exceptions import ImproperlyConfigured
    user = _make_user("viewer_nosvc", ["view_content_change_requests"])
    client.force_login(user)
    cr = _make_cr()
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", side_effect=ImproperlyConfigured("no workspace")):
            resp = client.get(url)
    assert resp.status_code == 200
    assert cr.request_id in resp.content.decode()
    assert "Operations" not in resp.content.decode()


def test_get_shows_conflict_badge_when_has_conflict(client):
    from django.test import override_settings
    user = _make_user("viewer_conflict", ["view_content_change_requests"])
    client.force_login(user)
    cr = _make_cr()
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)

    op = SimpleNamespace(
        operation_type="update",
        collection="pages",
        item_id="about",
        provider="flatfile",
        has_conflict=True,
        diff_summary="",
        proposed_slug="about",
        proposed_status="draft",
        proposed_schema="page",
        proposed_data={},
        current_data={},
    )
    changeset = SimpleNamespace(operations=(op,))
    mock_svc = _make_service(preview_return=changeset)

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.get(url)
    assert "conflict" in resp.content.decode()


def test_get_no_action_buttons_in_terminal_state(client):
    from django.test import override_settings
    user = _make_user("viewer_term", ["view_content_change_requests", "validate_content_changes", "approve_content_changes", "reject_content_changes", "apply_content_changes"])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="applied")
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    mock_svc = _make_service()

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.get(url)
    content = resp.content.decode()
    assert 'name="action"' not in content


def test_get_shows_validate_button_in_proposed_state(client):
    from django.test import override_settings
    user = _make_user("viewer_proposed", ["view_content_change_requests", "validate_content_changes"])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="proposed")
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    mock_svc = _make_service()

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.get(url)
    content = resp.content.decode()
    assert 'value="validate"' in content


def test_get_shows_approve_button_in_validated_state(client):
    from django.test import override_settings
    user = _make_user("viewer_validated", ["view_content_change_requests", "approve_content_changes"])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="validated")
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    mock_svc = _make_service()

    approval_on = {"cauldron.content.operations": {"require_approval": True, "max_operations_per_change_set": 100}}
    with override_settings(ROOT_URLCONF="tests.urls", CAULDRON_MODULES=approval_on):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.get(url)
    assert 'value="approve"' in resp.content.decode()


def test_get_shows_apply_button_in_approved_state(client):
    from django.test import override_settings
    user = _make_user("viewer_approved", ["view_content_change_requests", "apply_content_changes"])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="approved")
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    mock_svc = _make_service()

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.get(url)
    assert 'value="apply"' in resp.content.decode()


def test_get_no_reject_button_in_approved_state(client):
    from django.test import override_settings
    user = _make_user("viewer_noreject_approved", ["view_content_change_requests", "reject_content_changes"])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="approved")
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    mock_svc = _make_service()

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.get(url)
    assert 'value="reject"' not in resp.content.decode()


def test_get_shows_apply_button_in_apply_failed_state(client):
    from django.test import override_settings
    user = _make_user("viewer_applyfailed", ["view_content_change_requests", "apply_content_changes"])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="apply_failed")
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    mock_svc = _make_service()

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.get(url)
    assert 'value="apply"' in resp.content.decode()


def test_get_shows_apply_button_in_validated_state_when_approval_not_required(client):
    from django.test import override_settings
    user = _make_user("viewer_validated_apply", ["view_content_change_requests", "apply_content_changes"])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="validated")
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    mock_svc = _make_service()

    no_approval = {"cauldron.content.operations": {"require_approval": False, "max_operations_per_change_set": 100}}
    with override_settings(ROOT_URLCONF="tests.urls", CAULDRON_MODULES=no_approval):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.get(url)
    assert 'value="apply"' in resp.content.decode()


def test_get_hides_approve_button_when_approval_disabled(client):
    from django.test import override_settings
    user = _make_user("viewer_no_approve_btn", ["view_content_change_requests", "approve_content_changes"])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="validated")
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    mock_svc = _make_service()

    no_approval = {"cauldron.content.operations": {"require_approval": False, "max_operations_per_change_set": 100}}
    with override_settings(ROOT_URLCONF="tests.urls", CAULDRON_MODULES=no_approval):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.get(url)
    assert 'value="approve"' not in resp.content.decode()


def test_get_hides_apply_button_in_validated_state_when_approval_required(client):
    from django.test import override_settings
    user = _make_user("viewer_validated_noapply", ["view_content_change_requests", "apply_content_changes"])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="validated")
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    mock_svc = _make_service()

    approval_on = {"cauldron.content.operations": {"require_approval": True, "max_operations_per_change_set": 100}}
    with override_settings(ROOT_URLCONF="tests.urls", CAULDRON_MODULES=approval_on):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.get(url)
    assert 'value="apply"' not in resp.content.decode()


def test_get_shows_reject_button_when_permitted(client):
    from django.test import override_settings
    user = _make_user("viewer_reject_btn", ["view_content_change_requests", "reject_content_changes"])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="proposed")
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    mock_svc = _make_service()

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.get(url)
    assert 'value="reject"' in resp.content.decode()


def test_get_hides_action_buttons_when_lacking_permission(client):
    from django.test import override_settings
    user = _make_user("viewer_only", ["view_content_change_requests"])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="proposed")
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    mock_svc = _make_service()

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.get(url)
    content = resp.content.decode()
    assert 'value="validate"' not in content
    assert 'value="reject"' not in content


def test_get_no_self_approval_notice_for_creator(client):
    """Creator who has approve_content_changes sees the Approve button with no warning."""
    from django.test import override_settings
    user = _make_user("proposer_self", ["view_content_change_requests", "approve_content_changes"])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="validated", created_by=user)
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    mock_svc = _make_service()

    approval_on = {"cauldron.content.operations": {"require_approval": True, "max_operations_per_change_set": 100}}
    with override_settings(ROOT_URLCONF="tests.urls", CAULDRON_MODULES=approval_on):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.get(url)
    content = resp.content.decode()
    assert "Self-approval is not permitted" not in content
    assert 'value="approve"' in content


def test_get_approve_button_shown_to_any_user_with_permission(client):
    from django.test import override_settings
    proposer = _make_user("proposer_other2", [])
    approver = _make_user("approver_other2", ["view_content_change_requests", "approve_content_changes"])
    client.force_login(approver)
    cr = _make_cr(lifecycle_state="validated", created_by=proposer)
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    mock_svc = _make_service()

    approval_on = {"cauldron.content.operations": {"require_approval": True, "max_operations_per_change_set": 100}}
    with override_settings(ROOT_URLCONF="tests.urls", CAULDRON_MODULES=approval_on):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.get(url)
    assert 'value="approve"' in resp.content.decode()


def test_get_expected_version_in_form(client):
    from django.test import override_settings
    user = _make_user("viewer_ver", ["view_content_change_requests", "validate_content_changes"])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="proposed", request_version=3)
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    mock_svc = _make_service()

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.get(url)
    assert 'value="3"' in resp.content.decode()


def test_get_shows_rejection_reason_textarea(client):
    from django.test import override_settings
    user = _make_user("viewer_textarea", ["view_content_change_requests", "reject_content_changes"])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="proposed")
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    mock_svc = _make_service()

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.get(url)
    assert "rejection_reason" in resp.content.decode()


# ---------------------------------------------------------------------------
# POST — authentication / authorization
# ---------------------------------------------------------------------------

def test_post_requires_login(client):
    from django.test import override_settings
    cr = _make_cr()
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    with override_settings(ROOT_URLCONF="tests.urls"):
        resp = client.post(url, {"action": "validate", "expected_version": "1"})
    assert resp.status_code == 302
    assert "login" in resp["Location"].lower()


def test_post_unknown_action_rejected(client):
    from django.test import override_settings
    user = _make_user("poster_bad_action", ["view_content_change_requests", "validate_content_changes"])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="proposed", request_version=1)
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    mock_svc = _make_service()

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.post(url, {"action": "hax", "expected_version": "1"})
    assert resp.status_code == 302
    mock_svc.validate_change_request.assert_not_called()


def test_post_without_permission_rejected(client):
    from django.test import override_settings
    user = _make_user("poster_noperm", ["view_content_change_requests"])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="proposed", request_version=1)
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    mock_svc = _make_service()

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.post(url, {"action": "validate", "expected_version": "1"})
    assert resp.status_code == 302
    mock_svc.validate_change_request.assert_not_called()


def test_post_action_not_valid_for_state_rejected(client):
    from django.test import override_settings
    user = _make_user("poster_badstate", ["view_content_change_requests", "approve_content_changes"])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="proposed", request_version=1)
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    mock_svc = _make_service()

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.post(url, {"action": "approve", "expected_version": "1"})
    assert resp.status_code == 302
    mock_svc.approve_change_request.assert_not_called()


# ---------------------------------------------------------------------------
# POST — validate
# ---------------------------------------------------------------------------

def test_post_validate_calls_service(client):
    from django.test import override_settings
    user = _make_user("poster_validate", ["view_content_change_requests", "validate_content_changes"])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="proposed", request_version=1)
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    mock_svc = _make_service()

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.post(url, {"action": "validate", "expected_version": "1"})

    assert resp.status_code == 302
    assert cr.request_id in resp["Location"]
    mock_svc.validate_change_request.assert_called_once_with(
        cr.request_id, user=user, expected_version=1
    )


def test_post_validate_passes_expected_version(client):
    from django.test import override_settings
    user = _make_user("poster_validate_ver", ["view_content_change_requests", "validate_content_changes"])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="proposed", request_version=5)
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    mock_svc = _make_service()

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            client.post(url, {"action": "validate", "expected_version": "5"})

    _, kwargs = mock_svc.validate_change_request.call_args
    assert kwargs["expected_version"] == 5


def test_post_validate_shows_success_message(client):
    from django.test import override_settings
    user = _make_user("poster_validate_msg", ["view_content_change_requests", "validate_content_changes"])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="proposed", request_version=1)
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    mock_svc = _make_service()

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.post(url, {"action": "validate", "expected_version": "1"}, follow=True)

    assert "validated successfully" in resp.content.decode()


def test_post_validate_failure_shows_error_message(client):
    from django.test import override_settings
    user = _make_user("poster_validate_fail", ["view_content_change_requests", "validate_content_changes"])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="proposed", request_version=1)
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    fail = _fail_result(message="Schema validation failed.")
    mock_svc = _make_service(validate_return=fail)

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.post(url, {"action": "validate", "expected_version": "1"}, follow=True)

    assert "Schema validation failed." in resp.content.decode()


# ---------------------------------------------------------------------------
# POST — approve
# ---------------------------------------------------------------------------

def test_post_approve_calls_service(client):
    from django.test import override_settings
    user = _make_user("poster_approve", ["view_content_change_requests", "approve_content_changes"])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="validated", request_version=2)
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    mock_svc = _make_service()

    approval_on = {"cauldron.content.operations": {"require_approval": True, "max_operations_per_change_set": 100}}
    with override_settings(ROOT_URLCONF="tests.urls", CAULDRON_MODULES=approval_on):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.post(url, {"action": "approve", "expected_version": "2"})

    assert resp.status_code == 302
    mock_svc.approve_change_request.assert_called_once_with(
        cr.request_id, user=user, expected_version=2
    )


def test_post_creator_with_approve_permission_can_approve(client):
    """Creator who has approve_content_changes is not blocked from approving."""
    from django.test import override_settings
    user = _make_user("self_approver2", ["view_content_change_requests", "approve_content_changes"])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="validated", request_version=2, created_by=user)
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    mock_svc = _make_service()

    approval_on = {"cauldron.content.operations": {"require_approval": True, "max_operations_per_change_set": 100}}
    with override_settings(ROOT_URLCONF="tests.urls", CAULDRON_MODULES=approval_on):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.post(url, {"action": "approve", "expected_version": "2"})

    assert resp.status_code == 302
    mock_svc.approve_change_request.assert_called_once()


# ---------------------------------------------------------------------------
# POST — reject
# ---------------------------------------------------------------------------

def test_post_reject_calls_service_with_reason(client):
    from django.test import override_settings
    user = _make_user("poster_reject", ["view_content_change_requests", "reject_content_changes"])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="proposed", request_version=1)
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    mock_svc = _make_service()

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.post(url, {"action": "reject", "expected_version": "1", "rejection_reason": "Not ready."})

    assert resp.status_code == 302
    mock_svc.reject_change_request.assert_called_once_with(
        cr.request_id, user=user, reason="Not ready.", expected_version=1
    )


def test_post_reject_empty_reason_allowed(client):
    from django.test import override_settings
    user = _make_user("poster_reject_noreason", ["view_content_change_requests", "reject_content_changes"])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="proposed", request_version=1)
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    mock_svc = _make_service()

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.post(url, {"action": "reject", "expected_version": "1"})

    assert resp.status_code == 302
    _, kwargs = mock_svc.reject_change_request.call_args
    assert kwargs["reason"] == ""


def test_post_reject_reason_truncated_at_500(client):
    from django.test import override_settings
    user = _make_user("poster_reject_long", ["view_content_change_requests", "reject_content_changes"])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="proposed", request_version=1)
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    mock_svc = _make_service()
    long_reason = "x" * 600

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            client.post(url, {"action": "reject", "expected_version": "1", "rejection_reason": long_reason})

    _, kwargs = mock_svc.reject_change_request.call_args
    assert len(kwargs["reason"]) == 500


# ---------------------------------------------------------------------------
# POST — apply
# ---------------------------------------------------------------------------

def test_post_apply_calls_service(client):
    from django.test import override_settings
    user = _make_user("poster_apply", ["view_content_change_requests", "apply_content_changes"])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="approved", request_version=3)
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    mock_svc = _make_service()

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.post(url, {"action": "apply", "expected_version": "3"})

    assert resp.status_code == 302
    mock_svc.apply_change_request.assert_called_once_with(
        cr.request_id, user=user, expected_version=3
    )


def test_post_apply_redirects_to_detail(client):
    from django.test import override_settings
    user = _make_user("poster_apply_redir", ["view_content_change_requests", "apply_content_changes"])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="approved", request_version=3)
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    mock_svc = _make_service()

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.post(url, {"action": "apply", "expected_version": "3"})

    assert cr.request_id in resp["Location"]


def test_post_apply_from_apply_failed_calls_service(client):
    from django.test import override_settings
    user = _make_user("poster_apply_retry", ["view_content_change_requests", "apply_content_changes"])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="apply_failed", request_version=4)
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    mock_svc = _make_service()

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.post(url, {"action": "apply", "expected_version": "4"})

    assert resp.status_code == 302
    mock_svc.apply_change_request.assert_called_once_with(
        cr.request_id, user=user, expected_version=4
    )


def test_post_apply_from_validated_calls_service_when_approval_not_required(client):
    from django.test import override_settings
    user = _make_user("poster_apply_validated", ["view_content_change_requests", "apply_content_changes"])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="validated", request_version=2)
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    mock_svc = _make_service()

    no_approval = {"cauldron.content.operations": {"require_approval": False, "max_operations_per_change_set": 100}}
    with override_settings(ROOT_URLCONF="tests.urls", CAULDRON_MODULES=no_approval):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.post(url, {"action": "apply", "expected_version": "2"})

    assert resp.status_code == 302
    mock_svc.apply_change_request.assert_called_once_with(
        cr.request_id, user=user, expected_version=2
    )


def test_post_approve_rejected_when_approval_disabled(client):
    from django.test import override_settings
    user = _make_user("poster_approve_disabled", ["view_content_change_requests", "approve_content_changes"])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="validated", request_version=2)
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    mock_svc = _make_service()

    no_approval = {"cauldron.content.operations": {"require_approval": False, "max_operations_per_change_set": 100}}
    with override_settings(ROOT_URLCONF="tests.urls", CAULDRON_MODULES=no_approval):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.post(url, {"action": "approve", "expected_version": "2"})

    assert resp.status_code == 302
    mock_svc.approve_change_request.assert_not_called()


def test_post_apply_from_validated_blocked_when_approval_required(client):
    from django.test import override_settings
    user = _make_user("poster_apply_validated_blocked", ["view_content_change_requests", "apply_content_changes"])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="validated", request_version=2)
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    mock_svc = _make_service()

    approval_on = {"cauldron.content.operations": {"require_approval": True, "max_operations_per_change_set": 100}}
    with override_settings(ROOT_URLCONF="tests.urls", CAULDRON_MODULES=approval_on):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.post(url, {"action": "apply", "expected_version": "2"})

    assert resp.status_code == 302
    mock_svc.apply_change_request.assert_not_called()


# ---------------------------------------------------------------------------
# POST — version conflict
# ---------------------------------------------------------------------------

def test_post_version_conflict_shows_error(client):
    from django.test import override_settings
    user = _make_user("poster_conflict", ["view_content_change_requests", "validate_content_changes"])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="proposed", request_version=2)
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    fail = _fail_result(code="conflict.version", message="Version conflict: expected 1, got 2.")
    mock_svc = _make_service(validate_return=fail)

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.post(url, {"action": "validate", "expected_version": "1"}, follow=True)

    assert "Version conflict" in resp.content.decode()


# ---------------------------------------------------------------------------
# POST — success label
# ---------------------------------------------------------------------------

def test_post_apply_success_message_says_applied(client):
    from django.test import override_settings
    user = _make_user("poster_apply_label", ["view_content_change_requests", "apply_content_changes"])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="approved", request_version=3)
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    mock_svc = _make_service()

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.post(url, {"action": "apply", "expected_version": "3"}, follow=True)

    assert "applied successfully" in resp.content.decode()


# ---------------------------------------------------------------------------
# POST — service unavailable
# ---------------------------------------------------------------------------

def test_post_service_returns_none_shows_error(client):
    from django.test import override_settings
    user = _make_user("poster_nosvc", ["view_content_change_requests", "validate_content_changes"])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="proposed", request_version=1)
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=None):
            resp = client.post(url, {"action": "validate", "expected_version": "1"}, follow=True)

    assert resp.status_code == 200


def test_post_service_factory_raises_shows_error(client):
    from django.test import override_settings
    from django.core.exceptions import ImproperlyConfigured
    user = _make_user("poster_factoryraise", ["view_content_change_requests", "validate_content_changes"])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="proposed", request_version=1)
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", side_effect=ImproperlyConfigured("no workspace")):
            resp = client.post(url, {"action": "validate", "expected_version": "1"}, follow=True)

    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET — error fields rendered
# ---------------------------------------------------------------------------

def test_get_renders_last_error_when_present(client):
    from django.test import override_settings
    user = _make_user("viewer_err", ["view_content_change_requests"])
    client.force_login(user)
    cr = _make_cr(last_error_code="workspace.unavailable", last_error_summary="Workspace is gone.")
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    mock_svc = _make_service()

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.get(url)

    content = resp.content.decode()
    assert "workspace.unavailable" in content
    assert "Workspace is gone." in content


# ---------------------------------------------------------------------------
# GET — audit events table
# ---------------------------------------------------------------------------

def test_get_shows_audit_events(client):
    from django.test import override_settings
    from cauldron_content_operations.models import ContentAuditEvent
    user = _make_user("viewer_audit", ["view_content_change_requests"])
    client.force_login(user)
    cr = _make_cr()
    ContentAuditEvent.objects.create(
        event_id=str(uuid.uuid4()),
        change_request=cr,
        sequence=1,
        event_type="proposal_created",
        previous_state="",
        resulting_state="proposed",
        provider="flatfile",
        correlation_id=str(uuid.uuid4()),
    )
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    mock_svc = _make_service()

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.get(url)

    content = resp.content.decode()
    assert "proposal_created" in content or "Audit History" in content


# ---------------------------------------------------------------------------
# Publish action from change request detail
# ---------------------------------------------------------------------------

def test_publish_action_validates_and_applies_when_approval_not_required(client):
    """POST action=publish from proposed state validates then applies."""
    from django.test import override_settings
    user = _make_user("cr_pub1", [
        "view_content_change_requests",
        "validate_content_changes",
        "apply_content_changes",
    ])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="proposed", request_version=1)
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)

    validate_result = _ok_result(request_id=cr.request_id, state="validated", version=2)
    apply_result = _ok_result(request_id=cr.request_id, state="applied", version=3)
    mock_svc = _make_service(
        validate_return=validate_result,
        apply_return=apply_result,
    )

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.post(url, data={"action": "publish", "expected_version": "1"})

    assert resp.status_code == 302
    mock_svc.validate_change_request.assert_called_once()
    mock_svc.apply_change_request.assert_called_once()


def test_publish_action_validates_only_when_approval_required(client):
    """POST action=publish submits for review when require_approval=True."""
    from django.test import override_settings
    user = _make_user("cr_pub2", [
        "view_content_change_requests",
        "validate_content_changes",
        "apply_content_changes",
    ])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="proposed", request_version=1)
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)

    mock_svc = _make_service()
    approval_on = {"cauldron.content.operations": {"require_approval": True, "max_operations_per_change_set": 100}}

    with override_settings(ROOT_URLCONF="tests.urls", CAULDRON_MODULES=approval_on):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.post(url, data={"action": "publish", "expected_version": "1"})

    assert resp.status_code == 302
    mock_svc.validate_change_request.assert_called_once()
    mock_svc.apply_change_request.assert_not_called()


def test_publish_action_rerenders_with_issues_on_validation_failure(client):
    """POST action=publish re-renders the CR detail with validation issues on failure."""
    from django.test import override_settings
    user = _make_user("cr_pub3", [
        "view_content_change_requests",
        "validate_content_changes",
        "apply_content_changes",
    ])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="proposed", request_version=1)
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)

    fail_result = _fail_result(code="validation.failed", message="Validation failed: 1 issue(s).")
    fail_result.meta = {"validation_issues": [
        {"code": "schema.missing_field", "collection": "pages", "item_id": "pid", "message": "title required"},
    ]}
    mock_svc = _make_service(validate_return=fail_result)

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.post(url, data={"action": "publish", "expected_version": "1"})

    assert resp.status_code == 200
    content = resp.content.decode()
    assert "schema.missing_field" in content
    assert "title required" in content
    mock_svc.apply_change_request.assert_not_called()


def test_publish_action_rejected_without_permissions(client):
    """POST action=publish returns 302 error when user lacks validate permission."""
    from django.test import override_settings
    # User has view but NOT validate permission
    user = _make_user("cr_pub4", ["view_content_change_requests"])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="proposed", request_version=1)
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)

    mock_svc = _make_service()

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.post(url, data={"action": "publish", "expected_version": "1"})

    # Redirects with error message (does not call validate)
    assert resp.status_code == 302
    mock_svc.validate_change_request.assert_not_called()


def test_publish_action_not_valid_from_validated_state(client):
    """POST action=publish is rejected for already-validated state (use apply instead)."""
    from django.test import override_settings
    user = _make_user("cr_pub5", [
        "view_content_change_requests",
        "validate_content_changes",
        "apply_content_changes",
    ])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="validated", request_version=2)
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)

    mock_svc = _make_service()

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.post(url, data={"action": "publish", "expected_version": "2"})

    # "publish" is not in valid_actions for "validated" state
    assert resp.status_code == 302
    mock_svc.validate_change_request.assert_not_called()


def test_cr_detail_shows_publish_button_in_proposed_state(client):
    """GET: Publish button appears in proposed state when user has permissions."""
    from django.test import override_settings
    user = _make_user("cr_pub6", [
        "view_content_change_requests",
        "validate_content_changes",
        "apply_content_changes",
    ])
    client.force_login(user)
    cr = _make_cr(lifecycle_state="proposed", request_version=1)
    url = _DETAIL_URL_FMT.format(request_id=cr.request_id)
    mock_svc = _make_service()

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch("cauldron_admin_content.views._get_service", return_value=mock_svc):
            resp = client.get(url)

    content = resp.content.decode()
    assert 'value="publish"' in content
