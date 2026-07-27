"""Tests for the HomepageView admin interface."""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from unittest.mock import MagicMock, patch


@pytest.fixture
def superuser(db):
    User = get_user_model()
    return User.objects.create_superuser(
        username="homepage_test_admin",
        email="homepage@example.com",
        password="testpassword123",
    )


@pytest.fixture
def auth_client(superuser):
    c = Client()
    c.force_login(superuser)
    return c


HOMEPAGE_URL = "/cauldron/content/homepage/"


def _make_mock_item(status="draft"):
    """Return a mock content item for the homepage."""
    item = MagicMock()
    item.id = "homepage"
    item.slug = "homepage"
    item.status = status
    item.hash = "abc123def456"
    item.body = "# Welcome\n\nHome page body."
    item.schema = "page"
    item.data = {
        "title": "Welcome",
        "navigation_title": "Home",
        "summary": "The homepage.",
        "template": "homepage",
        "seo_title": "",
        "meta_description": "",
        "canonical_url": "",
        "robots_index": True,
        "robots_follow": True,
        "social_title": "",
        "social_description": "",
        "social_image": "",
    }
    return item


class TestHomepageViewGet:
    @pytest.mark.django_db
    def test_get_shows_create_form_when_homepage_does_not_exist(self, auth_client):
        """GET renders create form (PageCreateForm) when homepage doesn't exist."""
        with patch(
            "cauldron_admin_content.views._get_service"
        ) as mock_get_service:
            mock_service = MagicMock()
            mock_service.get_item.return_value = None
            mock_get_service.return_value = mock_service

            response = auth_client.get(HOMEPAGE_URL)

        assert response.status_code == 200
        template_names = [t.name for t in response.templates]
        assert "cauldron_admin_content/homepage.html" in template_names
        # No item in context means create form
        assert response.context["item"] is None
        assert response.context["is_edit"] is False

    @pytest.mark.django_db
    def test_get_shows_edit_form_when_homepage_exists(self, auth_client):
        """GET renders edit form pre-filled with current data when homepage exists."""
        mock_item = _make_mock_item(status="draft")

        with patch(
            "cauldron_admin_content.views._get_service"
        ) as mock_get_service:
            mock_service = MagicMock()
            mock_service.get_item.return_value = mock_item
            mock_get_service.return_value = mock_service

            response = auth_client.get(HOMEPAGE_URL)

        assert response.status_code == 200
        template_names = [t.name for t in response.templates]
        assert "cauldron_admin_content/homepage.html" in template_names
        assert response.context["item"] is mock_item
        assert response.context["is_edit"] is True
        # Form should be pre-filled with title
        form = response.context["form"]
        assert form.initial.get("title") == "Welcome"

    @pytest.mark.django_db
    def test_get_shows_view_site_link_when_build_exists(self, auth_client, tmp_path):
        """GET shows 'View Site' link when index.html exists in output_root."""
        (tmp_path / "index.html").write_text("<html>Built site</html>")
        mock_item = _make_mock_item(status="published")

        from django.test import override_settings
        from django.conf import settings

        modules = dict(getattr(settings, "CAULDRON_MODULES", {}))
        modules["cauldron.site.astro"] = {
            "frontend_root": "/tmp/nonexistent",
            "output_root": str(tmp_path),
        }

        with override_settings(CAULDRON_MODULES=modules):
            with patch(
                "cauldron_admin_content.views._get_service"
            ) as mock_get_service:
                mock_service = MagicMock()
                mock_service.get_item.return_value = mock_item
                mock_get_service.return_value = mock_service

                response = auth_client.get(HOMEPAGE_URL)

        assert response.status_code == 200
        assert response.context["build_exists"] is True
        content = response.content.decode()
        assert "View Site" in content


