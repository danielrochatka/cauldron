"""Django Admin registrations for content operations."""
from __future__ import annotations

import html
import logging
from typing import Any

from django.contrib import admin, messages
from django.http import HttpRequest, HttpResponseRedirect
from django.urls import reverse

from cauldron_content_operations.models import ContentAuditEvent, ContentChangeRequest

logger = logging.getLogger(__name__)


def _get_service():
    from .service_factory import get_service
    return get_service()


def _is_version_error(code: str) -> bool:
    return code in ("conflict.version", "conflict.version_required")


@admin.register(ContentChangeRequest)
class ContentChangeRequestAdmin(admin.ModelAdmin):
    list_display = [
        "request_id",
        "provider_name",
        "lifecycle_state",
        "created_by",
        "created_at",
        "updated_at",
    ]
    list_filter = ["lifecycle_state", "provider_name", "created_at"]
    search_fields = ["request_id", "workspace_changeset_id", "idempotency_key"]
    ordering = ["-created_at"]
    readonly_fields = [
        "request_id",
        "workspace_changeset_id",
        "provider_name",
        "lifecycle_state",
        "request_version",
        "payload_hash",
        "idempotency_key",
        "created_by",
        "validated_by",
        "approved_by",
        "rejected_by",
        "applied_by",
        "rolled_back_by",
        "created_at",
        "updated_at",
        "validated_at",
        "approved_at",
        "rejected_at",
        "applied_at",
        "rolled_back_at",
        "last_error_code",
        "last_error_summary",
        "application_result_meta",
        "reconciliation_meta",
    ]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def get_urls(self):
        from django.urls import path
        urls = super().get_urls()
        custom = [
            path(
                "<str:request_id>/validate/",
                self.admin_site.admin_view(self.validate_view),
                name="cauldron_content_operations_contentchangerequest_validate",
            ),
            path(
                "<str:request_id>/approve/",
                self.admin_site.admin_view(self.approve_view),
                name="cauldron_content_operations_contentchangerequest_approve",
            ),
            path(
                "<str:request_id>/reject/",
                self.admin_site.admin_view(self.reject_view),
                name="cauldron_content_operations_contentchangerequest_reject",
            ),
            path(
                "<str:request_id>/apply/",
                self.admin_site.admin_view(self.apply_view),
                name="cauldron_content_operations_contentchangerequest_apply",
            ),
            path(
                "<str:request_id>/rollback/",
                self.admin_site.admin_view(self.rollback_view),
                name="cauldron_content_operations_contentchangerequest_rollback",
            ),
        ]
        return custom + urls

    def _changelist_url(self) -> str:
        return reverse("admin:cauldron_content_operations_contentchangerequest_changelist")

    def _detail_url(self, request_id: str) -> str:
        try:
            obj = ContentChangeRequest.objects.get(request_id=request_id)
            return reverse(
                "admin:cauldron_content_operations_contentchangerequest_change",
                args=[obj.pk],
            )
        except ContentChangeRequest.DoesNotExist:
            return self._changelist_url()

    def _load_expected_version(self, request: HttpRequest, request_id: str):
        """Parse the submitted ``expected_version`` from the POST body.

        Item 10: the version must be supplied by the client (from the form as
        rendered at load time) — never re-read the latest DB version and use
        it as expected, or optimistic concurrency degenerates to no check at
        all.
        """
        raw = request.POST.get("expected_version", "") if request.method == "POST" else ""
        try:
            ver = int(raw)
        except (TypeError, ValueError):
            messages.error(
                request,
                "Version is required. Reload the page and try again.",
            )
            return None, HttpResponseRedirect(self._detail_url(request_id))
        if ver <= 0:
            messages.error(
                request,
                "Version is required. Reload the page and try again.",
            )
            return None, HttpResponseRedirect(self._detail_url(request_id))
        # Confirm the record exists so we can render a helpful redirect.
        try:
            ContentChangeRequest.objects.get(request_id=request_id)
        except ContentChangeRequest.DoesNotExist:
            messages.error(request, "Change request not found.")
            return None, HttpResponseRedirect(self._changelist_url())
        return ver, None

    def _handle_result(self, request, request_id, result, success_msg, fail_prefix):
        if result.ok:
            messages.success(request, success_msg)
            return
        if result.error and _is_version_error(result.error.code):
            messages.error(
                request,
                "Another user changed this request. Please reload and try again.",
            )
            return
        detail = result.error.message if result.error else "unknown error"
        messages.error(request, f"{fail_prefix}: {html.escape(detail)}")

    def validate_view(self, request: HttpRequest, request_id: str):
        if request.method != "POST":
            return HttpResponseRedirect(self._detail_url(request_id))
        expected_version, redirect = self._load_expected_version(request, request_id)
        if redirect is not None:
            return redirect
        from django.core.exceptions import ImproperlyConfigured
        try:
            try:
                service = _get_service()
            except ImproperlyConfigured:
                logger.exception("Admin service factory misconfiguration")
                messages.error(request, "The content service is not available. Please contact your administrator.")
                return HttpResponseRedirect(self._detail_url(request_id))
            result = service.validate_change_request(
                request_id, user=request.user, expected_version=expected_version
            )
            self._handle_result(
                request,
                request_id,
                result,
                f"Change request {request_id} validated successfully.",
                "Validation failed",
            )
        except Exception:
            logger.exception("Unexpected error in validate_view for %s", request_id)
            messages.error(request, "An unexpected error occurred. Please check server logs.")
        return HttpResponseRedirect(self._detail_url(request_id))

    def approve_view(self, request: HttpRequest, request_id: str):
        if request.method != "POST":
            return HttpResponseRedirect(self._detail_url(request_id))
        expected_version, redirect = self._load_expected_version(request, request_id)
        if redirect is not None:
            return redirect
        from django.core.exceptions import ImproperlyConfigured
        try:
            try:
                service = _get_service()
            except ImproperlyConfigured:
                logger.exception("Admin service factory misconfiguration")
                messages.error(request, "The content service is not available. Please contact your administrator.")
                return HttpResponseRedirect(self._detail_url(request_id))
            result = service.approve_change_request(
                request_id, user=request.user, expected_version=expected_version
            )
            self._handle_result(
                request,
                request_id,
                result,
                f"Change request {request_id} approved.",
                "Approval failed",
            )
        except Exception:
            logger.exception("Unexpected error in approve_view for %s", request_id)
            messages.error(request, "An unexpected error occurred. Please check server logs.")
        return HttpResponseRedirect(self._detail_url(request_id))

    def reject_view(self, request: HttpRequest, request_id: str):
        if request.method != "POST":
            return HttpResponseRedirect(self._detail_url(request_id))
        expected_version, redirect = self._load_expected_version(request, request_id)
        if redirect is not None:
            return redirect
        reason = request.POST.get("reason", "")
        from django.core.exceptions import ImproperlyConfigured
        try:
            try:
                service = _get_service()
            except ImproperlyConfigured:
                logger.exception("Admin service factory misconfiguration")
                messages.error(request, "The content service is not available. Please contact your administrator.")
                return HttpResponseRedirect(self._detail_url(request_id))
            result = service.reject_change_request(
                request_id,
                user=request.user,
                reason=reason,
                expected_version=expected_version,
            )
            self._handle_result(
                request,
                request_id,
                result,
                f"Change request {request_id} rejected.",
                "Rejection failed",
            )
        except Exception:
            logger.exception("Unexpected error in reject_view for %s", request_id)
            messages.error(request, "An unexpected error occurred. Please check server logs.")
        return HttpResponseRedirect(self._detail_url(request_id))

    def apply_view(self, request: HttpRequest, request_id: str):
        if request.method != "POST":
            return HttpResponseRedirect(self._detail_url(request_id))
        expected_version, redirect = self._load_expected_version(request, request_id)
        if redirect is not None:
            return redirect
        from django.core.exceptions import ImproperlyConfigured
        try:
            try:
                service = _get_service()
            except ImproperlyConfigured:
                logger.exception("Admin service factory misconfiguration")
                messages.error(request, "The content service is not available. Please contact your administrator.")
                return HttpResponseRedirect(self._detail_url(request_id))
            result = service.apply_change_request(
                request_id, user=request.user, expected_version=expected_version
            )
            self._handle_result(
                request,
                request_id,
                result,
                f"Change request {request_id} applied.",
                "Apply failed",
            )
        except Exception:
            logger.exception("Unexpected error in apply_view for %s", request_id)
            messages.error(request, "An unexpected error occurred. Please check server logs.")
        return HttpResponseRedirect(self._detail_url(request_id))

    def rollback_view(self, request: HttpRequest, request_id: str):
        if request.method != "POST":
            return HttpResponseRedirect(self._detail_url(request_id))
        expected_version, redirect = self._load_expected_version(request, request_id)
        if redirect is not None:
            return redirect
        from django.core.exceptions import ImproperlyConfigured
        try:
            try:
                service = _get_service()
            except ImproperlyConfigured:
                logger.exception("Admin service factory misconfiguration")
                messages.error(request, "The content service is not available. Please contact your administrator.")
                return HttpResponseRedirect(self._detail_url(request_id))
            result = service.rollback_change_request(
                request_id, user=request.user, expected_version=expected_version
            )
            self._handle_result(
                request,
                request_id,
                result,
                f"Change request {request_id} rolled back.",
                "Rollback failed",
            )
        except Exception:
            logger.exception("Unexpected error in rollback_view for %s", request_id)
            messages.error(request, "An unexpected error occurred. Please check server logs.")
        return HttpResponseRedirect(self._detail_url(request_id))


@admin.register(ContentAuditEvent)
class ContentAuditEventAdmin(admin.ModelAdmin):
    list_display = [
        "event_id",
        "change_request",
        "sequence",
        "event_type",
        "actor",
        "occurred_at",
        "previous_state",
        "resulting_state",
    ]
    list_filter = ["event_type", "occurred_at", "resulting_state"]
    search_fields = ["event_id", "change_request__request_id", "correlation_id"]
    ordering = ["change_request", "sequence"]
    readonly_fields = [
        "event_id",
        "change_request",
        "sequence",
        "event_type",
        "actor",
        "occurred_at",
        "previous_state",
        "resulting_state",
        "provider",
        "detail",
        "correlation_id",
    ]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False
