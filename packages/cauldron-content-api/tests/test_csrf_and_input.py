"""CSRF and input-hardening integration tests for the content API."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory

pytestmark = pytest.mark.django_db


def _make_user(is_superuser=False, username="csrfuser"):
    from django.contrib.auth import get_user_model

    User = get_user_model()
    user = User.objects.create_user(username=username, password="password")
    if is_superuser:
        user.is_superuser = True
        user.is_staff = True
        user.save()
    return user


class TestExpectedVersionEnforcement:
    def test_validate_missing_expected_version_returns_400(self):
        from cauldron_content_api.views import ChangeRequestValidateView

        user = _make_user(is_superuser=True, username="veruser1")
        factory = RequestFactory()
        req = factory.post("/", data=b"", content_type="text/plain")
        req.user = user
        view = ChangeRequestValidateView.as_view()
        resp = view(req, request_id="fake-id")
        assert resp.status_code == 400
        body = json.loads(resp.content)
        assert body["error"]["code"] == "conflict.version_required"

    def test_approve_expected_version_zero_returns_400(self):
        from cauldron_content_api.views import ChangeRequestApproveView

        user = _make_user(is_superuser=True, username="veruser2")
        factory = RequestFactory()
        req = factory.post(
            "/",
            data=json.dumps({"expected_version": 0}),
            content_type="application/json",
        )
        req.user = user
        view = ChangeRequestApproveView.as_view()
        resp = view(req, request_id="fake-id")
        assert resp.status_code == 400

    def test_apply_expected_version_negative_returns_400(self):
        from cauldron_content_api.views import ChangeRequestApplyView

        user = _make_user(is_superuser=True, username="veruser3")
        factory = RequestFactory()
        req = factory.post(
            "/",
            data=json.dumps({"expected_version": -1}),
            content_type="application/json",
        )
        req.user = user
        view = ChangeRequestApplyView.as_view()
        resp = view(req, request_id="fake-id")
        assert resp.status_code == 400

    def test_reject_expected_version_string_returns_400(self):
        from cauldron_content_api.views import ChangeRequestRejectView

        user = _make_user(is_superuser=True, username="veruser4")
        factory = RequestFactory()
        req = factory.post(
            "/",
            data=json.dumps({"expected_version": "5"}),
            content_type="application/json",
        )
        req.user = user
        view = ChangeRequestRejectView.as_view()
        resp = view(req, request_id="fake-id")
        assert resp.status_code == 400

    def test_rollback_expected_version_bool_returns_400(self):
        """Booleans are not valid expected_version values (bool is a subclass of int)."""
        from cauldron_content_api.views import ChangeRequestRollbackView

        user = _make_user(is_superuser=True, username="veruser5")
        factory = RequestFactory()
        req = factory.post(
            "/",
            data=json.dumps({"expected_version": True}),
            content_type="application/json",
        )
        req.user = user
        view = ChangeRequestRollbackView.as_view()
        resp = view(req, request_id="fake-id")
        assert resp.status_code == 400


class TestCreateInputHardening:
    def test_missing_operations_field_returns_400(self):
        from cauldron_content_api.views import ChangeRequestListView

        user = _make_user(is_superuser=True, username="createuser1")
        factory = RequestFactory()
        req = factory.post("/", data=json.dumps({}), content_type="application/json")
        req.user = user
        view = ChangeRequestListView.as_view()
        resp = view(req)
        assert resp.status_code == 400
        body = json.loads(resp.content)
        assert body["error"]["code"] in ("request.empty_operations",)

    def test_invalid_operations_type_returns_400(self):
        from cauldron_content_api.views import ChangeRequestListView

        user = _make_user(is_superuser=True, username="createuser2")
        factory = RequestFactory()
        req = factory.post(
            "/",
            data=json.dumps({"operations": "not-a-list"}),
            content_type="application/json",
        )
        req.user = user
        view = ChangeRequestListView.as_view()
        resp = view(req)
        assert resp.status_code == 400

    def test_missing_operation_collection_returns_400(self):
        from cauldron_content_api.views import ChangeRequestListView

        user = _make_user(is_superuser=True, username="createuser3")
        factory = RequestFactory()
        req = factory.post(
            "/",
            data=json.dumps({"operations": [{"kind": "create", "item_id": "p1"}]}),
            content_type="application/json",
        )
        req.user = user
        view = ChangeRequestListView.as_view()
        resp = view(req)
        assert resp.status_code == 400
        body = json.loads(resp.content)
        assert body["error"]["code"] == "request.missing_field"

    def test_missing_operation_item_id_returns_400(self):
        from cauldron_content_api.views import ChangeRequestListView

        user = _make_user(is_superuser=True, username="createuser4")
        factory = RequestFactory()
        req = factory.post(
            "/",
            data=json.dumps({"operations": [{"kind": "create", "collection": "pages"}]}),
            content_type="application/json",
        )
        req.user = user
        view = ChangeRequestListView.as_view()
        resp = view(req)
        assert resp.status_code == 400

    def test_provider_name_non_string_returns_400(self):
        from cauldron_content_api.views import ChangeRequestListView

        user = _make_user(is_superuser=True, username="createuser5")
        factory = RequestFactory()
        req = factory.post(
            "/",
            data=json.dumps({
                "operations": [{"kind": "create", "collection": "pages", "item_id": "p1"}],
                "provider_name": 123,
            }),
            content_type="application/json",
        )
        req.user = user
        view = ChangeRequestListView.as_view()
        resp = view(req)
        assert resp.status_code == 400


class TestCSRFEnforcement:
    def test_post_without_csrf_rejected_via_client(self):
        """Django's test Client with enforce_csrf_checks rejects unsafe POSTs."""
        from django.test import Client

        user = _make_user(is_superuser=True, username="csrfpost")
        client = Client(enforce_csrf_checks=True)
        client.force_login(user)
        resp = client.post(
            "/change-requests/",
            data=json.dumps({
                "operations": [{"kind": "create", "collection": "pages", "item_id": "p1"}],
                "provider_name": "flatfile",
            }),
            content_type="application/json",
        )
        # 403 because no CSRF token was supplied.
        assert resp.status_code == 403

    def test_get_does_not_require_csrf(self):
        """GET is a safe method and does not require CSRF tokens."""
        from django.test import Client

        user = _make_user(is_superuser=True, username="csrfget")
        client = Client(enforce_csrf_checks=True)
        client.force_login(user)
        with patch("cauldron_content_api.views.get_service") as mock_svc_fn:
            mock_svc = MagicMock()
            mock_svc.list_collections.return_value = []
            mock_svc_fn.return_value = mock_svc
            resp = client.get("/collections/")
        assert resp.status_code == 200


class TestMalformedJSON:
    def test_malformed_json_body_returns_400(self):
        from cauldron_content_api.views import ChangeRequestListView

        user = _make_user(is_superuser=True, username="badjson")
        factory = RequestFactory()
        req = factory.post("/", data=b"{bad json", content_type="application/json")
        req.user = user
        view = ChangeRequestListView.as_view()
        resp = view(req)
        assert resp.status_code == 400
        body = json.loads(resp.content)
        assert body["error"]["code"] == "request.invalid_json"


class TestVersionRequiredHTTPStatus:
    def test_conflict_version_required_maps_to_400_not_409(self):
        from cauldron_content_api.views import _service_error_to_response
        from cauldron_content_operations.results import OperationError

        resp = _service_error_to_response(
            OperationError("conflict.version_required", "missing")
        )
        assert resp.status_code == 400

    def test_conflict_version_maps_to_409(self):
        from cauldron_content_api.views import _service_error_to_response
        from cauldron_content_operations.results import OperationError

        resp = _service_error_to_response(
            OperationError("conflict.version", "stale")
        )
        assert resp.status_code == 409
