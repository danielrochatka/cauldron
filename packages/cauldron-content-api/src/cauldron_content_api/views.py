"""API views for cauldron_content_api."""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from django.http import HttpRequest, JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from .envelope import error_response, success_response, unexpected_error_response
from .service_factory import get_service

logger = logging.getLogger(__name__)

MAX_REQUEST_BODY_BYTES = 1 * 1024 * 1024  # 1 MB default
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


def _require_authenticated(request: HttpRequest):
    """Return an error response if not authenticated, else None."""
    if not hasattr(request, "user") or not request.user.is_authenticated:
        return error_response("auth.not_authenticated", "Authentication required.", status=401)
    return None


def _parse_json_body(request: HttpRequest):
    """Return (data, error_response) tuple."""
    ct = request.content_type or ""
    if "application/json" not in ct:
        return None, error_response("request.unsupported_content_type", "Content-Type must be application/json.", status=400)
    try:
        content_length = int(request.META.get("CONTENT_LENGTH") or 0)
    except (ValueError, TypeError):
        content_length = 0
    if content_length > MAX_REQUEST_BODY_BYTES:
        return None, error_response("request.body_too_large", "Request body exceeds size limit.", status=400)
    try:
        data = json.loads(request.body)
        return data, None
    except (json.JSONDecodeError, ValueError):
        return None, error_response("request.invalid_json", "Request body must be valid JSON.", status=400)


def _service_error_to_response(error) -> JsonResponse:
    """Map a service OperationError to an HTTP response."""
    code = error.code
    message = error.message
    if code in ("not_found",):
        return error_response(code, message, status=404)
    if code.startswith("conflict.") or code.startswith("lifecycle."):
        return error_response(code, message, status=409)
    if code.startswith("validation."):
        return error_response(code, message, status=422)
    if code.startswith("auth."):
        return error_response(code, message, status=403)
    if code == "approval.self_approval_denied":
        return error_response(code, message, status=403)
    return error_response(code, message, status=400)


def _pagination_params(request: HttpRequest) -> tuple[int, int]:
    try:
        limit = min(int(request.GET.get("limit", DEFAULT_PAGE_SIZE)), MAX_PAGE_SIZE)
        offset = max(int(request.GET.get("offset", 0)), 0)
    except (ValueError, TypeError):
        limit = DEFAULT_PAGE_SIZE
        offset = 0
    return limit, offset


class CollectionsView(View):
    def get(self, request: HttpRequest) -> JsonResponse:
        err = _require_authenticated(request)
        if err:
            return err
        try:
            service = get_service()
            collections = service.list_collections(user=request.user)
            return success_response({"collections": collections})
        except Exception as exc:
            from cauldron_content_operations.service import PermissionDenied
            if isinstance(exc, PermissionDenied):
                return error_response(exc.code, exc.message, status=403)
            return unexpected_error_response(exc)


class CollectionItemsView(View):
    def get(self, request: HttpRequest, collection: str) -> JsonResponse:
        err = _require_authenticated(request)
        if err:
            return err
        include_drafts = request.GET.get("include_drafts", "").lower() in ("1", "true", "yes")
        limit, offset = _pagination_params(request)
        try:
            service = get_service()
            items = service.list_items(collection, user=request.user, include_drafts=include_drafts)
            page = items[offset: offset + limit]
            return success_response(
                {"items": [item.to_dict() for item in page]},
                meta={"total": len(items), "limit": limit, "offset": offset},
            )
        except Exception as exc:
            from cauldron_content_operations.service import PermissionDenied
            if isinstance(exc, PermissionDenied):
                return error_response(exc.code, exc.message, status=403)
            return unexpected_error_response(exc)


