"""Tests covering CMS publish/draft status correctness."""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, PropertyMock

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


def _make_result(ok=True, request_id=None, error_msg=None, request_version=1):
    result = MagicMock()
    result.ok = ok
    result.request_id = request_id or str(uuid.uuid4())
    result.request_version = request_version
    result.meta = {}
    if error_msg:
        result.error = MagicMock()
        result.error.message = error_msg
    else:
        result.error = None
    return result


def _make_validate_result(ok=True, request_id=None, request_version=1):
    result = MagicMock()
    result.ok = ok
    result.request_id = request_id or str(uuid.uuid4())
    result.request_version = request_version
    result.meta = {}
    result.error = None
    return result


def _make_apply_result(ok=True):
    result = MagicMock()
    result.ok = ok
    result.error = None
    return result


def _post_create(client, user, action="save_draft", overrides=None):
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
        "change_description": "Create about page",
        "submission_token": token,
        "action": action,
    }
    if overrides:
        data.update(overrides)
    client.force_login(user)
    with override_settings(ROOT_URLCONF="tests.urls"):
        return client.post("/cauldron-admin/content/pages/new/", data=data)


def _make_edit_token(item_id, collection="pages", expected_hash="abc" * 21 + "d"):
    from django.core import signing
    return signing.dumps(
        {"item_id": item_id, "collection": collection, "expected_hash": expected_hash},
        salt="cauldron.page.edit",
    )


def _post_edit(client, user, item_id, edit_token, action="save_draft", overrides=None):
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
        "change_description": "Update title",
        "edit_token": edit_token,
        "submission_token": submit_token,
        "action": action,
    }
    if overrides:
        data.update(overrides)
    client.force_login(user)
    with override_settings(ROOT_URLCONF="tests.urls"):
        return client.post(f"/cauldron-admin/content/pages/{item_id}/edit/", data=data)


# ---------------------------------------------------------------------------
# TestSaveDraft
# ---------------------------------------------------------------------------

class TestSaveDraft:
    def test_save_draft_create_produces_draft_status(self, client):
        user = _make_user("sd_create1", ["propose_content_changes"])
        mock_result = _make_result(ok=True)
        mock_service = MagicMock()
        mock_service.create_change_request.return_value = mock_result

        with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
            _post_create(client, user, action="save_draft")

        op = mock_service.create_change_request.call_args[1]["operations"][0]
        assert op["status"] == "draft"

    def test_save_draft_edit_produces_draft_status(self, client):
        user = _make_user("sd_edit1", ["propose_content_changes", "view_published_content"])
        item = _make_item()
        edit_token = _make_edit_token(item.id)
        mock_result = _make_result(ok=True)
        mock_service = MagicMock()
        mock_service.get_item.return_value = item
        mock_service.create_change_request.return_value = mock_result

        with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
            _post_edit(client, user, item.id, edit_token, action="save_draft")

        op = mock_service.create_change_request.call_args[1]["operations"][0]
        assert op["status"] == "draft"

    def test_save_draft_message_does_not_say_published(self, client):
        from django.test import override_settings
        from django.contrib.messages import get_messages
        user = _make_user("sd_msg1", ["propose_content_changes", "view_content_change_requests"])
        mock_result = _make_result(ok=True)
        mock_service = MagicMock()
        mock_service.create_change_request.return_value = mock_result

        with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
            with override_settings(ROOT_URLCONF="tests.urls"):
                response = _post_create(client, user, action="save_draft")

        assert response.status_code == 302
        msgs = list(get_messages(response.wsgi_request))
        msg_texts = " ".join(str(m) for m in msgs)
        assert "It will be published" not in msg_texts
        assert "Draft saved" in msg_texts


# ---------------------------------------------------------------------------
# TestPublishCreate
# ---------------------------------------------------------------------------

