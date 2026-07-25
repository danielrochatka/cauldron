"""Django views for the Admin AI page."""
from __future__ import annotations

import json
import logging
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View

from .models import AdminAIRun
from .style_service import get_style_service


logger = logging.getLogger(__name__)


ADMIN_AI_PERMISSION = "cauldron_ai_admin.use_admin_ai"
MANAGE_AI_SETTINGS_PERMISSION = "cauldron_ai_admin.manage_admin_ai_settings"


def _get_service():
    from .service_factory import get_admin_ai_service
    return get_admin_ai_service()


@method_decorator([
    login_required,
    permission_required(MANAGE_AI_SETTINGS_PERMISSION, raise_exception=True),
], name="dispatch")
class AdminAISettingsView(View):
    """Settings shell for the Admin AI module.

    Phase 1: establishes the stable URL, permission, breadcrumbs, and layout
    that the next AI Admin PR will extend with provider and credential
    configuration.  No API-key fields, no provider selection, no secret
    storage are implemented here.
    """

    template_name = "cauldron_ai_admin/settings.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        return render(request, self.template_name, {
            "provider_name": "fake",
            "provider_status": "Demo provider active",
            "breadcrumbs": [
                {"label": "AI Assistant", "url": reverse("cauldron_ai_admin:ai-page")},
                {"label": "Settings", "url": ""},
            ],
        })


@method_decorator([
    login_required,
    permission_required(ADMIN_AI_PERMISSION, raise_exception=True),
], name="dispatch")
class AdminAIPageView(View):
    """Render the Admin AI console and accept POSTed requests.

    GET returns an HTML page showing:
      * a text area for the natural-language request;
      * a hint listing the tools the current user can invoke;
      * the caller's most recent runs.

    POST is JSON-in / JSON-out. CSRF is required (Django enforces this
    against the default middleware). The view calls
    ``AdminAIService.run()`` and returns a summary of the resulting run.
    """

    template_name = "cauldron_ai_admin/ai_page.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        from .tools import get_tool_registry
        allowed_tools = get_tool_registry().list_for_actor(request.user)
        recent = list(
            AdminAIRun.objects.filter(actor=request.user).order_by("-created_at")[:10]
        )
        return render(request, self.template_name, {
            "allowed_tools": [
                {
                    "name": t.name,
                    "risk_level": t.risk_level.value,
                    "description": t.description,
                }
                for t in allowed_tools
            ],
            "recent_runs": [
                {
                    "run_id": str(r.run_id),
                    "status": r.status,
                    "created_at": r.created_at.isoformat() if r.created_at else "",
                    "user_request": r.user_request[:200],
                }
                for r in recent
            ],
        })

    def post(self, request: HttpRequest) -> HttpResponse:
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        request_text = payload.get("request", "")
        correlation_id = payload.get("correlation_id", "")
        if not isinstance(request_text, str) or not request_text.strip():
            return JsonResponse(
                {"error": "Field 'request' must be a non-empty string."},
                status=400,
            )
        try:
            service = _get_service()
        except Exception:
            logger.exception("Admin AI service is not configured")
            return JsonResponse(
                {"error": "Admin AI is not available. Contact your administrator."},
                status=503,
            )
        try:
            run = service.run(request.user, request_text, correlation_id=correlation_id)
        except (PermissionDenied, PermissionError) as exc:
            return JsonResponse({"error": str(exc)}, status=403)
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        except Exception:
            logger.exception("Admin AI run raised an unexpected exception")
            return JsonResponse(
                {"error": "Admin AI run failed. See server logs."},
                status=500,
            )
        return JsonResponse(_serialize_run(run))


@method_decorator([
    login_required,
    permission_required("cauldron_ai_admin.view_admin_ai_runs", raise_exception=True),
], name="dispatch")
class AdminAIRunListView(View):
    template_name = "cauldron_ai_admin/run_list.html"

    def get(self, request):
        # view_admin_ai_runs is an admin-visibility permission: any user who
        # holds it can see the full run history, not just their own runs.
        runs = AdminAIRun.objects.all().order_by("-created_at")[:100]
        return render(request, self.template_name, {
            "runs": runs,
            "breadcrumbs": [
                {"label": "Admin AI", "url": reverse("cauldron_ai_admin:ai-page")},
                {"label": "Runs", "url": ""},
            ],
        })


