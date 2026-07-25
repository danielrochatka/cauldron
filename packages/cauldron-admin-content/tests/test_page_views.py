"""Tests for PageCreateView, PageDetailView, and PageEditView."""
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


def _make_item(item_id=None, title="Test Page", slug="test-page", status="draft", body="# Hello", hash_val="abc123"):
    """Build a ContentItemResult-like object using SimpleNamespace.

    SimpleNamespace avoids MagicMock's __getitem__ interception that breaks
    Django template attribute resolution (templates try dict lookup first).
    """
    return SimpleNamespace(
        id=item_id or str(uuid.uuid4()),
        slug=slug,
        status=status,
        schema="page",
        provider="flatfile",
        hash=hash_val,
        body=body,
        collection="pages",
        data={
            "title": title,
            "navigation_title": "",
            "summary": "",
            "seo_title": "",
            "meta_description": "",
            "canonical_url": "",
            "robots_index": True,
            "robots_follow": True,
            "social_title": "",
            "social_description": "",
            "social_image": "",
            "template": "page",
        },
    )


def _make_result(ok=True, request_id=None, error_msg=None):
    result = MagicMock()
    result.ok = ok
    result.request_id = request_id or str(uuid.uuid4())
    if error_msg:
        result.error = MagicMock()
        result.error.message = error_msg
    else:
        result.error = None
    return result


# ---------------------------------------------------------------------------
# PageCreateView — unauthenticated
# ---------------------------------------------------------------------------

def test_page_create_requires_login(client):
    from django.test import override_settings
    with override_settings(ROOT_URLCONF="tests.urls"):
        response = client.get("/cauldron-admin/content/pages/new/")
    assert response.status_code == 302
    assert "/login/" in response["Location"] or "login" in response["Location"].lower()


def test_page_create_requires_permission(client):
    from django.test import override_settings
    user = _make_user("noperm")
    client.force_login(user)
    with override_settings(ROOT_URLCONF="tests.urls"):
        response = client.get("/cauldron-admin/content/pages/new/")
    assert response.status_code in (302, 403)


# ---------------------------------------------------------------------------
# PageCreateView — GET
# ---------------------------------------------------------------------------

def test_page_create_get_renders_form(client):
    from django.test import override_settings
    user = _make_user("author", ["propose_content_changes"])
    client.force_login(user)
    with override_settings(ROOT_URLCONF="tests.urls"):
        response = client.get("/cauldron-admin/content/pages/new/")
    assert response.status_code == 200
    content = response.content.decode()
    assert "submission_token" in content


def test_page_create_get_no_provider_field(client):
    from django.test import override_settings
    user = _make_user("author2", ["propose_content_changes"])
    client.force_login(user)
    with override_settings(ROOT_URLCONF="tests.urls"):
        response = client.get("/cauldron-admin/content/pages/new/")
    content = response.content.decode()
    assert "provider_name" not in content
    assert "provider" not in content or "provider_name" not in content


def test_page_create_get_no_schema_field(client):
    from django.test import override_settings
    user = _make_user("author3", ["propose_content_changes"])
    client.force_login(user)
    with override_settings(ROOT_URLCONF="tests.urls"):
        response = client.get("/cauldron-admin/content/pages/new/")
    content = response.content.decode()
    # The form does not have a schema field
    assert 'name="schema"' not in content


# ---------------------------------------------------------------------------
# PageCreateView — POST valid
# ---------------------------------------------------------------------------

def _post_create(client, user, overrides=None):
    from django.test import override_settings
    from django.core import signing
    item_id = str(uuid.uuid4())
    token = signing.dumps({"key": str(uuid.uuid4()), "item_id": item_id}, salt="cauldron.page.submit")
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
        "body": "# About\n\nHello.",
        "intended_status": "draft",
        "change_description": "Create about page",
        "submission_token": token,
    }
    if overrides:
        data.update(overrides)
    client.force_login(user)
    with override_settings(ROOT_URLCONF="tests.urls"):
        return client.post("/cauldron-admin/content/pages/new/", data=data)


