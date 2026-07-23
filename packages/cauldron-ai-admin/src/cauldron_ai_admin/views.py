"""Django views for the Admin AI page."""
from __future__ import annotations

import json
import logging
from typing import Any

from django.contrib.auth.decorators import login_required, permission_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
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
        except PermissionError as exc:
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