class CollectionItemDetailView(View):
    def get(self, request: HttpRequest, collection: str, item_id: str) -> JsonResponse:
        err = _require_authenticated(request)
        if err:
            return err
        include_drafts = request.GET.get("include_drafts", "").lower() in ("1", "true", "yes")
        try:
            service = get_service()
            item = service.get_item(item_id, collection, user=request.user, include_drafts=include_drafts)
            if item is None:
                return error_response("not_found", f"Item {item_id!r} not found in collection {collection!r}.", status=404)
            response = success_response(item.to_dict())
            response["ETag"] = f'"{item.hash}"'
            return response
        except Exception as exc:
            from cauldron_content_operations.service import PermissionDenied
            if isinstance(exc, PermissionDenied):
                return error_response(exc.code, exc.message, status=403)
            return unexpected_error_response(exc)


class ChangeRequestListView(View):
    def get(self, request: HttpRequest) -> JsonResponse:
        err = _require_authenticated(request)
        if err:
            return err
        limit, offset = _pagination_params(request)
        state_filter = request.GET.get("state", "")
        try:
            service = get_service()
            items = service.list_change_requests(
                user=request.user,
                lifecycle_state=state_filter or None,
                limit=limit,
                offset=offset,
            )
            return success_response({"change_requests": [i.to_dict() for i in items]})
        except Exception as exc:
            from cauldron_content_operations.service import PermissionDenied
            if isinstance(exc, PermissionDenied):
                return error_response(exc.code, exc.message, status=403)
            return unexpected_error_response(exc)

    def post(self, request: HttpRequest) -> JsonResponse:
        err = _require_authenticated(request)
        if err:
            return err
        data, parse_err = _parse_json_body(request)
        if parse_err:
            return parse_err
        operations = data.get("operations", [])
        if not isinstance(operations, list):
            return error_response("request.invalid_operations", "operations must be a list.", status=400)
        provider_name = data.get("provider_name", "")
        description = data.get("description", "")
        idempotency_key = data.get("idempotency_key", "")
        try:
            service = get_service()
            result = service.create_change_request(
                user=request.user,
                operations=operations,
                provider_name=provider_name,
                description=description,
                idempotency_key=idempotency_key,
            )
        except Exception as exc:
            from cauldron_content_operations.service import PermissionDenied
            if isinstance(exc, PermissionDenied):
                return error_response(exc.code, exc.message, status=403)
            return unexpected_error_response(exc)

        if not result.ok:
            return _service_error_to_response(result.error)

        status_code = 200 if result.meta.get("idempotent") else 201
        return success_response(result.to_dict(), status=status_code)


class ChangeRequestDetailView(View):
    def get(self, request: HttpRequest, request_id: str) -> JsonResponse:
        err = _require_authenticated(request)
        if err:
            return err
        try:
            service = get_service()
            detail = service.get_change_request(request_id, user=request.user)
            if detail is None:
                return error_response("not_found", f"Change request {request_id!r} not found.", status=404)
            response = success_response(detail.to_dict())
            response["ETag"] = f'"{detail.request_version}"'
            return response
        except Exception as exc:
            from cauldron_content_operations.service import PermissionDenied
            if isinstance(exc, PermissionDenied):
                return error_response(exc.code, exc.message, status=403)
            return unexpected_error_response(exc)


class ChangeRequestPreviewView(View):
    def get(self, request: HttpRequest, request_id: str) -> JsonResponse:
        err = _require_authenticated(request)
        if err:
            return err
        try:
            service = get_service()
            preview = service.get_preview(request_id, user=request.user)
            if preview is None:
                return error_response("not_found", f"Change request {request_id!r} not found.", status=404)
            return success_response(preview.to_dict())
        except Exception as exc:
            from cauldron_content_operations.service import PermissionDenied
            if isinstance(exc, PermissionDenied):
                return error_response(exc.code, exc.message, status=403)
            return unexpected_error_response(exc)


class ChangeRequestAuditView(View):
    def get(self, request: HttpRequest, request_id: str) -> JsonResponse:
        err = _require_authenticated(request)
        if err:
            return err
        try:
            service = get_service()
            events = service.get_audit_history(request_id, user=request.user)
            return success_response({"events": [e.to_dict() for e in events]})
        except Exception as exc:
            from cauldron_content_operations.service import PermissionDenied
            if isinstance(exc, PermissionDenied):
                return error_response(exc.code, exc.message, status=403)
            return unexpected_error_response(exc)