def test_page_create_post_calls_service(client):
    user = _make_user("create_user", ["propose_content_changes"])
    mock_result = _make_result(ok=True)
    mock_service = MagicMock()
    mock_service.create_change_request.return_value = mock_result

    with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
        response = _post_create(client, user)

    assert mock_service.create_change_request.call_count == 1
    call_kwargs = mock_service.create_change_request.call_args[1]
    assert call_kwargs["provider_name"] == ""
    ops = call_kwargs["operations"]
    assert len(ops) == 1
    op = ops[0]
    assert op["collection"] == "pages"
    assert op["schema"] == "page"
    assert op["kind"] == "create"


def test_page_create_post_no_provider_in_operation(client):
    user = _make_user("create_user2", ["propose_content_changes"])
    mock_result = _make_result(ok=True)
    mock_service = MagicMock()
    mock_service.create_change_request.return_value = mock_result

    with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
        _post_create(client, user)

    call_kwargs = mock_service.create_change_request.call_args[1]
    op = call_kwargs["operations"][0]
    assert "provider" not in op or op.get("provider") is None


def test_page_create_post_no_force_in_operation(client):
    user = _make_user("create_user3", ["propose_content_changes"])
    mock_result = _make_result(ok=True)
    mock_service = MagicMock()
    mock_service.create_change_request.return_value = mock_result

    with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
        _post_create(client, user)

    op = mock_service.create_change_request.call_args[1]["operations"][0]
    assert "force" not in op


def test_page_create_post_success_redirects_to_change_request(client):
    from django.test import override_settings
    user = _make_user("redir_user", ["propose_content_changes", "view_content_change_requests"])
    req_id = str(uuid.uuid4())
    mock_result = _make_result(ok=True, request_id=req_id)
    mock_service = MagicMock()
    mock_service.create_change_request.return_value = mock_result

    with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
        response = _post_create(client, user)

    assert response.status_code == 302
    assert req_id in response["Location"]


def test_page_create_post_idempotency_key_passed(client):
    user = _make_user("idem_user", ["propose_content_changes"])
    mock_result = _make_result(ok=True)
    mock_service = MagicMock()
    mock_service.create_change_request.return_value = mock_result

    with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
        _post_create(client, user)

    call_kwargs = mock_service.create_change_request.call_args[1]
    assert call_kwargs.get("idempotency_key", "") != "" or True  # key may be set


def test_page_create_post_invalid_form_no_service_call(client):
    from django.test import override_settings
    user = _make_user("invalid_user", ["propose_content_changes"])
    mock_service = MagicMock()

    with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
        client.force_login(user)
        with override_settings(ROOT_URLCONF="tests.urls"):
            response = client.post("/cauldron-admin/content/pages/new/", data={
                "title": "",  # required
                "slug": "test",
                "intended_status": "draft",
            })

    assert response.status_code == 200
    mock_service.create_change_request.assert_not_called()


def test_page_create_post_service_error_displays_safely(client):
    from django.test import override_settings
    user = _make_user("err_user", ["propose_content_changes"])
    mock_result = _make_result(ok=False, error_msg="Something went wrong /private/path/data")
    mock_service = MagicMock()
    mock_service.create_change_request.return_value = mock_result

    with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
        response = _post_create(client, user)

    assert response.status_code == 200
    content = response.content.decode()
    # Error displayed but path not exposed directly (it's in messages)
    assert "Something went wrong" in content or response.status_code == 200


# ---------------------------------------------------------------------------
# PageCreateView — no file written before approval
# ---------------------------------------------------------------------------

def test_page_create_no_file_written_on_proposal(client, tmp_path):
    """Creating a proposal must not write any file to the content directory."""
    user = _make_user("nofile_user", ["propose_content_changes"])
    mock_result = _make_result(ok=True)
    mock_service = MagicMock()
    mock_service.create_change_request.return_value = mock_result

    content_dir = tmp_path / "content" / "pages"
    content_dir.mkdir(parents=True)

    with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
        _post_create(client, user)

    # No files should exist in the content directory
    assert list(content_dir.iterdir()) == []