@method_decorator([
    login_required,
    permission_required("cauldron_ai_admin.view_admin_ai_runs", raise_exception=True),
], name="dispatch")
class AdminAIRunDetailView(View):
    template_name = "cauldron_ai_admin/run_detail.html"

    def get(self, request, run_id):
        from django.shortcuts import get_object_or_404
        run = get_object_or_404(AdminAIRun, run_id=run_id)
        show_invocations = request.user.has_perm(
            "cauldron_ai_admin.view_admin_ai_audit"
        )
        invocations = (
            list(run.invocations.order_by("created_at"))
            if show_invocations else []
        )
        return render(request, self.template_name, {
            "run": run,
            "invocations": invocations,
            "show_invocations": show_invocations,
            "can_view_audit": show_invocations,
            "breadcrumbs": [
                {"label": "Admin AI", "url": reverse("cauldron_ai_admin:ai-page")},
                {"label": "Runs", "url": reverse("cauldron_ai_admin:run-list")},
                {"label": str(run.run_id)[:8] + "…", "url": ""},
            ],
        })


@method_decorator([
    login_required,
    permission_required("cauldron_ai_admin.view_ui_styles", raise_exception=True),
], name="dispatch")
class UIStyleChangeListView(View):
    template_name = "cauldron_ai_admin/style_list.html"

    def get(self, request):
        from .models import UIStyleChangeRequest
        proposals = UIStyleChangeRequest.objects.all().order_by("-created_at")[:50]
        return render(request, self.template_name, {
            "proposals": proposals,
            "breadcrumbs": [{"label": "Style Proposals", "url": ""}],
        })


@method_decorator([
    login_required,
    permission_required("cauldron_ai_admin.view_ui_styles", raise_exception=True),
], name="dispatch")
class UIStyleChangeDetailView(View):
    template_name = "cauldron_ai_admin/style_detail.html"

    # Maximum bytes of unified-diff text we render into the page.
    _DIFF_LIMIT_BYTES = 32_000

    def get(self, request, request_id):
        import difflib

        from django.shortcuts import get_object_or_404
        from .models import UIStyleChangeRequest

        proposal = get_object_or_404(UIStyleChangeRequest, request_id=request_id)

        can_view_audit = request.user.has_perm(
            "cauldron_ai_admin.view_ui_style_audit"
        )
        audit_events = (
            list(proposal.audit_events.order_by("sequence"))
            if can_view_audit else []
        )

        # Best-effort read of the current on-disk content so we can render
        # a real diff view. Failures fall back to a "new file" summary.
        current_content: str | None = None
        current_label = "new file"
        if proposal.base_exists:
            try:
                from cauldron_django_admin.override_views import _get_override_root
                from cauldron_django_admin.override_store import UIOverrideStore
                root = _get_override_root()
                if root is not None and root.is_dir():
                    store = UIOverrideStore(root)
                    current_content = store.read_file(
                        proposal.scope, proposal.target_path,
                    )
                    current_label = f"{proposal.scope}/{proposal.target_path}"
            except Exception:
                current_content = None

        # Unified diff — bounded so a large proposal cannot blow up the
        # response body.
        diff_lines: list[str] = []
        if current_content is not None:
            diff_lines = list(difflib.unified_diff(
                current_content.splitlines(keepends=True),
                proposal.proposed_content.splitlines(keepends=True),
                fromfile=f"current: {proposal.scope}/{proposal.target_path}",
                tofile=f"proposed: {proposal.scope}/{proposal.target_path}",
                lineterm="",
            ))
        elif not proposal.base_exists:
            diff_lines = list(difflib.unified_diff(
                [],
                proposal.proposed_content.splitlines(keepends=True),
                fromfile="/dev/null",
                tofile=f"new: {proposal.scope}/{proposal.target_path}",
                lineterm="",
            ))
        unified_diff = "".join(diff_lines)[: self._DIFF_LIMIT_BYTES]

        return render(request, self.template_name, {
            "proposal": proposal,
            "audit_events": audit_events,
            "can_approve": request.user.has_perm(
                "cauldron_ai_admin.approve_ui_style_changes"
            ),
            "can_view_audit": can_view_audit,
            "current_content": current_content,
            "current_label": current_label,
            "unified_diff": unified_diff,
            "breadcrumbs": [
                {"label": "Style Proposals", "url": reverse("cauldron_ai_admin:style-list")},
                {"label": str(proposal.request_id)[:8] + "…", "url": ""},
            ],
        })

    def post(self, request, request_id):
        """Handle approve/reject/apply actions."""
        from django.shortcuts import get_object_or_404, redirect
        from .models import UIStyleChangeRequest
        from cauldron_django_admin.override_store import HashConflictError, OverrideStoreError
        if not request.user.has_perm("cauldron_ai_admin.approve_ui_style_changes"):
            raise PermissionDenied
        proposal = get_object_or_404(UIStyleChangeRequest, request_id=request_id)
        action = request.POST.get("action", "")
        service = get_style_service()
        try:
            if action == "approve" and proposal.status == "proposed":
                service.approve(proposal, reviewed_by=request.user)
                messages.success(request, "Proposal approved.")
            elif action == "reject" and proposal.status == "proposed":
                service.reject(proposal, reviewed_by=request.user)
                messages.success(request, "Proposal rejected.")
            elif action == "apply" and proposal.status == "approved":
                try:
                    service.apply(proposal, applied_by=request.user)
                    messages.success(request, "Style change applied successfully.")
                except HashConflictError:
                    messages.error(
                        request,
                        "Conflict: the target file was modified. Proposal marked as conflicted.",
                    )
                except OverrideStoreError:
                    messages.error(request, "Failed to apply style change. See audit for details.")
        except ValueError as exc:
            messages.error(request, str(exc))
        return redirect("cauldron_ai_admin:style-detail", request_id=request_id)