class ChangeRequestValidateView(View):
    def post(self, request: HttpRequest, request_id: str) -> JsonResponse:
        err = _require_authenticated(request)
        if err:
            return err
        data, parse_err = _parse_json_body(request)
        expected_version = 0
        if data and isinstance(data, dict):
            expected_version = int(data.get("expected_version", 0))
        try:
            service = get_service()
            result = service.validate_change_request(request_id, user=request.user, expected_version=expected_version)
        except Exception as exc:
            from cauldron_content_operations.service import PermissionDenied
            if isinstance(exc, PermissionDenied):
                return error_response(exc.code, exc.message, status=403)
            return unexpected_error_response(exc)
        if not result.ok:
            return _service_error_to_response(result.error)
        return success_response(result.to_dict())


class ChangeRequestApproveView(View):
    def post(self, request: HttpRequest, request_id: str) -> JsonResponse:
        err = _require_authenticated(request)
        if err:
            return err
        data, _ = _parse_json_body(request)
        expected_version = int((data or {}).get("expected_version", 0))
        try:
            service = get_service()
            result = service.approve_change_request(request_id, user=request.user, expected_version=expected_version)
        except Exception as exc:
            from cauldron_content_operations.service import PermissionDenied
            if isinstance(exc, PermissionDenied):
                return error_response(exc.code, exc.message, status=403)
            return unexpected_error_response(exc)
        if not result.ok:
            return _service_error_to_response(result.error)
        return success_response(result.to_dict())


class ChangeRequestRejectView(View):
    def post(self, request: HttpRequest, request_id: str) -> JsonResponse:
        err = _require_authenticated(request)
        if err:
            return err
        data, _ = _parse_json_body(request)
        expected_version = int((data or {}).get("expected_version", 0))
        reason = str((data or {}).get("reason", ""))
        try:
            service = get_service()
            result = service.reject_change_request(request_id, user=request.user, reason=reason, expected_version=expected_version)
        except Exception as exc:
            from cauldron_content_operations.service import PermissionDenied
            if isinstance(exc, PermissionDenied):
                return error_response(exc.code, exc.message, status=403)
            return unexpected_error_response(exc)
        if not result.ok:
            return _service_error_to_response(result.error)
        return success_response(result.to_dict())


class ChangeRequestApplyView(View):
    def post(self, request: HttpRequest, request_id: str) -> JsonResponse:
        err = _require_authenticated(request)
        if err:
            return err
        data, _ = _parse_json_body(request)
        expected_version = int((data or {}).get("expected_version", 0))
        try:
            service = get_service()
            result = service.apply_change_request(request_id, user=request.user, expected_version=expected_version)
        except Exception as exc:
            from cauldron_content_operations.service import PermissionDenied
            if isinstance(exc, PermissionDenied):
                return error_response(exc.code, exc.message, status=403)
            return unexpected_error_response(exc)
        if not result.ok:
            return _service_error_to_response(result.error)
        return success_response(result.to_dict())


class ChangeRequestRollbackView(View):
    def post(self, request: HttpRequest, request_id: str) -> JsonResponse:
        err = _require_authenticated(request)
        if err:
            return err
        data, _ = _parse_json_body(request)
        expected_version = int((data or {}).get("expected_version", 0))
        force = bool((data or {}).get("force", False))
        try:
            service = get_service()
            result = service.rollback_change_request(request_id, user=request.user, force=force, expected_version=expected_version)
        except Exception as exc:
            from cauldron_content_operations.service import PermissionDenied
            if isinstance(exc, PermissionDenied):
                return error_response(exc.code, exc.message, status=403)
            return unexpected_error_response(exc)
        if not result.ok:
            return _service_error_to_response(result.error)
        return success_response(result.to_dict())