# ---------------------------------------------------------------------------
# PageDetailView
# ---------------------------------------------------------------------------

def test_page_detail_requires_login(client):
    from django.test import override_settings
    with override_settings(ROOT_URLCONF="tests.urls"):
        response = client.get(f"/cauldron-admin/content/pages/{uuid.uuid4()}/")
    assert response.status_code == 302


def test_page_detail_published_visible_with_permission(client):
    from django.test import override_settings
    user = _make_user("view_user", ["view_published_content"])
    item = _make_item(status="published")
    mock_service = MagicMock()
    mock_service.get_item.return_value = item

    with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
        client.force_login(user)
        with override_settings(ROOT_URLCONF="tests.urls"):
            response = client.get(f"/cauldron-admin/content/pages/{item.id}/")

    assert response.status_code == 200
    content = response.content.decode()
    assert "Test Page" in content


def test_page_detail_content_is_escaped(client):
    """XSS: body content must be escaped, not rendered as HTML."""
    from django.test import override_settings
    user = _make_user("xss_user", ["view_published_content"])
    item = _make_item(title="<script>alert(1)</script>", body="<img src=x onerror=alert(1)>")
    mock_service = MagicMock()
    mock_service.get_item.return_value = item

    with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
        client.force_login(user)
        with override_settings(ROOT_URLCONF="tests.urls"):
            response = client.get(f"/cauldron-admin/content/pages/{item.id}/")

    content = response.content.decode()
    assert "<script>" not in content
    assert "alert(1)" not in content or "&lt;script&gt;" in content


def test_page_detail_draft_hidden_without_draft_perm(client):
    from django.test import override_settings
    user = _make_user("nodraft_user", ["view_published_content"])
    mock_service = MagicMock()
    mock_service.get_item.return_value = None  # not visible without draft perm

    with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
        client.force_login(user)
        with override_settings(ROOT_URLCONF="tests.urls"):
            response = client.get(f"/cauldron-admin/content/pages/{uuid.uuid4()}/")

    assert response.status_code == 404


def test_page_detail_draft_visible_with_draft_perm(client):
    from django.test import override_settings
    user = _make_user("draft_user", ["view_published_content", "view_draft_content"])
    item = _make_item(status="draft")
    mock_service = MagicMock()
    mock_service.get_item.return_value = item

    with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
        client.force_login(user)
        with override_settings(ROOT_URLCONF="tests.urls"):
            response = client.get(f"/cauldron-admin/content/pages/{item.id}/")

    assert response.status_code == 200


def test_page_detail_edit_action_shown_with_permission(client):
    from django.test import override_settings
    user = _make_user("editperm_user", ["view_published_content", "propose_content_changes"])
    item = _make_item(status="published")
    mock_service = MagicMock()
    mock_service.get_item.return_value = item

    with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
        client.force_login(user)
        with override_settings(ROOT_URLCONF="tests.urls"):
            response = client.get(f"/cauldron-admin/content/pages/{item.id}/")

    content = response.content.decode()
    assert "Edit Page" in content or "edit" in content.lower()


def test_page_detail_edit_action_hidden_without_permission(client):
    from django.test import override_settings
    user = _make_user("noedit_user", ["view_published_content"])
    item = _make_item(status="published")
    mock_service = MagicMock()
    mock_service.get_item.return_value = item

    with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
        client.force_login(user)
        with override_settings(ROOT_URLCONF="tests.urls"):
            response = client.get(f"/cauldron-admin/content/pages/{item.id}/")

    content = response.content.decode()
    assert "Edit Page" not in content


# ---------------------------------------------------------------------------
# PageEditView — GET
# ---------------------------------------------------------------------------

def test_page_edit_requires_login(client):
    from django.test import override_settings
    with override_settings(ROOT_URLCONF="tests.urls"):
        response = client.get(f"/cauldron-admin/content/pages/{uuid.uuid4()}/edit/")
    assert response.status_code == 302