@method_decorator([
    login_required,
    permission_required("cauldron_ai_admin.view_admin_ai_audit", raise_exception=True),
], name="dispatch")
class AdminAIInvocationDetailView(View):
    template_name = "cauldron_ai_admin/invocation_detail.html"

    def get(self, request, run_id, invocation_id):
        from django.shortcuts import get_object_or_404
        from .models import AdminAIToolInvocation
        inv = get_object_or_404(
            AdminAIToolInvocation,
            invocation_id=invocation_id,
            run__run_id=run_id,
        )
        return render(request, self.template_name, {
            "invocation": inv,
            "run": inv.run,
            "breadcrumbs": [
                {"label": "Admin AI", "url": reverse("cauldron_ai_admin:ai-page")},
                {"label": "Runs", "url": reverse("cauldron_ai_admin:run-list")},
                {"label": str(run_id)[:8] + "…", "url": reverse("cauldron_ai_admin:run-detail", kwargs={"run_id": run_id})},
                {"label": str(invocation_id)[:8] + "…", "url": ""},
            ],
        })


def _parse_json_body(request: HttpRequest) -> dict[str, Any]:
    ct = (request.META.get("CONTENT_TYPE") or "").split(";", 1)[0].strip().lower()
    if ct != "application/json":
        raise ValueError("Content-Type must be application/json")
    try:
        return json.loads(request.body.decode("utf-8"))
    except Exception as exc:
        raise ValueError(f"Invalid JSON body: {exc}") from exc


def _serialize_run(run: AdminAIRun) -> dict[str, Any]:
    invocations = list(run.invocations.order_by("created_at"))
    return {
        "run_id": str(run.run_id),
        "status": run.status,
        "final_response": run.final_response,
        "error_code": run.error_code,
        "error_summary": run.error_summary,
        "tool_call_count": run.tool_call_count,
        "tool_invocations": [
            {
                "invocation_id": str(inv.invocation_id),
                "tool_name": inv.tool_name,
                "risk_level": inv.risk_level,
                "status": inv.status,
                "error_code": inv.error_code,
                "duration_ms": inv.duration_ms,
            }
            for inv in invocations
        ],
    }
