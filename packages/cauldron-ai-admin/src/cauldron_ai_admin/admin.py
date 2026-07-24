"""Read-only Django Admin registrations for Admin AI audit records.

Access is gated by custom permissions:

* ``cauldron_ai_admin.view_admin_ai_runs`` — see the run list/detail.
* ``cauldron_ai_admin.view_admin_ai_audit`` — see the invocation list/detail.

Callers with only ``use_admin_ai`` cannot see either page — that is the
right to invoke, not the right to audit.
"""
from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.http import HttpRequest

from .models import AdminAIRun, AdminAIToolInvocation


VIEW_RUNS_PERM = "cauldron_ai_admin.view_admin_ai_runs"
VIEW_AUDIT_PERM = "cauldron_ai_admin.view_admin_ai_audit"


def _user_has_perm(request: HttpRequest, perm: str) -> bool:
    user = getattr(request, "user", None)
    if user is None:
        return False
    try:
        return bool(user.has_perm(perm))
    except Exception:  # pragma: no cover - defensive
        return False


@admin.register(AdminAIRun)
class AdminAIRunAdmin(admin.ModelAdmin):
    list_display = [
        "run_id",
        "actor",
        "status",
        "provider_name",
        "tool_call_count",
        "created_at",
        "completed_at",
    ]
    list_filter = ["status", "provider_name", "created_at"]
    search_fields = ["run_id", "correlation_id", "provider_request_id"]
    ordering = ["-created_at"]
    readonly_fields = [
        "run_id",
        "actor",
        "status",
        "provider_name",
        "provider_request_id",
        "user_request",
        "final_response",
        "correlation_id",
        "tool_call_count",
        "error_code",
        "error_summary",
        "created_at",
        "started_at",
        "completed_at",
        "version",
    ]

    def has_module_permission(self, request: HttpRequest) -> bool:
        return _user_has_perm(request, VIEW_RUNS_PERM)

    def has_view_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return _user_has_perm(request, VIEW_RUNS_PERM)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(AdminAIToolInvocation)
class AdminAIToolInvocationAdmin(admin.ModelAdmin):
    list_display = [
        "invocation_id",
        "run",
        "tool_name",
        "risk_level",
        "status",
        "duration_ms",
        "created_at",
    ]
    list_filter = ["status", "risk_level", "tool_name", "created_at"]
    search_fields = ["invocation_id", "tool_name", "run__run_id"]
    ordering = ["-created_at"]
    readonly_fields = [
        "invocation_id",
        "run",
        "tool_call_id",
        "tool_name",
        "tool_version",
        "owning_module",
        "required_permission",
        "correlation_id",
        "risk_level",
        "status",
        "arguments_hash",
        "argument_summary",
        "result_summary",
        "error_code",
        "duration_ms",
        "created_at",
        "started_at",
        "completed_at",
    ]

    def has_module_permission(self, request: HttpRequest) -> bool:
        return _user_has_perm(request, VIEW_AUDIT_PERM)

    def has_view_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return _user_has_perm(request, VIEW_AUDIT_PERM)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False
