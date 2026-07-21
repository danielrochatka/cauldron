"""Django Admin registrations for content operations."""
from __future__ import annotations

import html
import json
import logging
from typing import Any

from django.contrib import admin, messages
from django.http import HttpRequest, HttpResponseRedirect
from django.urls import reverse
from django.utils.html import format_html

from cauldron_content_operations.models import ContentAuditEvent, ContentChangeRequest

logger = logging.getLogger(__name__)


def _get_service():
    from .service_factory import get_service
    return get_service()


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

    def validate_view(self, request: HttpRequest, request_id: str):
        if request.method != "POST":
            return HttpResponseRedirect(self._detail_url(request_id))
        try:
            service = _get_service()
            result = service.validate_change_request(request_id, user=request.user)
            if result.ok:
                messages.success(request, f"Change request {request_id} validated successfully.")
            else:
                messages.error(request, f"Validation failed: {html.escape(result.error.message)}")
        except Exception as exc:
            messages.error(request, f"Unexpected error: {html.escape(str(exc)[:200])}")
        return HttpResponseRedirect(self._detail_url(request_id))

    def approve_view(self, request: HttpRequest, request_id: str):
        if request.method != "POST":
            return HttpResponseRedirect(self._detail_url(request_id))
        try:
            service = _get_service()
            result = service.approve_change_request(request_id, user=request.user)
            if result.ok:
                messages.success(request, f"Change request {request_id} approved.")
            else:
                messages.error(request, f"Approval failed: {html.escape(result.error.message)}")
        except Exception as exc:
            messages.error(request, f"Unexpected error: {html.escape(str(exc)[:200])}")
        return HttpResponseRedirect(self._detail_url(request_id))

    def reject_view(self, request: HttpRequest, request_id: str):
        if request.method != "POST":
            return HttpResponseRedirect(self._detail_url(request_id))
        reason = request.POST.get("reason", "")
        try:
            service = _get_service()
            result = service.reject_change_request(request_id, user=request.user, reason=reason)
            if result.ok:
                messages.success(request, f"Change request {request_id} rejected.")
            else:
                messages.error(request, f"Rejection failed: {html.escape(result.error.message)}")
        except Exception as exc:
            messages.error(request, f"Unexpected error: {html.escape(str(exc)[:200])}")
        return HttpResponseRedirect(self._detail_url(request_id))

    def apply_view(self, request: HttpRequest, request_id: str):
        if request.method != "POST":
            return HttpResponseRedirect(self._detail_url(request_id))
        try:
            service = _get_service()
            result = service.apply_change_request(request_id, user=request.user)
            if result.ok:
                messages.success(request, f"Change request {request_id} applied.")
            else:
                messages.error(request, f"Apply failed: {html.escape(result.error.message)}")
        except Exception as exc:
            messages.error(request, f"Unexpected error: {html.escape(str(exc)[:200])}")
        return HttpResponseRedirect(self._detail_url(request_id))

    def rollback_view(self, request: HttpRequest, request_id: str):
        if request.method != "POST":
            return HttpResponseRedirect(self._detail_url(request_id))
        try:
            service = _get_service()
            result = service.rollback_change_request(request_id, user=request.user)
            if result.ok:
                messages.success(request, f"Change request {request_id} rolled back.")
            else:
                messages.error(request, f"Rollback failed: {html.escape(result.error.message)}")
        except Exception as exc:
            messages.error(request, f"Unexpected error: {html.escape(str(exc)[:200])}")
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