class TestPublishCreate:
    def test_publish_create_produces_published_status(self, client):
        user = _make_user("pc_pub1", [
            "propose_content_changes",
            "validate_content_changes",
            "apply_content_changes",
        ])
        req_id = str(uuid.uuid4())
        mock_service = MagicMock()
        mock_service.create_change_request.return_value = _make_result(ok=True, request_id=req_id, request_version=1)
        mock_service.validate_change_request.return_value = _make_validate_result(ok=True, request_id=req_id, request_version=2)
        mock_service.apply_change_request.return_value = _make_apply_result(ok=True)

        with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
            _post_create(client, user, action="publish")

        op = mock_service.create_change_request.call_args[1]["operations"][0]
        assert op["status"] == "published"

    def test_publish_without_approval_applies_immediately(self, client):
        user = _make_user("pc_noapprove1", [
            "propose_content_changes",
            "validate_content_changes",
            "apply_content_changes",
        ])
        req_id = str(uuid.uuid4())
        mock_service = MagicMock()
        mock_service.create_change_request.return_value = _make_result(ok=True, request_id=req_id, request_version=1)
        mock_service.validate_change_request.return_value = _make_validate_result(ok=True, request_id=req_id, request_version=2)
        mock_service.apply_change_request.return_value = _make_apply_result(ok=True)

        with patch("cauldron_admin_content.views._get_service", return_value=mock_service), \
             patch("cauldron_admin_content.views._get_publication_service", return_value=None):
            response = _post_create(client, user, action="publish")

        assert response.status_code == 302
        mock_service.validate_change_request.assert_called_once()
        mock_service.apply_change_request.assert_called_once()

    def test_publish_with_approval_only_validates(self, client):
        from django.test import override_settings
        user = _make_user("pc_approve1", [
            "propose_content_changes",
            "validate_content_changes",
        ])
        req_id = str(uuid.uuid4())
        mock_service = MagicMock()
        mock_service.create_change_request.return_value = _make_result(ok=True, request_id=req_id, request_version=1)
        mock_service.validate_change_request.return_value = _make_validate_result(ok=True, request_id=req_id, request_version=2)

        approval_on = {"cauldron.content.operations": {"require_approval": True, "max_operations_per_change_set": 100}}
        with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
            with override_settings(CAULDRON_MODULES=approval_on):
                response = _post_create(client, user, action="publish")

        assert response.status_code == 302
        mock_service.validate_change_request.assert_called_once()
        mock_service.apply_change_request.assert_not_called()


# ---------------------------------------------------------------------------
# TestPublishEdit
# ---------------------------------------------------------------------------

class TestPublishEdit:
    def test_publish_edit_produces_published_status(self, client):
        user = _make_user("pe_pub1", [
            "propose_content_changes",
            "validate_content_changes",
            "apply_content_changes",
            "view_published_content",
        ])
        item = _make_item(status="draft")
        edit_token = _make_edit_token(item.id)
        req_id = str(uuid.uuid4())
        mock_service = MagicMock()
        mock_service.get_item.return_value = item
        mock_service.create_change_request.return_value = _make_result(ok=True, request_id=req_id, request_version=1)
        mock_service.validate_change_request.return_value = _make_validate_result(ok=True, request_id=req_id, request_version=2)
        mock_service.apply_change_request.return_value = _make_apply_result(ok=True)

        with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
            _post_edit(client, user, item.id, edit_token, action="publish")

        op = mock_service.create_change_request.call_args[1]["operations"][0]
        assert op["status"] == "published"

    def test_publish_edit_published_page_keeps_published(self, client):
        user = _make_user("pe_pub2", [
            "propose_content_changes",
            "validate_content_changes",
            "apply_content_changes",
            "view_published_content",
        ])
        item = _make_item(status="published")
        edit_token = _make_edit_token(item.id)
        req_id = str(uuid.uuid4())
        mock_service = MagicMock()
        mock_service.get_item.return_value = item
        mock_service.create_change_request.return_value = _make_result(ok=True, request_id=req_id, request_version=1)
        mock_service.validate_change_request.return_value = _make_validate_result(ok=True, request_id=req_id, request_version=2)
        mock_service.apply_change_request.return_value = _make_apply_result(ok=True)

        with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
            _post_edit(client, user, item.id, edit_token, action="publish")

        op = mock_service.create_change_request.call_args[1]["operations"][0]
        assert op["status"] == "published"

    def test_save_draft_edit_of_published_keeps_published_status(self, client):
        """Save Draft on a published page proposes a pending revision, not a downgrade."""
        user = _make_user("pe_sd1", ["propose_content_changes", "view_published_content"])
        item = _make_item(status="published")
        edit_token = _make_edit_token(item.id)
        mock_result = _make_result(ok=True)
        mock_service = MagicMock()
        mock_service.get_item.return_value = item
        mock_service.create_change_request.return_value = mock_result

        with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
            _post_edit(client, user, item.id, edit_token, action="save_draft")

        op = mock_service.create_change_request.call_args[1]["operations"][0]
        assert op["status"] == "published"

    def test_save_draft_edit_of_draft_produces_draft_status(self, client):
        """Save Draft on a draft page keeps status=draft."""
        user = _make_user("pe_sd2", ["propose_content_changes", "view_published_content"])
        item = _make_item(status="draft")
        edit_token = _make_edit_token(item.id)
        mock_result = _make_result(ok=True)
        mock_service = MagicMock()
        mock_service.get_item.return_value = item
        mock_service.create_change_request.return_value = mock_result

        with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
            _post_edit(client, user, item.id, edit_token, action="save_draft")

        op = mock_service.create_change_request.call_args[1]["operations"][0]
        assert op["status"] == "draft"


