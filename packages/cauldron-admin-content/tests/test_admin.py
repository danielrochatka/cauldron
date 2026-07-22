"""Tests for Django Admin registrations."""
import pytest

pytestmark = pytest.mark.django_db


def test_admin_registrations_exist():
    from django.contrib import admin
    from cauldron_content_operations.models import ContentAuditEvent, ContentChangeRequest
    # Force admin registration
    import cauldron_admin_content.admin  # noqa
    assert ContentChangeRequest in admin.site._registry
    assert ContentAuditEvent in admin.site._registry


def test_audit_event_admin_readonly():
    from django.contrib import admin
    from cauldron_content_operations.models import ContentAuditEvent
    import cauldron_admin_content.admin  # noqa
    admin_class = admin.site._registry[ContentAuditEvent]
    fake_request = type("R", (), {"user": None})()
    assert not admin_class.has_add_permission(fake_request)
    assert not admin_class.has_change_permission(fake_request)
    assert not admin_class.has_delete_permission(fake_request)


def test_change_request_admin_no_add_delete():
    from django.contrib import admin
    from cauldron_content_operations.models import ContentChangeRequest
    import cauldron_admin_content.admin  # noqa
    admin_class = admin.site._registry[ContentChangeRequest]
    fake_request = type("R", (), {"user": None})()
    assert not admin_class.has_add_permission(fake_request)
    assert not admin_class.has_delete_permission(fake_request)


def test_module_manifest():
    from cauldron_admin_content.module import module
    assert module.slug == "cauldron.admin.content"
    assert "admin.content" in module.manifest.provides


def test_content_proposal_form_valid():
    from cauldron_admin_content.forms import ContentProposalForm
    form = ContentProposalForm(data={
        "collection": "pages",
        "operation": "create",
        "item_id": "home",
        "slug": "home",
        "status": "draft",
        "schema": "pages",
        "structured_data": '{"title": "Home"}',
        "body": "# Home\n\nWelcome.",
        "expected_hash": "",
        "provider_name": "",
        "description": "Create home page",
    })
    assert form.is_valid(), form.errors
    op = form.to_operation()
    assert op["kind"] == "create"
    assert op["data"] == {"title": "Home"}


def test_content_proposal_form_invalid_json():
    from cauldron_admin_content.forms import ContentProposalForm
    form = ContentProposalForm(data={
        "collection": "pages",
        "operation": "create",
        "item_id": "home",
        "structured_data": "not-json",
    })
    assert not form.is_valid()
    assert "structured_data" in form.errors


# ---------------------------------------------------------------------------
# Item 10: Admin optimistic concurrency — expected_version from POST body
# ---------------------------------------------------------------------------


def test_item10_missing_expected_version_rejects():
    """If the admin action POST is missing expected_version, error out."""
    from unittest.mock import patch
    from django.test import RequestFactory
    from django.contrib.messages.storage.cookie import CookieStorage
    from cauldron_content_operations.models import ContentChangeRequest
    from django.contrib import admin as _admin
    import cauldron_admin_content.admin  # noqa
    admin_class = _admin.site._registry[ContentChangeRequest]

    cr = ContentChangeRequest.objects.create(
        workspace_changeset_id="cs-item10-missing",
        provider_name="flatfile",
    )
    factory = RequestFactory()
    request = factory.post(f"/admin/x/{cr.request_id}/validate/", data={})
    request._messages = CookieStorage(request)

    with patch.object(admin_class, "_detail_url", return_value="/back"):
        ver, redirect = admin_class._load_expected_version(request, cr.request_id)
    assert ver is None
    assert redirect is not None


def test_item10_stale_expected_version_from_post_body():
    """Submitting expected_version from POST returns that value (not the DB version)."""
    from unittest.mock import patch
    from django.test import RequestFactory
    from django.contrib.messages.storage.cookie import CookieStorage
    from cauldron_content_operations.models import ContentChangeRequest
    from django.contrib import admin as _admin
    import cauldron_admin_content.admin  # noqa
    admin_class = _admin.site._registry[ContentChangeRequest]

    cr = ContentChangeRequest.objects.create(
        workspace_changeset_id="cs-item10-stale",
        provider_name="flatfile",
        request_version=5,
    )
    factory = RequestFactory()
    request = factory.post(
        f"/admin/x/{cr.request_id}/validate/",
        data={"expected_version": "2"},  # stale, but this is what should be passed
    )
    request._messages = CookieStorage(request)
    with patch.object(admin_class, "_detail_url", return_value="/back"):
        ver, redirect = admin_class._load_expected_version(request, cr.request_id)
    assert ver == 2  # From POST, not DB (which is 5)
    assert redirect is None


def test_item10_valid_expected_version_accepted():
    from unittest.mock import patch
    from django.test import RequestFactory
    from django.contrib.messages.storage.cookie import CookieStorage
    from cauldron_content_operations.models import ContentChangeRequest
    from django.contrib import admin as _admin
    import cauldron_admin_content.admin  # noqa
    admin_class = _admin.site._registry[ContentChangeRequest]

    cr = ContentChangeRequest.objects.create(
        workspace_changeset_id="cs-item10-valid",
        provider_name="flatfile",
    )
    factory = RequestFactory()
    request = factory.post(
        f"/admin/x/{cr.request_id}/validate/",
        data={"expected_version": "1"},
    )
    request._messages = CookieStorage(request)
    with patch.object(admin_class, "_detail_url", return_value="/back"):
        ver, redirect = admin_class._load_expected_version(request, cr.request_id)
    assert ver == 1
    assert redirect is None


# ---------------------------------------------------------------------------
# Item 11: Service factory fails closed on missing workspace
# ---------------------------------------------------------------------------


def test_item11_admin_service_factory_missing_workspace():
    from django.test import override_settings
    from django.core.exceptions import ImproperlyConfigured
    from cauldron_admin_content.service_factory import get_service
    with override_settings(CAULDRON_MODULES={"cauldron.content": {}, "cauldron.admin.content": {}}):
        with pytest.raises(ImproperlyConfigured):
            get_service()


def test_item11_admin_service_factory_bad_workspace(tmp_path):
    """Non-existent workspace_root or bad init raises ImproperlyConfigured."""
    from unittest.mock import patch
    from django.test import override_settings
    from django.core.exceptions import ImproperlyConfigured
    from cauldron_admin_content.service_factory import get_service
    with override_settings(
        CAULDRON_MODULES={
            "cauldron.content": {},
            "cauldron.workspace.flatfile": {"workspace_root": str(tmp_path / "ok")},
            "cauldron.admin.content": {},
        }
    ):
        with patch(
            "cauldron_workspace_flatfile.store.ChangeSetStore.__init__",
            side_effect=RuntimeError("boom"),
        ):
            with pytest.raises(ImproperlyConfigured):
                get_service()
