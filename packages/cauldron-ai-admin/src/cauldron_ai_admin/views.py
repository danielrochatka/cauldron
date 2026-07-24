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


logger = logging.getLogger(__name__)


ADMIN_AI_PERMISSION = "cauldron_ai_admin.use_admin_ai"


def _get_service():
    from .service_factory import get_admin_ai_service
    return get_admin_ai_service()


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
        runs = AdminAIRun.objects.filter(actor=request.user).order_by("-created_at")[:50]
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
        run = get_object_or_404(AdminAIRun, run_id=run_id, actor=request.user)
        invocations = list(run.invocations.order_by("created_at"))
        return render(request, self.template_name, {
            "run": run,
            "invocations": invocations,
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

    def get(self, request, request_id):
        from django.shortcuts import get_object_or_404
        from .models import UIStyleChangeRequest
        proposal = get_object_or_404(UIStyleChangeRequest, request_id=request_id)
        audit_events = list(proposal.audit_events.order_by("sequence"))
        return render(request, self.template_name, {
            "proposal": proposal,
            "audit_events": audit_events,
            "can_approve": request.user.has_perm("cauldron_ai_admin.approve_ui_style_changes"),
            "breadcrumbs": [
                {"label": "Style Proposals", "url": reverse("cauldron_ai_admin:style-list")},
                {"label": str(proposal.request_id)[:8] + "…", "url": ""},
            ],
        })

    def post(self, request, request_id):
        """Handle approve/reject actions."""
        from django.shortcuts import get_object_or_404, redirect
        from django.utils import timezone
        from .models import UIStyleChangeRequest, UIStyleAuditEvent
        if not request.user.has_perm("cauldron_ai_admin.approve_ui_style_changes"):
            raise PermissionDenied
        proposal = get_object_or_404(UIStyleChangeRequest, request_id=request_id)
        action = request.POST.get("action", "")
        if action == "approve" and proposal.status == "proposed":
            proposal.status = "approved"
            proposal.reviewed_by = request.user
            proposal.reviewed_at = timezone.now()
            proposal.save(update_fields=["status", "reviewed_by", "reviewed_at"])
            seq = proposal.audit_events.count() + 1
            UIStyleAuditEvent.objects.create(
                change_request=proposal, sequence=seq, event_type="approved",
                actor=request.user, detail={"action": "approved"},
            )
            messages.success(request, "Proposal approved.")
        elif action == "reject" and proposal.status == "proposed":
            proposal.status = "rejected"
            proposal.reviewed_by = request.user
            proposal.reviewed_at = timezone.now()
            proposal.save(update_fields=["status", "reviewed_by", "reviewed_at"])
            seq = proposal.audit_events.count() + 1
            UIStyleAuditEvent.objects.create(
                change_request=proposal, sequence=seq, event_type="rejected",
                actor=request.user, detail={"action": "rejected"},
            )
            messages.success(request, "Proposal rejected.")
        elif action == "apply" and proposal.status == "approved":
            self._apply_proposal(request, proposal)
        return redirect("cauldron_ai_admin:style-detail", request_id=request_id)

    def _apply_proposal(self, request, proposal):
        from django.utils import timezone
        from .models import UIStyleAuditEvent
        from cauldron_django_admin.override_store import UIOverrideStore, HashConflictError, OverrideStoreError
        from pathlib import Path
        from django.conf import settings
        override_dir = getattr(settings, "CAULDRON_UI_OVERRIDES_DIR", None)
        if override_dir is None:
            base_dir = getattr(settings, "BASE_DIR", None)
            override_dir = Path(base_dir) / "cauldron-overrides" if base_dir else None
        if override_dir is None:
            messages.error(request, "Override directory not configured.")
            return
        store = UIOverrideStore(Path(override_dir))
        try:
            new_hash = store.write_file_atomic(
                proposal.target_path,
                proposal.proposed_content,
                expected_hash=proposal.base_hash or None,
            )
            proposal.status = "applied"
            proposal.applied_at = timezone.now()
            proposal.proposed_hash = new_hash
            proposal.save(update_fields=["status", "applied_at", "proposed_hash"])
            seq = proposal.audit_events.count() + 1
            UIStyleAuditEvent.objects.create(
                change_request=proposal, sequence=seq, event_type="applied",
                actor=request.user, detail={"new_hash": new_hash},
            )
            messages.success(request, "Style change applied successfully.")
        except HashConflictError:
            proposal.status = "conflicted"
            proposal.error_code = "HASH_CONFLICT"
            proposal.error_summary = "File was modified since the proposal was created."
            proposal.save(update_fields=["status", "error_code", "error_summary"])
            seq = proposal.audit_events.count() + 1
            UIStyleAuditEvent.objects.create(
                change_request=proposal, sequence=seq, event_type="conflict",
                actor=request.user, detail={"error": "hash_conflict"},
            )
            messages.error(request, "Conflict: the target file was modified. Proposal marked as conflicted.")
        except OverrideStoreError as exc:
            proposal.error_code = "STORE_ERROR"
            proposal.error_summary = str(exc)[:200]
            proposal.save(update_fields=["error_code", "error_summary"])
            seq = proposal.audit_events.count() + 1
            UIStyleAuditEvent.objects.create(
                change_request=proposal, sequence=seq, event_type="failed",
                actor=request.user, detail={"error": str(exc)[:200]},
            )
            messages.error(request, "Failed to apply style change. See audit for details.")


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
