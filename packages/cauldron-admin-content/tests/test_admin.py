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