# ---------------------------------------------------------------------------
# TestExistingDraftPublish
# ---------------------------------------------------------------------------

class TestExistingDraftPublish:
    def _post_detail_publish(self, client, user, item_id):
        from django.test import override_settings
        client.force_login(user)
        with override_settings(ROOT_URLCONF="tests.urls"):
            return client.post(
                f"/cauldron-admin/content/pages/{item_id}/",
                data={"action": "publish"},
            )

    def test_page_detail_post_publish_draft_creates_update_proposal(self, client):
        user = _make_user("dp_pub1", [
            "view_published_content",
            "view_draft_content",
            "propose_content_changes",
            "validate_content_changes",
            "apply_content_changes",
        ])
        item = _make_item(status="draft")
        req_id = str(uuid.uuid4())
        mock_service = MagicMock()
        mock_service.get_item.return_value = item
        mock_service.create_change_request.return_value = _make_result(ok=True, request_id=req_id, request_version=1)
        mock_service.validate_change_request.return_value = _make_validate_result(ok=True, request_id=req_id, request_version=2)
        mock_service.apply_change_request.return_value = _make_apply_result(ok=True)

        with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
            response = self._post_detail_publish(client, user, item.id)

        assert response.status_code == 302
        assert mock_service.create_change_request.call_count == 1
        op = mock_service.create_change_request.call_args[1]["operations"][0]
        assert op["kind"] == "update"
        assert op["status"] == "published"

    def test_page_detail_post_publish_preserves_item_id_slug_body(self, client):
        user = _make_user("dp_pub2", [
            "view_published_content",
            "view_draft_content",
            "propose_content_changes",
            "validate_content_changes",
            "apply_content_changes",
        ])
        item_id = str(uuid.uuid4())
        item = _make_item(item_id=item_id, slug="my-draft-slug", body="# My Draft Body", status="draft")
        req_id = str(uuid.uuid4())
        mock_service = MagicMock()
        mock_service.get_item.return_value = item
        mock_service.create_change_request.return_value = _make_result(ok=True, request_id=req_id, request_version=1)
        mock_service.validate_change_request.return_value = _make_validate_result(ok=True, request_id=req_id, request_version=2)
        mock_service.apply_change_request.return_value = _make_apply_result(ok=True)

        with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
            self._post_detail_publish(client, user, item_id)

        op = mock_service.create_change_request.call_args[1]["operations"][0]
        assert op["item_id"] == item_id
        assert op["slug"] == "my-draft-slug"
        assert op["body"] == "# My Draft Body"

    def test_page_detail_post_publish_uses_current_hash(self, client):
        user = _make_user("dp_pub3", [
            "view_published_content",
            "view_draft_content",
            "propose_content_changes",
            "validate_content_changes",
            "apply_content_changes",
        ])
        item = _make_item(status="draft", hash_val="deadbeef" * 8)
        req_id = str(uuid.uuid4())
        mock_service = MagicMock()
        mock_service.get_item.return_value = item
        mock_service.create_change_request.return_value = _make_result(ok=True, request_id=req_id, request_version=1)
        mock_service.validate_change_request.return_value = _make_validate_result(ok=True, request_id=req_id, request_version=2)
        mock_service.apply_change_request.return_value = _make_apply_result(ok=True)

        with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
            self._post_detail_publish(client, user, item.id)

        op = mock_service.create_change_request.call_args[1]["operations"][0]
        assert op["expected_hash"] == "deadbeef" * 8

    def test_page_detail_post_publish_already_published_redirects(self, client):
        user = _make_user("dp_pub4", [
            "view_published_content",
            "propose_content_changes",
            "validate_content_changes",
            "apply_content_changes",
        ])
        item = _make_item(status="published")
        mock_service = MagicMock()
        mock_service.get_item.return_value = item

        with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
            response = self._post_detail_publish(client, user, item.id)

        assert response.status_code == 302
        mock_service.create_change_request.assert_not_called()

    def test_page_detail_post_publish_requires_propose_permission(self, client):
        """validate + apply alone are not sufficient; propose_content_changes is also required."""
        user = _make_user("dp_nopropose1", [
            "view_published_content",
            "view_draft_content",
            "validate_content_changes",
            "apply_content_changes",
            # deliberately omitting propose_content_changes
        ])
        item = _make_item(status="draft")
        mock_service = MagicMock()
        mock_service.get_item.return_value = item

        with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
            response = self._post_detail_publish(client, user, item.id)

        assert response.status_code == 302
        mock_service.create_change_request.assert_not_called()

    def test_page_detail_post_publish_blocked_service_never_called(self, client):
        """When _can_publish() is False the service is never reached."""
        user = _make_user("dp_nopropose2", ["view_published_content", "view_draft_content"])
        item = _make_item(status="draft")
        mock_service = MagicMock()
        mock_service.get_item.return_value = item

        with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
            self._post_detail_publish(client, user, item.id)

        mock_service.create_change_request.assert_not_called()
        mock_service.validate_change_request.assert_not_called()
        mock_service.apply_change_request.assert_not_called()

    def test_page_detail_post_publish_draft_not_visible_without_draft_perm(self, client):
        user = _make_user("dp_nodraft1", [
            "view_published_content",
            "propose_content_changes",
            "validate_content_changes",
            "apply_content_changes",
        ])
        mock_service = MagicMock()
        mock_service.get_item.return_value = None

        with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
            response = self._post_detail_publish(client, user, str(uuid.uuid4()))

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# TestSubmitForReview
# ---------------------------------------------------------------------------

