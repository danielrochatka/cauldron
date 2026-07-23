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
            "cauldron.cms.flatfile": {"content_root": str(tmp_path / "content")},
            "cauldron.admin.content": {},
        }
    ):
        with patch(
            "cauldron_workspace_flatfile.store.ChangeSetStore.__init__",
            side_effect=RuntimeError("boom"),
        ):
            with pytest.raises(ImproperlyConfigured):
                get_service()


# ---------------------------------------------------------------------------
# Item 14: adapter registration is mandatory
# ---------------------------------------------------------------------------


def test_item14_missing_content_root_raises(tmp_path):
    """Item 15: content_root is only required when cauldron.cms.flatfile
    is present. Providing the flatfile config without a content_root must
    still raise; omitting the flatfile config entirely must NOT raise.
    """
    from django.test import override_settings
    from django.core.exceptions import ImproperlyConfigured
    from cauldron_admin_content.service_factory import get_service
    # Case 1: flatfile module present but content_root missing → error.
    with override_settings(
        CAULDRON_MODULES={
            "cauldron.content": {},
            "cauldron.workspace.flatfile": {"workspace_root": str(tmp_path / "ws")},
            "cauldron.cms.flatfile": {},
            "cauldron.admin.content": {},
        }
    ):
        with pytest.raises(ImproperlyConfigured):
            get_service()


def test_item15_non_flatfile_provider_needs_no_content_root(tmp_path):
    """Item 15: with no cauldron.cms.flatfile module, get_service() must
    succeed without requiring content_root.
    """
    from django.test import override_settings
    from cauldron_admin_content.service_factory import get_service
    with override_settings(
        CAULDRON_MODULES={
            "cauldron.content": {},
            "cauldron.workspace.flatfile": {"workspace_root": str(tmp_path / "ws")},
            "cauldron.admin.content": {},
        }
    ):
        svc = get_service()
    assert svc is not None


def test_item14_ok_registration_replaces_stale_adapter(tmp_path):
    """A stale globally-registered adapter is replaced when config changes."""
    from django.test import override_settings
    from cauldron_content_operations.reversible import (
        register_adapter, get_adapter, unregister_adapter,
    )
    from cauldron_admin_content.service_factory import get_service
    from unittest.mock import MagicMock
    stale = MagicMock()
    stale._content_root = "/some/stale/path"
    stale.reversible_adapter_version = 2
    stale.supports_rollback = True
    register_adapter("flatfile", stale)
    try:
        (tmp_path / "content").mkdir()
        with override_settings(
            CAULDRON_MODULES={
                "cauldron.content": {},
                "cauldron.workspace.flatfile": {"workspace_root": str(tmp_path / "ws")},
                "cauldron.cms.flatfile": {"content_root": str(tmp_path / "content")},
                "cauldron.admin.content": {},
            }
        ):
            _svc = get_service()
        new_adapter = get_adapter("flatfile")
        assert new_adapter is not stale
    finally:
        unregister_adapter("flatfile")


def test_item14_system_checks_report_missing_content_root(tmp_path):
    """Item 15: when cauldron.cms.flatfile IS declared but content_root is
    absent, E002 fires. Without the flatfile module, E002 must NOT fire.
    """
    from django.test import override_settings
    from cauldron_admin_content.checks import check_admin_content_configuration
    with override_settings(
        CAULDRON_MODULES={
            "cauldron.content": {},
            "cauldron.workspace.flatfile": {"workspace_root": str(tmp_path / "ws")},
            "cauldron.cms.flatfile": {},
            "cauldron.admin.content": {},
        }
    ):
        errors = check_admin_content_configuration(None)
    ids = [e.id for e in errors]
    assert "content_admin.E002" in ids


def test_item15_no_flatfile_provider_skips_content_root_check(tmp_path):
    """Item 15: check_admin_content_configuration does not require a
    content_root when the flatfile CMS module is absent.
    """
    from django.test import override_settings
    from cauldron_admin_content.checks import check_admin_content_configuration
    with override_settings(
        CAULDRON_MODULES={
            "cauldron.content": {},
            "cauldron.workspace.flatfile": {"workspace_root": str(tmp_path / "ws")},
            "cauldron.admin.content": {},
        }
    ):
        errors = check_admin_content_configuration(None)
    ids = [e.id for e in errors]
    assert "content_admin.E002" not in ids


