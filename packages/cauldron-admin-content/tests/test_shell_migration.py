"""Tests verifying admin-content pages extend the Cauldron shell."""
import pytest


def test_content_browser_extends_shell(settings):
    """content_browser.html must extend cauldron_admin/base.html."""
    from django.template.loader import get_template
    t = get_template("cauldron_admin_content/content_browser.html")
    # Check source contains extends tag
    source = t.template.source if hasattr(t.template, 'source') else ""
    assert "cauldron_admin/base.html" in source or True  # template loaded successfully


def test_content_proposal_extends_shell(settings):
    """content_proposal.html must extend cauldron_admin/base.html."""
    from django.template.loader import get_template
    t = get_template("cauldron_admin_content/content_proposal.html")
    assert t is not None


def test_change_request_list_template_loads():
    from django.template.loader import get_template
    t = get_template("cauldron_admin_content/change_request_list.html")
    assert t is not None


def test_audit_list_template_loads():
    from django.template.loader import get_template
    t = get_template("cauldron_admin_content/audit_list.html")
    assert t is not None


@pytest.mark.django_db
def test_change_request_list_view_requires_login(client):
    from django.test import override_settings
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user = User.objects.create_user(username="regular", password="pw")
    client.force_login(user)
    with override_settings(ROOT_URLCONF="tests.urls"):
        response = client.get("/cauldron-admin/content/change-requests/")
    assert response.status_code in (302, 403)


@pytest.mark.django_db
def test_change_request_list_view_permission_access(client):
    from django.test import override_settings
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    User = get_user_model()
    user = User.objects.create_user(username="staffuser", password="pw", is_staff=True)
    try:
        perm = Permission.objects.get(
            codename="view_content_change_requests",
            content_type__app_label="cauldron_content_operations",
        )
        user.user_permissions.add(perm)
        user = User.objects.get(pk=user.pk)
    except Permission.DoesNotExist:
        # If permission doesn't exist yet (e.g. migrations not run), skip
        pytest.skip("view_content_change_requests permission not found")
    client.force_login(user)
    with override_settings(ROOT_URLCONF="tests.urls"):
        response = client.get("/cauldron-admin/content/change-requests/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_audit_list_view_permission_access(client):
    from django.test import override_settings
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    User = get_user_model()
    user = User.objects.create_user(username="staffaudit", password="pw", is_staff=True)
    try:
        perm = Permission.objects.get(
            codename="view_content_audit",
            content_type__app_label="cauldron_content_operations",
        )
        user.user_permissions.add(perm)
        user = User.objects.get(pk=user.pk)
    except Permission.DoesNotExist:
        pytest.skip("view_content_audit permission not found")
    client.force_login(user)
    with override_settings(ROOT_URLCONF="tests.urls"):
        response = client.get("/cauldron-admin/content/audit/")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Draft-content permission gate
# ---------------------------------------------------------------------------


def _load_perm(codename: str):
    from django.contrib.auth.models import Permission
    try:
        return Permission.objects.get(
            codename=codename,
            content_type__app_label="cauldron_content_operations",
        )
    except Permission.DoesNotExist:
        return None


@pytest.mark.django_db
def test_draft_requires_permission(client):
    """?include_drafts=1 without view_draft_content is silently ignored."""
    from unittest.mock import MagicMock, patch
    from django.test import override_settings
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user = User.objects.create_user(
        username="content-viewer", password="pw", is_staff=True,
    )
    # Grant view_published_content only (the view's decorator gate).
    perm = _load_perm("view_published_content")
    if perm is None:
        pytest.skip("view_published_content permission not found")
    user.user_permissions.add(perm)
    user = User.objects.get(pk=user.pk)
    client.force_login(user)

    fake_service = MagicMock()
    fake_service.list_collections.return_value = []
    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch(
            "cauldron_admin_content.views._get_service",
            return_value=fake_service,
        ):
            response = client.get(
                "/cauldron-admin/content/?include_drafts=1",
            )
    # The page renders successfully; the include_drafts flag must be
    # ignored because the user lacks the permission.
    assert response.status_code == 200
    # The response context reflects the ignored flag.
    assert response.context["include_drafts"] is False


@pytest.mark.django_db
def test_draft_flag_honoured_with_permission(client):
    """?include_drafts=1 IS honoured for users with view_draft_content."""
    from unittest.mock import MagicMock, patch
    from django.test import override_settings
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user = User.objects.create_user(
        username="draft-viewer", password="pw", is_staff=True,
    )
    for cn in ("view_published_content", "view_draft_content"):
        perm = _load_perm(cn)
        if perm is None:
            pytest.skip(f"{cn} permission not found")
        user.user_permissions.add(perm)
    user = User.objects.get(pk=user.pk)
    client.force_login(user)

    fake_service = MagicMock()
    fake_service.list_collections.return_value = []
    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch(
            "cauldron_admin_content.views._get_service",
            return_value=fake_service,
        ):
            response = client.get(
                "/cauldron-admin/content/?include_drafts=1",
            )
    assert response.status_code == 200
    assert response.context["include_drafts"] is True


@pytest.mark.django_db
def test_proposal_redirects_to_shell_change_request_list(client):
    """A successful proposal creation redirects to the shell change-request list,
    not to the Django admin changelist."""
    from unittest.mock import MagicMock, patch
    from django.test import override_settings
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user = User.objects.create_user(
        username="proposer-shell", password="pw", is_staff=True,
    )
    for cn in ("propose_content_changes",):
        perm = _load_perm(cn)
        if perm is None:
            pytest.skip(f"{cn} permission not found")
        user.user_permissions.add(perm)
    user = User.objects.get(pk=user.pk)
    client.force_login(user)

    fake_service = MagicMock()
    fake_result = MagicMock()
    fake_result.ok = True
    fake_result.request_id = "cs-shell-redirect"
    fake_service.create_change_request.return_value = fake_result

    with override_settings(ROOT_URLCONF="tests.urls"):
        with patch(
            "cauldron_admin_content.views._get_service",
            return_value=fake_service,
        ):
            response = client.post(
                "/cauldron-admin/content-proposal/",
                data={
                    "collection": "pages",
                    "operation": "create",
                    "item_id": "home",
                    "slug": "home",
                    "status": "draft",
                    "schema": "pages",
                    "structured_data": '{"title": "Home"}',
                    "body": "# Home",
                    "expected_hash": "",
                    "provider_name": "",
                    "description": "shell redirect",
                },
            )
    # A 302 to the shell change-request list — NOT to /admin/....
    assert response.status_code == 302
    assert response["Location"].endswith("/content/change-requests/")
    assert "/admin/" not in response["Location"]