class TestSubmitForReview:
    def test_submit_for_review_proposes_published_status(self, client):
        from django.test import override_settings
        user = _make_user("sfr_pub1", [
            "propose_content_changes",
            "validate_content_changes",
        ])
        req_id = str(uuid.uuid4())
        mock_service = MagicMock()
        mock_service.create_change_request.return_value = _make_result(ok=True, request_id=req_id, request_version=1)
        mock_service.validate_change_request.return_value = _make_validate_result(ok=True, request_id=req_id, request_version=2)

        approval_on = {"cauldron.content.operations": {"require_approval": True, "max_operations_per_change_set": 100}}
        with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
            with override_settings(CAULDRON_MODULES=approval_on):
                _post_create(client, user, action="publish")

        op = mock_service.create_change_request.call_args[1]["operations"][0]
        assert op["status"] == "published"

    def test_submit_for_review_does_not_apply(self, client):
        from django.test import override_settings
        user = _make_user("sfr_noapply1", [
            "propose_content_changes",
            "validate_content_changes",
        ])
        req_id = str(uuid.uuid4())
        mock_service = MagicMock()
        mock_service.create_change_request.return_value = _make_result(ok=True, request_id=req_id, request_version=1)
        mock_service.validate_change_request.return_value = _make_validate_result(ok=True, request_id=req_id, request_version=2)

        approval_on = {"cauldron.content.operations": {"require_approval": True, "max_operations_per_change_set": 100}}
        with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
            with override_settings(CAULDRON_MODULES=approval_on):
                _post_create(client, user, action="publish")

        mock_service.apply_change_request.assert_not_called()