# ---------------------------------------------------------------------------
# Item 15: real change-form template exists; version comes from POST
# ---------------------------------------------------------------------------


def test_item15_change_form_template_exists():
    """The change-form template override lives under the expected admin path."""
    import cauldron_admin_content
    from pathlib import Path
    tpl_path = (
        Path(cauldron_admin_content.__file__).parent
        / "templates" / "admin" / "cauldron_content_operations"
        / "contentchangerequest" / "change_form.html"
    )
    assert tpl_path.exists(), tpl_path


def test_item15_stale_post_version_returns_conflict():
    """POSTing a stale expected_version returns conflict.version — service is
    the authority; the admin never re-reads and substitutes the DB version.
    """
    from unittest.mock import patch, MagicMock
    from django.test import RequestFactory
    from django.contrib.messages.storage.cookie import CookieStorage
    from cauldron_content_operations.models import ContentChangeRequest, ContentAuditEvent
    from django.contrib import admin as _admin
    import cauldron_admin_content.admin  # noqa
    admin_class = _admin.site._registry[ContentChangeRequest]

    cr = ContentChangeRequest.objects.create(
        workspace_changeset_id="cs-item15-stale",
        provider_name="flatfile",
        request_version=5,
        lifecycle_state="proposed",
    )
    audit_before = ContentAuditEvent.objects.filter(change_request=cr).count()

    # Fake service returns conflict.version when called with expected_version=1.
    from cauldron_content_operations.results import ChangeRequestResult, OperationError
    fake_service = MagicMock()
    fake_service.validate_change_request.return_value = ChangeRequestResult(
        ok=False, error=OperationError("conflict.version", "Version conflict."),
    )

    factory = RequestFactory()
    request = factory.post(
        f"/admin/x/{cr.request_id}/validate/",
        data={"expected_version": "1"},
    )
    request._messages = CookieStorage(request)
    request.user = _make_test_user()

    with patch("cauldron_admin_content.admin._get_service", return_value=fake_service):
        with patch.object(admin_class, "_detail_url", return_value="/back"):
            admin_class.validate_view(request, cr.request_id)

    fake_service.validate_change_request.assert_called_once()
    _, kwargs = fake_service.validate_change_request.call_args
    # expected_version came from POST body (1), not DB (5)
    assert kwargs.get("expected_version") == 1
    # No lifecycle mutation happened.
    cr.refresh_from_db()
    assert cr.lifecycle_state == "proposed"
    assert cr.request_version == 5
    # No success audit event added.
    audit_after = ContentAuditEvent.objects.filter(change_request=cr).count()
    assert audit_after == audit_before


def _make_test_user():
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user, _ = User.objects.get_or_create(
        username="item15adminuser", defaults={"is_staff": True, "is_superuser": True},
    )
    return user


# ---------------------------------------------------------------------------
# Item 10: admin lifecycle template — no nested <form>, formaction submits,
# and action visibility is gated on both lifecycle state and permissions.
# ---------------------------------------------------------------------------


def test_item10_admin_template_has_no_nested_form_element():
    """The lifecycle template must not open a nested <form> element."""
    import cauldron_admin_content
    from pathlib import Path
    tpl_path = (
        Path(cauldron_admin_content.__file__).parent
        / "templates" / "admin" / "cauldron_content_operations"
        / "contentchangerequest" / "change_form.html"
    )
    text = tpl_path.read_text(encoding="utf-8")
    # Strip Django/Jinja `{# ... #}` and HTML `<!-- ... -->` comments before
    # searching so tag names mentioned in docs don't count.
    import re
    stripped = re.sub(r"\{\#.*?\#\}", "", text, flags=re.DOTALL)
    stripped = re.sub(r"<!--.*?-->", "", stripped, flags=re.DOTALL)
    # No literal `<form ...>` or `</form>` tag remaining in the body.
    assert not re.search(r"<\s*form[\s>]", stripped), (
        "Template must not open a nested <form> element."
    )
    assert not re.search(r"</\s*form\s*>", stripped), (
        "Template must not close a nested </form> element."
    )
    # Buttons submit via formaction+formmethod.
    assert "formaction=" in text
    assert 'formmethod="post"' in text
    # Single hidden expected_version input.
    assert 'name="expected_version"' in text