class TestHomepageViewPost:
    @pytest.mark.django_db
    def test_post_save_draft_creates_homepage(self, auth_client):
        """POST save_draft creates homepage as draft when it doesn't exist."""
        mock_result = MagicMock()
        mock_result.ok = True
        mock_result.request_id = "req-00000000-0000-0000-0000-000000000001"

        with patch(
            "cauldron_admin_content.views._get_service"
        ) as mock_get_service:
            mock_service = MagicMock()
            mock_service.get_item.return_value = None
            mock_service.create_change_request.return_value = mock_result
            mock_get_service.return_value = mock_service

            response = auth_client.post(HOMEPAGE_URL, {
                "action": "save_draft",
                "title": "Welcome to Cauldron",
                "body": "# Welcome\n\nHello world.",
                "template": "homepage",
                "submission_token": "",
            })

        # Should redirect back to the homepage admin URL on success
        assert response.status_code == 302
        assert response["Location"] == HOMEPAGE_URL
        mock_service.create_change_request.assert_called_once()
        call_kwargs = mock_service.create_change_request.call_args[1]
        ops = call_kwargs["operations"]
        assert len(ops) == 1
        assert ops[0]["kind"] == "create"
        assert ops[0]["item_id"] == "homepage"

    @pytest.mark.django_db
    def test_post_save_draft_updates_existing_homepage(self, auth_client):
        """POST save_draft updates homepage as draft when it already exists."""
        from cauldron_admin_content.views import _make_edit_token
        from cauldron_content.homepage import HOMEPAGE_COLLECTION

        mock_item = _make_mock_item(status="draft")
        edit_token = _make_edit_token("homepage", HOMEPAGE_COLLECTION, "abc123def456")

        mock_result = MagicMock()
        mock_result.ok = True
        mock_result.request_id = "req-00000000-0000-0000-0000-000000000002"

        with patch(
            "cauldron_admin_content.views._get_service"
        ) as mock_get_service:
            mock_service = MagicMock()
            mock_service.get_item.return_value = mock_item
            mock_service.create_change_request.return_value = mock_result
            mock_get_service.return_value = mock_service

            response = auth_client.post(HOMEPAGE_URL, {
                "action": "save_draft",
                "title": "Updated Homepage",
                "body": "# Updated\n\nNew content.",
                "template": "homepage",
                "edit_token": edit_token,
                "submission_token": "",
            })

        assert response.status_code == 302
        assert response["Location"] == HOMEPAGE_URL
        call_kwargs = mock_service.create_change_request.call_args[1]
        ops = call_kwargs["operations"]
        assert ops[0]["kind"] == "update"
        assert ops[0]["item_id"] == "homepage"

    @pytest.mark.django_db
    def test_post_invalid_form_rerenders(self, auth_client):
        """POST with missing required title re-renders the form with errors."""
        with patch(
            "cauldron_admin_content.views._get_service"
        ) as mock_get_service:
            mock_service = MagicMock()
            mock_service.get_item.return_value = None
            mock_get_service.return_value = mock_service

            response = auth_client.post(HOMEPAGE_URL, {
                "action": "save_draft",
                "title": "",  # required — should fail validation
                "body": "",
                "submission_token": "",
            })

        assert response.status_code == 200
        assert response.context["form"].errors

    @pytest.mark.django_db
    def test_post_publish_calls_publish_flow(self, auth_client):
        """POST publish invokes validate+apply for homepage when approval not required."""
        from cauldron_content_operations.config import get_operations_config

        mock_result = MagicMock()
        mock_result.ok = True
        mock_result.request_id = "req-00000000-0000-0000-0000-000000000003"
        mock_result.request_version = 1

        mock_validate = MagicMock()
        mock_validate.ok = True
        mock_validate.meta = {}
        mock_validate.request_version = 1

        mock_apply = MagicMock()
        mock_apply.ok = True

        with patch(
            "cauldron_admin_content.views._get_service"
        ) as mock_get_service:
            mock_service = MagicMock()
            mock_service.get_item.return_value = None
            mock_service.create_change_request.return_value = mock_result
            mock_service.validate_change_request.return_value = mock_validate
            mock_service.apply_change_request.return_value = mock_apply
            mock_get_service.return_value = mock_service

            response = auth_client.post(HOMEPAGE_URL, {
                "action": "publish",
                "title": "Welcome",
                "body": "# Hello",
                "template": "homepage",
                "submission_token": "",
            })

        # Should call validate and apply
        mock_service.validate_change_request.assert_called_once()
        mock_service.apply_change_request.assert_called_once()
        # On success, redirects to content browser (after publish)
        assert response.status_code == 302