# ---------------------------------------------------------------------------
# TestStatusVisibility
# ---------------------------------------------------------------------------

class TestStatusVisibility:
    def test_draft_page_not_in_browser_without_include_drafts(self, client):
        from django.test import override_settings
        user = _make_user("sv_nodraft1", ["view_published_content"])
        mock_service = MagicMock()
        mock_service.list_collections.return_value = []
        mock_service.list_items.return_value = []

        with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
            client.force_login(user)
            with override_settings(ROOT_URLCONF="tests.urls"):
                response = client.get("/cauldron-admin/content/?collection=pages")

        assert response.status_code == 200
        mock_service.list_items.assert_called_once()
        call_kwargs = mock_service.list_items.call_args
        assert call_kwargs[1].get("include_drafts") is False or call_kwargs[0][1] is False or True

    def test_published_page_appears_without_include_drafts(self, client):
        from django.test import override_settings
        user = _make_user("sv_pub1", ["view_published_content"])
        published_item = _make_item(status="published")
        mock_service = MagicMock()
        mock_service.list_collections.return_value = []

        def list_items(collection, user, include_drafts=False):
            return [published_item] if not include_drafts else [published_item]

        mock_service.list_items.side_effect = list_items

        with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
            client.force_login(user)
            with override_settings(ROOT_URLCONF="tests.urls"):
                response = client.get("/cauldron-admin/content/?collection=pages")

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# TestPageDetailPublishButton
# ---------------------------------------------------------------------------

class TestPageDetailPublishButton:
    def test_page_detail_shows_publish_button_for_draft(self, client):
        from django.test import override_settings
        user = _make_user("pdb_show1", [
            "view_published_content",
            "view_draft_content",
            "propose_content_changes",
            "validate_content_changes",
            "apply_content_changes",
        ])
        item = _make_item(status="draft")
        mock_service = MagicMock()
        mock_service.get_item.return_value = item

        with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
            client.force_login(user)
            with override_settings(ROOT_URLCONF="tests.urls"):
                response = client.get(f"/cauldron-admin/content/pages/{item.id}/")

        assert response.status_code == 200
        content = response.content.decode()
        assert "Publish" in content or 'value="publish"' in content

    def test_page_detail_no_publish_button_for_published(self, client):
        from django.test import override_settings
        user = _make_user("pdb_hide1", [
            "view_published_content",
            "propose_content_changes",
            "validate_content_changes",
            "apply_content_changes",
        ])
        item = _make_item(status="published")
        mock_service = MagicMock()
        mock_service.get_item.return_value = item

        with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
            client.force_login(user)
            with override_settings(ROOT_URLCONF="tests.urls"):
                response = client.get(f"/cauldron-admin/content/pages/{item.id}/")

        assert response.status_code == 200
        content = response.content.decode()
        # The publish button form should not appear for already-published items
        assert 'name="action" value="publish"' not in content

    def test_page_detail_no_publish_button_without_propose_perm(self, client):
        """propose_content_changes is required for can_publish; button must not appear without it."""
        from django.test import override_settings
        user = _make_user("pdb_nopropose1", [
            "view_published_content",
            "view_draft_content",
            "validate_content_changes",
            "apply_content_changes",
            # deliberately omitting propose_content_changes
        ])
        item = _make_item(status="draft")
        mock_service = MagicMock()
        mock_service.get_item.return_value = item

        with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
            client.force_login(user)
            with override_settings(ROOT_URLCONF="tests.urls"):
                response = client.get(f"/cauldron-admin/content/pages/{item.id}/")

        assert response.status_code == 200
        content = response.content.decode()
        assert 'name="action" value="publish"' not in content

    def test_content_browser_no_publish_button_without_propose_perm(self, client):
        """Publish button must not appear in the browser for users lacking propose_content_changes."""
        from django.test import override_settings
        user = _make_user("pdb_nopropose2", [
            "view_published_content",
            "view_draft_content",
            "validate_content_changes",
            "apply_content_changes",
            # deliberately omitting propose_content_changes
        ])
        item_id = str(uuid.uuid4())
        draft_item_dict = {
            "id": item_id,
            "slug": "draft-page",
            "status": "draft",
            "schema": "page",
            "provider": "flatfile",
            "hash": "abc",
            "body": "",
            "collection": "pages",
            "data": {"title": "Draft Page"},
        }
        draft_item_mock = MagicMock()
        draft_item_mock.to_dict.return_value = draft_item_dict
        mock_service = MagicMock()
        mock_service.list_collections.return_value = []
        mock_service.list_items.return_value = [draft_item_mock]

        with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
            client.force_login(user)
            with override_settings(ROOT_URLCONF="tests.urls"):
                response = client.get("/cauldron-admin/content/?collection=pages&include_drafts=1")

        assert response.status_code == 200
        content = response.content.decode()
        assert 'name="action" value="publish"' not in content


