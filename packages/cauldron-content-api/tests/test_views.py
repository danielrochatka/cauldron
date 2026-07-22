"""Tests for API views."""
import json
import pytest
from django.test import RequestFactory
from unittest.mock import MagicMock, patch

pytestmark = pytest.mark.django_db


def _make_user(is_superuser=False, username="apiuser"):
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user = User.objects.create_user(username=username, password="password")
    if is_superuser:
        user.is_superuser = True
        user.is_staff = True
        user.save()
    return user


def _make_request(method, path, user=None, data=None, content_type="application/json"):
    factory = RequestFactory()
    fn = getattr(factory, method)
    if data is not None:
        req = fn(path, data=json.dumps(data), content_type=content_type)
    else:
        req = fn(path)
    if user:
        req.user = user
    else:
        from django.contrib.auth.models import AnonymousUser
        req.user = AnonymousUser()
    return req


class TestCollectionsView:
    def test_anonymous_returns_401(self):
        from cauldron_content_api.views import CollectionsView
        req = _make_request("get", "/collections/")
        view = CollectionsView.as_view()
        response = view(req)
        assert response.status_code == 401

    def test_authenticated_returns_200(self):
        from cauldron_content_api.views import CollectionsView
        user = _make_user(is_superuser=True, username="coluser")
        req = _make_request("get", "/collections/", user=user)
        with patch("cauldron_content_api.views.get_service") as mock_svc_fn:
            mock_svc = MagicMock()
            mock_svc.list_collections.return_value = ["pages", "posts"]
            mock_svc_fn.return_value = mock_svc
            view = CollectionsView.as_view()
            response = view(req)
        assert response.status_code == 200
        body = json.loads(response.content)
        assert "data" in body
        assert "collections" in body["data"]


class TestChangeRequestListView:
    def test_anonymous_returns_401(self):
        from cauldron_content_api.views import ChangeRequestListView
        req = _make_request("get", "/change-requests/")
        view = ChangeRequestListView.as_view()
        response = view(req)
        assert response.status_code == 401

    def test_post_invalid_json_returns_400(self):
        from cauldron_content_api.views import ChangeRequestListView
        user = _make_user(is_superuser=True, username="crpostuser")
        factory = RequestFactory()
        req = factory.post("/change-requests/", data=b"not-json", content_type="application/json")
        req.user = user
        view = ChangeRequestListView.as_view()
        response = view(req)
        assert response.status_code == 400
        body = json.loads(response.content)
        assert "error" in body

    def test_post_wrong_content_type_returns_400(self):
        from cauldron_content_api.views import ChangeRequestListView
        user = _make_user(is_superuser=True, username="crctuser")
        factory = RequestFactory()
        req = factory.post("/change-requests/", data="foo=bar", content_type="application/x-www-form-urlencoded")
        req.user = user
        view = ChangeRequestListView.as_view()
        response = view(req)
        assert response.status_code == 400

    def test_success_envelope_used(self):
        from cauldron_content_api.views import ChangeRequestListView
        user = _make_user(is_superuser=True, username="envelopeuser")
        req = _make_request("get", "/change-requests/", user=user)
        with patch("cauldron_content_api.views.get_service") as mock_svc_fn:
            mock_svc = MagicMock()
            mock_svc.list_change_requests.return_value = []
            mock_svc_fn.return_value = mock_svc
            view = ChangeRequestListView.as_view()
            response = view(req)
        assert response.status_code == 200
        body = json.loads(response.content)
        assert "data" in body
        assert "meta" in body

    def test_workspace_paths_not_exposed(self):
        """API responses must not contain workspace path information."""
        from cauldron_content_api.views import ChangeRequestDetailView
        user = _make_user(is_superuser=True, username="pathuser")
        req = _make_request("get", "/change-requests/fake-id/", user=user)
        with patch("cauldron_content_api.views.get_service") as mock_svc_fn:
            mock_svc = MagicMock()
            from cauldron_content_operations.results import ChangeRequestDetail
            detail = ChangeRequestDetail(
                request_id="fake-id",
                workspace_changeset_id="cs-internal",
                provider_name="flatfile",
                lifecycle_state="proposed",
                request_version=1,
                payload_hash="abc",
                idempotency_key="",
                created_by_id=None,
                validated_by_id=None,
                approved_by_id=None,
                rejected_by_id=None,
                applied_by_id=None,
                rolled_back_by_id=None,
                created_at=None,
                validated_at=None,
                approved_at=None,
                rejected_at=None,
                applied_at=None,
                rolled_back_at=None,
            )
            mock_svc.get_change_request.return_value = detail
            mock_svc_fn.return_value = mock_svc
            view = ChangeRequestDetailView.as_view()
            response = view(req, request_id="fake-id")
        body = json.loads(response.content)
        body_str = json.dumps(body)
        # workspace_changeset_id is returned (it's an internal reference, not a filesystem path)
        # but we should not have filesystem paths
        assert "/home/" not in body_str
        assert ".json" not in body_str


class TestServiceFactoryFailClosed:
    """Item 11: service factory raises ImproperlyConfigured, views translate."""

    def test_missing_workspace_root_raises(self):
        from django.test import override_settings
        from django.core.exceptions import ImproperlyConfigured
        from cauldron_content_api.service_factory import get_service
        with override_settings(CAULDRON_MODULES={"cauldron.content": {}}):
            with pytest.raises(ImproperlyConfigured):
                get_service()

    def test_view_catches_and_returns_internal_error_envelope(self):
        from django.core.exceptions import ImproperlyConfigured
        from cauldron_content_api.views import CollectionsView
        user = _make_user(is_superuser=True, username="factoryuser")
        req = _make_request("get", "/collections/", user=user)
        with patch("cauldron_content_api.views.get_service") as mock_svc_fn:
            mock_svc_fn.side_effect = ImproperlyConfigured("bad workspace")
            view = CollectionsView.as_view()
            response = view(req)
        assert response.status_code == 500
        body = json.loads(response.content)
        assert body["error"]["code"] == "internal_error"
        # No path leakage.
        body_str = json.dumps(body)
        assert "/home/" not in body_str
        assert "workspace_root" not in body_str.lower()


class TestErrorEnvelopes:
    def test_error_envelope_structure(self):
        from cauldron_content_api.envelope import error_response
        resp = error_response("test.code", "Test message", details=["detail1"])
        body = json.loads(resp.content)
        assert "error" in body
        assert body["error"]["code"] == "test.code"
        assert body["error"]["message"] == "Test message"
        assert "detail1" in body["error"]["details"]

    def test_success_envelope_structure(self):
        from cauldron_content_api.envelope import success_response
        resp = success_response({"key": "value"})
        body = json.loads(resp.content)
        assert "data" in body
        assert "meta" in body
        assert body["data"]["key"] == "value"