class TestHomepagePublishMessages:
    """Verify that publish messages are only shown after confirmed success."""

    @pytest.mark.django_db
    def test_validation_exception_shows_error_only(self, auth_client):
        """Validation exception → error message only, no success message."""
        mock_result = MagicMock()
        mock_result.ok = True
        mock_result.request_id = "req-msg-001"
        mock_result.request_version = 1

        with patch("cauldron_admin_content.views._get_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.get_item.return_value = None
            mock_service.create_change_request.return_value = mock_result
            mock_service.validate_change_request.side_effect = Exception("Validation exploded")
            mock_get_service.return_value = mock_service

            response = auth_client.post(HOMEPAGE_URL, {
                "action": "publish",
                "title": "Home",
                "body": "# Hello",
                "template": "homepage",
                "submission_token": "",
            }, follow=True)

        messages_list = list(response.context["messages"])
        assert any("error" in str(m.tags) or m.level_tag == "error" for m in messages_list), \
            "Expected an error message for validation exception"
        assert not any(
            "published" in str(m.message).lower() or "submitted" in str(m.message).lower() or "queued" in str(m.message).lower()
            for m in messages_list
        ), "No success message should appear after validation exception"

    @pytest.mark.django_db
    def test_apply_failure_shows_error_only(self, auth_client):
        """Apply failure → error message only, no success message."""
        from django.contrib.messages import get_messages

        mock_result = MagicMock()
        mock_result.ok = True
        mock_result.request_id = "req-msg-002"
        mock_result.request_version = 1

        mock_validate = MagicMock()
        mock_validate.ok = True
        mock_validate.meta = {}
        mock_validate.request_version = 2

        mock_apply = MagicMock()
        mock_apply.ok = False
        mock_apply.error = MagicMock()
        mock_apply.error.message = "Apply failed: conflict"

        with patch("cauldron_admin_content.views._get_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.get_item.return_value = None
            mock_service.create_change_request.return_value = mock_result
            mock_service.validate_change_request.return_value = mock_validate
            mock_service.apply_change_request.return_value = mock_apply
            mock_get_service.return_value = mock_service

            # Don't follow: apply failure redirects to a CR detail URL that
            # doesn't exist in the test DB, so the followed page has no messages.
            response = auth_client.post(HOMEPAGE_URL, {
                "action": "publish",
                "title": "Home",
                "body": "# Hello",
                "template": "homepage",
                "submission_token": "",
            })

        assert response.status_code == 302
        messages_list = list(get_messages(response.wsgi_request))
        assert any("error" in str(m.tags) or m.level_tag == "error" for m in messages_list)
        assert not any(
            "published" in str(m.message).lower() or "queued" in str(m.message).lower()
            for m in messages_list
        )

    @pytest.mark.django_db
    def test_successful_publish_shows_queued_message(self, auth_client):
        """Successful publish → homepage-specific queued message (not generic 'Page published')."""
        mock_result = MagicMock()
        mock_result.ok = True
        mock_result.request_id = "req-msg-003"
        mock_result.request_version = 1

        mock_validate = MagicMock()
        mock_validate.ok = True
        mock_validate.meta = {}
        mock_validate.request_version = 2

        mock_apply = MagicMock()
        mock_apply.ok = True

        with patch("cauldron_admin_content.views._get_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.get_item.return_value = None
            mock_service.create_change_request.return_value = mock_result
            mock_service.validate_change_request.return_value = mock_validate
            mock_service.apply_change_request.return_value = mock_apply
            mock_get_service.return_value = mock_service

            response = auth_client.post(HOMEPAGE_URL, {
                "action": "publish",
                "title": "Home",
                "body": "# Hello",
                "template": "homepage",
                "submission_token": "",
            }, follow=True)

        messages_list = list(response.context["messages"])
        assert any(
            "queued" in str(m.message).lower() or "homepage" in str(m.message).lower()
            for m in messages_list
        ), "Expected a homepage-specific message after successful publish"
        # Must NOT say "Page published successfully" (generic message)
        assert not any(
            m.message == "Page published successfully." for m in messages_list
        )