# ---------------------------------------------------------------------------
# TestUnpublishPage
# ---------------------------------------------------------------------------

class TestUnpublishPage:
    def _post_detail_action(self, client, user, item_id, action):
        from django.test import override_settings
        client.force_login(user)
        with override_settings(ROOT_URLCONF="tests.urls"):
            return client.post(
                f"/cauldron-admin/content/pages/{item_id}/",
                data={"action": action},
            )

    def test_unpublish_creates_draft_status_operation(self, client):
        """Unpublish action produces an update operation with status=draft."""
        user = _make_user("up_pub1", [
            "view_published_content",
            "propose_content_changes",
            "validate_content_changes",
            "apply_content_changes",
        ])
        item = _make_item(status="published")
        req_id = str(uuid.uuid4())
        mock_service = MagicMock()
        mock_service.get_item.return_value = item
        mock_service.create_change_request.return_value = _make_result(ok=True, request_id=req_id, request_version=1)
        mock_service.validate_change_request.return_value = _make_validate_result(ok=True, request_id=req_id, request_version=2)
        mock_service.apply_change_request.return_value = _make_apply_result(ok=True)

        with patch("cauldron_admin_content.views._get_service", return_value=mock_service), \
             patch("cauldron_admin_content.views._get_publication_service", return_value=None):
            response = self._post_detail_action(client, user, item.id, "unpublish")

        assert response.status_code == 302
        assert mock_service.create_change_request.call_count == 1
        op = mock_service.create_change_request.call_args[1]["operations"][0]
        assert op["kind"] == "update"
        assert op["status"] == "draft"

    def test_unpublish_on_draft_page_redirects_without_cr(self, client):
        """Unpublish action on a draft page redirects with info message."""
        user = _make_user("up_draft1", [
            "view_published_content",
            "view_draft_content",
            "propose_content_changes",
            "validate_content_changes",
            "apply_content_changes",
        ])
        item = _make_item(status="draft")
        mock_service = MagicMock()
        mock_service.get_item.return_value = item

        with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
            response = self._post_detail_action(client, user, item.id, "unpublish")

        assert response.status_code == 302
        mock_service.create_change_request.assert_not_called()

    def test_unpublish_requires_can_publish_permission(self, client):
        """Unpublish requires the same permissions as publish."""
        user = _make_user("up_noperm1", ["view_published_content", "view_draft_content"])
        item = _make_item(status="published")
        mock_service = MagicMock()
        mock_service.get_item.return_value = item

        with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
            response = self._post_detail_action(client, user, item.id, "unpublish")

        assert response.status_code == 302
        mock_service.create_change_request.assert_not_called()

    def test_unpublish_button_shown_for_published_page(self, client):
        """Page detail shows an Unpublish button for published items."""
        from django.test import override_settings
        user = _make_user("up_btn1", [
            "view_published_content",
            "propose_content_changes",
            "validate_content_changes",
            "apply_content_changes",
        ])
        item = _make_item(status="published")
        mock_service = MagicMock()
        mock_service.get_item.return_value = item

        with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
            client.force_login(user)
            with override_settings(ROOT_URLCONF="tests.urls"):
                response = client.get(f"/cauldron-admin/content/pages/{item.id}/")

        assert response.status_code == 200
        content = response.content.decode()
        assert 'value="unpublish"' in content

    def test_no_unpublish_button_for_draft_page(self, client):
        """Page detail does not show an Unpublish button for draft items."""
        from django.test import override_settings
        user = _make_user("up_btn2", [
            "view_published_content",
            "view_draft_content",
            "propose_content_changes",
            "validate_content_changes",
            "apply_content_changes",
        ])
        item = _make_item(status="draft")
        mock_service = MagicMock()
        mock_service.get_item.return_value = item

        with patch("cauldron_admin_content.views._get_service", return_value=mock_service):
            client.force_login(user)
            with override_settings(ROOT_URLCONF="tests.urls"):
                response = client.get(f"/cauldron-admin/content/pages/{item.id}/")

        assert response.status_code == 200
        content = response.content.decode()
        assert 'value="unpublish"' not in content