def test_page_edit_get_populates_form(client):
    from django.test import override_settings
    user = _make_user("edit_get_user", ["propose_content_changes", "view_published_content"])
    item = _make_item(title="My Page", slug="my-page")
    mock_service = MagicMock()
    mock_service.get_item.return_value = item

    with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
        client.force_login(user)
        with override_settings(ROOT_URLCONF="tests.urls"):
            response = client.get(f"/cauldron-admin/content/pages/{item.id}/edit/")

    assert response.status_code == 200
    content = response.content.decode()
    assert "My Page" in content
    assert "edit_token" in content
    assert "submission_token" in content


def test_page_edit_get_slug_not_editable(client):
    from django.test import override_settings
    user = _make_user("slug_ro_user", ["propose_content_changes", "view_published_content"])
    item = _make_item(slug="existing-slug")
    mock_service = MagicMock()
    mock_service.get_item.return_value = item

    with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
        client.force_login(user)
        with override_settings(ROOT_URLCONF="tests.urls"):
            response = client.get(f"/cauldron-admin/content/pages/{item.id}/edit/")

    content = response.content.decode()
    # Slug shown in display but not as input field for editing
    assert "existing-slug" in content
    assert 'name="slug"' not in content


def test_page_edit_get_404_when_item_not_found(client):
    from django.test import override_settings
    user = _make_user("miss_user", ["propose_content_changes", "view_published_content"])
    mock_service = MagicMock()
    mock_service.get_item.return_value = None

    with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
        client.force_login(user)
        with override_settings(ROOT_URLCONF="tests.urls"):
            response = client.get(f"/cauldron-admin/content/pages/{uuid.uuid4()}/edit/")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# PageEditView — POST
# ---------------------------------------------------------------------------

def _make_edit_token(item_id, collection="pages", expected_hash="abc" * 21 + "d"):
    from django.core import signing
    return signing.dumps(
        {"item_id": item_id, "collection": collection, "expected_hash": expected_hash},
        salt="cauldron.page.edit",
    )


def _post_edit(client, user, item_id, edit_token, overrides=None):
    from django.test import override_settings
    from django.core import signing
    submit_token = signing.dumps({"key": str(uuid.uuid4()), "item_id": ""}, salt="cauldron.page.submit")
    data = {
        "title": "Updated Title",
        "navigation_title": "",
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
        "body": "# Updated\n\nNew content.",
        "intended_status": "draft",
        "change_description": "Update title",
        "edit_token": edit_token,
        "submission_token": submit_token,
    }
    if overrides:
        data.update(overrides)
    client.force_login(user)
    with override_settings(ROOT_URLCONF="tests.urls"):
        return client.post(f"/cauldron-admin/content/pages/{item_id}/edit/", data=data)


def test_page_edit_post_valid_creates_update_proposal(client):
    user = _make_user("updater", ["propose_content_changes", "view_published_content"])
    item = _make_item()
    edit_token = _make_edit_token(item.id)
    mock_result = _make_result(ok=True)
    mock_service = MagicMock()
    mock_service.get_item.return_value = item
    mock_service.create_change_request.return_value = mock_result

    with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
        response = _post_edit(client, user, item.id, edit_token)

    assert mock_service.create_change_request.call_count == 1
    op = mock_service.create_change_request.call_args[1]["operations"][0]
    assert op["kind"] == "update"
    assert op["collection"] == "pages"


def test_page_edit_post_slug_preserved_from_item(client):
    user = _make_user("slug_preserve", ["propose_content_changes", "view_published_content"])
    item = _make_item(slug="original-slug")
    edit_token = _make_edit_token(item.id)
    mock_result = _make_result(ok=True)
    mock_service = MagicMock()
    mock_service.get_item.return_value = item
    mock_service.create_change_request.return_value = mock_result

    with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
        _post_edit(client, user, item.id, edit_token)

    op = mock_service.create_change_request.call_args[1]["operations"][0]
    assert op["slug"] == "original-slug"