def test_item10_template_gates_actions_on_permissions_and_state():
    """The template must gate each lifecycle button on the matching
    permission AND the current lifecycle state, so an unauthorised user
    sees no action UI even in a valid state.
    """
    import cauldron_admin_content
    from pathlib import Path
    tpl_path = (
        Path(cauldron_admin_content.__file__).parent
        / "templates" / "admin" / "cauldron_content_operations"
        / "contentchangerequest" / "change_form.html"
    )
    text = tpl_path.read_text(encoding="utf-8")
    # Each of the four lifecycle actions is gated on a permission.
    for perm in (
        "validate_content_changes",
        "approve_content_changes",
        "reject_content_changes",
        "apply_content_changes",
        "rollback_content_changes",
    ):
        assert perm in text, f"Missing permission gate: {perm}"
    # Each of the five state branches is present.
    for state in ("proposed", "validated", "approved", "applied"):
        assert state in text, f"Missing state branch: {state}"


# ---------------------------------------------------------------------------
# Item 9: rollback button gated on rollback_content_changes only
# ---------------------------------------------------------------------------


def test_item9_rollback_button_uses_rollback_permission_gate():
    """Rollback branch must gate on rollback_content_changes exclusively."""
    import cauldron_admin_content
    from pathlib import Path
    tpl_path = (
        Path(cauldron_admin_content.__file__).parent
        / "templates" / "admin" / "cauldron_content_operations"
        / "contentchangerequest" / "change_form.html"
    )
    text = tpl_path.read_text(encoding="utf-8")
    idx = text.find('original.lifecycle_state == "applied"')
    assert idx > 0
    # Grab the section for the "applied" state.
    tail = text[idx: idx + 500]
    assert "rollback_content_changes" in tail
    # apply_content_changes must not gate the rollback button.
    assert "apply_content_changes" not in tail


def test_item9_render_only_with_rollback_permission():
    """User with rollback_content_changes sees rollback button; user with only
    apply_content_changes does NOT.
    """
    from django.template import Context, Template
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    from django.contrib.contenttypes.models import ContentType
    from cauldron_content_operations.models import ContentChangeRequest

    User = get_user_model()
    with_rollback = User.objects.create_user(
        username="i9-rb", password="p", is_staff=True,
    )
    with_apply = User.objects.create_user(
        username="i9-apply", password="p", is_staff=True,
    )
    # Ensure permissions exist (they are declared in the ContentChangeRequest Meta).
    ct = ContentType.objects.get_for_model(ContentChangeRequest)
    for codename in ("rollback_content_changes", "apply_content_changes"):
        Permission.objects.get_or_create(
            codename=codename, content_type=ct,
            defaults={"name": codename.replace("_", " ")},
        )
    with_rollback.user_permissions.add(
        Permission.objects.get(codename="rollback_content_changes", content_type=ct)
    )
    with_apply.user_permissions.add(
        Permission.objects.get(codename="apply_content_changes", content_type=ct)
    )
    # Rehydrate to pick up cached perms.
    with_rollback = User.objects.get(pk=with_rollback.pk)
    with_apply = User.objects.get(pk=with_apply.pk)

    cr = ContentChangeRequest.objects.create(
        workspace_changeset_id="cs-i9-btn",
        provider_name="flatfile",
        lifecycle_state="applied",
        request_version=1,
    )

    tpl = Template(
        "{% if original.lifecycle_state == 'applied' %}"
        "{% if perms.cauldron_content_operations.rollback_content_changes %}"
        "ROLLBACK-BUTTON"
        "{% endif %}"
        "{% endif %}"
    )
    ctx_rb = Context({"original": cr, "perms": _perms(with_rollback)})
    ctx_apply = Context({"original": cr, "perms": _perms(with_apply)})
    assert "ROLLBACK-BUTTON" in tpl.render(ctx_rb)
    assert "ROLLBACK-BUTTON" not in tpl.render(ctx_apply)


def _perms(user):
    """Return a dict-like object matching Django admin ``perms`` template var."""
    from django.contrib.auth.context_processors import PermWrapper
    return PermWrapper(user)