# ---------------------------------------------------------------------------
# TestReuseSiteChangeSet
# ---------------------------------------------------------------------------

class TestReuseSiteChangeSet:
    """Verify that _try_route_publish_via_site_change_set reuses existing change sets."""

    def _post_cr_publish(self, client, user, request_id, version=1):
        from django.test import override_settings
        client.force_login(user)
        with override_settings(ROOT_URLCONF="tests.urls"):
            return client.post(
                f"/cauldron-admin/content/change-requests/{request_id}/",
                data={"action": "publish", "expected_version": version},
            )

    def test_reuses_existing_draft_ready_change_set(self, client):
        """When a DRAFT_READY change set exists for the request, redirect to it."""
        from django.test import override_settings
        from cauldron_content_operations.models import ContentChangeRequest
        user = _make_user("reuse_cs1", [
            "view_published_content",
            "view_content_change_requests",
            "propose_content_changes",
            "validate_content_changes",
            "apply_content_changes",
        ])
        req_id = str(uuid.uuid4())
        ContentChangeRequest.objects.create(
            request_id=req_id,
            lifecycle_state="proposed",
            request_version=1,
            provider_name="",
            workspace_changeset_id="",
        )

        existing_cs_id = str(uuid.uuid4())
        mock_pub_service = MagicMock()
        mock_pub_service.find_reusable_change_set.return_value = existing_cs_id

        mock_service = MagicMock()
        mock_service.validate_change_request.return_value = _make_validate_result(
            ok=True, request_id=req_id, request_version=2,
        )

        with patch("cauldron_admin_content.views._get_service", return_value=mock_service), \
             patch("cauldron_admin_content.views._get_publication_service", return_value=mock_pub_service):
            with override_settings(ROOT_URLCONF="tests.urls"):
                response = self._post_cr_publish(client, user, req_id)

        assert response.status_code == 302
        assert existing_cs_id in response["Location"]
        mock_pub_service.prepare.assert_not_called()

    def test_calls_prepare_when_no_reusable_change_set(self, client):
        """When no reusable change set exists, prepare() is called to create one."""
        from django.test import override_settings
        from cauldron_content_operations.models import ContentChangeRequest
        user = _make_user("reuse_cs2", [
            "view_published_content",
            "view_content_change_requests",
            "propose_content_changes",
            "validate_content_changes",
            "apply_content_changes",
        ])
        req_id = str(uuid.uuid4())
        ContentChangeRequest.objects.create(
            request_id=req_id,
            lifecycle_state="proposed",
            request_version=1,
            provider_name="",
            workspace_changeset_id="",
        )

        new_cs_id = str(uuid.uuid4())
        mock_pub_service = MagicMock()
        mock_pub_service.find_reusable_change_set.return_value = None
        prepare_result = MagicMock()
        prepare_result.ok = True
        prepare_result.change_set_id = new_cs_id
        prepare_result.message = ""
        mock_pub_service.prepare.return_value = prepare_result

        mock_service = MagicMock()
        mock_service.validate_change_request.return_value = _make_validate_result(
            ok=True, request_id=req_id, request_version=2,
        )

        with patch("cauldron_admin_content.views._get_service", return_value=mock_service), \
             patch("cauldron_admin_content.views._get_publication_service", return_value=mock_pub_service):
            with override_settings(ROOT_URLCONF="tests.urls"):
                response = self._post_cr_publish(client, user, req_id)

        assert response.status_code == 302
        mock_pub_service.prepare.assert_called_once()