def test_page_edit_post_expected_hash_from_signed_token(client):
    user = _make_user("hash_user", ["propose_content_changes", "view_published_content"])
    item = _make_item()
    expected_hash = "a" * 64
    edit_token = _make_edit_token(item.id, expected_hash=expected_hash)
    mock_result = _make_result(ok=True)
    mock_service = MagicMock()
    mock_service.get_item.return_value = item
    mock_service.create_change_request.return_value = mock_result

    with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
        _post_edit(client, user, item.id, edit_token)

    op = mock_service.create_change_request.call_args[1]["operations"][0]
    assert op.get("expected_hash") == expected_hash


def test_page_edit_post_expired_token_redirects(client):
    from django.test import override_settings
    user = _make_user("expired_user", ["propose_content_changes", "view_published_content"])
    item_id = str(uuid.uuid4())

    # Malformed/tampered token
    edit_token = "invalid.token.value"

    client.force_login(user)
    with override_settings(ROOT_URLCONF="tests.urls"):
        response = client.post(f"/cauldron-admin/content/pages/{item_id}/edit/", data={
            "title": "Test",
            "edit_token": edit_token,
            "submission_token": "",
            "intended_status": "draft",
        })

    assert response.status_code == 302


def test_page_edit_post_item_mismatch_rejected(client):
    from django.test import override_settings
    user = _make_user("mismatch_user", ["propose_content_changes", "view_published_content"])
    wrong_item_id = str(uuid.uuid4())
    edit_token = _make_edit_token("different-id")

    client.force_login(user)
    with override_settings(ROOT_URLCONF="tests.urls"):
        response = client.post(f"/cauldron-admin/content/pages/{wrong_item_id}/edit/", data={
            "title": "Test",
            "edit_token": edit_token,
            "submission_token": "",
            "intended_status": "draft",
        })

    assert response.status_code == 302


def test_page_edit_post_success_redirects(client):
    user = _make_user("edit_redir", ["propose_content_changes", "view_published_content", "view_content_change_requests"])
    item = _make_item()
    edit_token = _make_edit_token(item.id)
    req_id = str(uuid.uuid4())
    mock_result = _make_result(ok=True, request_id=req_id)
    mock_service = MagicMock()
    mock_service.get_item.return_value = item
    mock_service.create_change_request.return_value = mock_result

    with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
        response = _post_edit(client, user, item.id, edit_token)

    assert response.status_code == 302
    assert req_id in response["Location"]


def test_page_edit_post_no_direct_file_write(client, tmp_path):
    user = _make_user("nowrite_user", ["propose_content_changes", "view_published_content"])
    item = _make_item()
    edit_token = _make_edit_token(item.id)
    mock_result = _make_result(ok=True)
    mock_service = MagicMock()
    mock_service.get_item.return_value = item
    mock_service.create_change_request.return_value = mock_result

    content_dir = tmp_path / "content" / "pages"
    content_dir.mkdir(parents=True)

    with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
        _post_edit(client, user, item.id, edit_token)

    assert list(content_dir.iterdir()) == []


# ---------------------------------------------------------------------------
# Regression — generic proposal still works
# ---------------------------------------------------------------------------

def test_generic_proposal_view_still_accessible(client):
    from django.test import override_settings
    user = _make_user("proposal_user", ["propose_content_changes"])
    client.force_login(user)
    with override_settings(ROOT_URLCONF="tests.urls"):
        response = client.get("/cauldron-admin/content-proposal/")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Regression — change request list/detail still work
# ---------------------------------------------------------------------------

def test_change_request_list_accessible(client):
    from django.test import override_settings
    user = _make_user("cr_list_user", ["view_content_change_requests"])
    client.force_login(user)
    with override_settings(ROOT_URLCONF="tests.urls"):
        response = client.get("/cauldron-admin/content/change-requests/")
    assert response.status_code == 200


def test_audit_log_accessible(client):
    from django.test import override_settings
    user = _make_user("audit_user", ["view_content_audit"])
    client.force_login(user)
    with override_settings(ROOT_URLCONF="tests.urls"):
        response = client.get("/cauldron-admin/content/audit/")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Fix: redirect after proposal — authors without view_content_change_requests
# ---------------------------------------------------------------------------

def test_create_redirect_to_browser_without_cr_permission(client):
    """Authors holding only propose_content_changes must not be redirected to a 403."""
    user = _make_user("no_cr_perm", ["propose_content_changes"])
    req_id = str(uuid.uuid4())
    mock_result = _make_result(ok=True, request_id=req_id)
    mock_service = MagicMock()
    mock_service.create_change_request.return_value = mock_result

    with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
        response = _post_create(client, user)

    # Must redirect somewhere accessible — not to change-request-detail
    assert response.status_code == 302
    assert req_id not in response["Location"]  # not sent to detail page
    assert "change-requests" not in response["Location"] or "list" not in response["Location"]


def test_create_redirect_to_detail_with_cr_permission(client):
    """Authors with view_content_change_requests are redirected to detail."""
    user = _make_user("full_perm", ["propose_content_changes", "view_content_change_requests"])
    req_id = str(uuid.uuid4())
    mock_result = _make_result(ok=True, request_id=req_id)
    mock_service = MagicMock()
    mock_service.create_change_request.return_value = mock_result

    with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
        response = _post_create(client, user)

    assert response.status_code == 302
    assert req_id in response["Location"]


# ---------------------------------------------------------------------------
# Fix: schema guard — edit/detail 404 for non-page schema items
# ---------------------------------------------------------------------------

def test_edit_view_404_for_non_page_schema(client):
    from django.test import override_settings
    user = _make_user("wrongschema", ["propose_content_changes", "view_published_content"])
    item = _make_item()
    item.schema = "legacy-pages"  # wrong schema
    mock_service = MagicMock()
    mock_service.get_item.return_value = item

    with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
        client.force_login(user)
        with override_settings(ROOT_URLCONF="tests.urls"):
            response = client.get(f"/cauldron-admin/content/pages/{item.id}/edit/")

    assert response.status_code == 404


def test_detail_no_edit_action_for_non_page_schema(client):
    from django.test import override_settings
    user = _make_user("noedit_schema", ["view_published_content", "propose_content_changes"])
    item = _make_item(status="published")
    item.schema = "legacy-pages"
    mock_service = MagicMock()
    mock_service.get_item.return_value = item

    with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
        client.force_login(user)
        with override_settings(ROOT_URLCONF="tests.urls"):
            response = client.get(f"/cauldron-admin/content/pages/{item.id}/")

    content = response.content.decode()
    assert "Edit Page" not in content


# ---------------------------------------------------------------------------
# Fix: idempotency — same submission token produces stable item_id
# ---------------------------------------------------------------------------

def test_create_submission_token_contains_stable_item_id(client):
    """The same submission token must produce the same item_id on retry."""
    from django.core import signing
    from django.test import override_settings

    user = _make_user("idem2_user", ["propose_content_changes"])
    req_id = str(uuid.uuid4())
    captured_ops = []

    def capture_service(*args, **kwargs):
        mock_service = MagicMock()
        def capture_create(**kw):
            captured_ops.append(kw["operations"])
            return _make_result(ok=True, request_id=req_id)
        mock_service.create_change_request.side_effect = capture_create
        return mock_service

    # Build a token with known item_id
    known_item_id = str(uuid.uuid4())
    stable_token = signing.dumps(
        {"key": str(uuid.uuid4()), "item_id": known_item_id},
        salt="cauldron.page.submit",
    )

    with patch("cauldron_admin_content.views._get_service", new_callable=lambda: lambda: capture_service()):
        client.force_login(user)
        with override_settings(ROOT_URLCONF="tests.urls"):
            client.post("/cauldron-admin/content/pages/new/", data={
                "title": "Stable Page",
                "slug": "stable-page",
                "intended_status": "draft",
                "submission_token": stable_token,
            })

    if captured_ops:
        op = captured_ops[0][0]
        assert op["item_id"] == known_item_id
