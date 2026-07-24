"""Django Admin views for content browser and proposal creation."""
from __future__ import annotations

import html
import json
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.http import HttpRequest, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.views import View
from django.views.generic import RedirectView
from django.utils.decorators import method_decorator

from .forms import ContentProposalForm


def _get_service():
    from .service_factory import get_service
    return get_service()


def _handle_config_error(request):
    """Render a generic error message when the service cannot be built."""
    import logging
    logging.getLogger(__name__).exception("Admin service factory misconfiguration")
    messages.error(
        request,
        "The content service is not available. Please contact your administrator.",
    )


@method_decorator([
    login_required,
    permission_required("cauldron_content_operations.view_published_content", raise_exception=True),
], name="dispatch")
class ContentBrowserView(View):
    """Browse published and draft content via ContentOperationService."""

    template_name = "cauldron_admin_content/content_browser.html"

    def get(self, request: HttpRequest) -> Any:
        collection = request.GET.get("collection", "")
        requested_drafts = request.GET.get("include_drafts", "").lower() in ("1", "true", "yes")
        # view_draft_content gates access to any drafts. Silently ignore
        # ?include_drafts=1 when the caller lacks the permission — surfacing
        # a permission error would leak that drafts exist.
        has_draft_perm = request.user.has_perm(
            "cauldron_content_operations.view_draft_content"
        )
        include_drafts = requested_drafts and has_draft_perm
        from django.core.exceptions import ImproperlyConfigured
        try:
            service = _get_service()
        except ImproperlyConfigured:
            _handle_config_error(request)
            return render(request, self.template_name, {
                "collections": [],
                "selected_collection": "",
                "items": [],
                "include_drafts": False,
                "can_view_drafts": has_draft_perm,
                "error": "Service unavailable",
            })

        collections = []
        items = []
        error = ""

        try:
            collections = service.list_collections(user=request.user)
        except Exception as exc:
            error = html.escape(str(exc)[:200])

        if collection:
            try:
                items_raw = service.list_items(collection, user=request.user, include_drafts=include_drafts)
                items = [item.to_dict() for item in items_raw]
            except Exception as exc:
                error = html.escape(str(exc)[:200])

        return render(request, self.template_name, {
            "collections": collections,
            "selected_collection": collection,
            "items": items,
            "include_drafts": include_drafts,
            "can_view_drafts": has_draft_perm,
            "error": error,
        })


class ContentBrowserRedirectView(RedirectView):
    """Permanent redirect from legacy content-browser/ to canonical content/ route."""
    permanent = True
    pattern_name = "cauldron_admin_content:content-browser"


@method_decorator([
    login_required,
    permission_required("cauldron_content_operations.propose_content_changes", raise_exception=True),
], name="dispatch")
class ContentProposalView(View):
    """Create a content proposal via ContentOperationService."""

    template_name = "cauldron_admin_content/content_proposal.html"

    def get(self, request: HttpRequest) -> Any:
        form = ContentProposalForm()
        return render(request, self.template_name, {"form": form})

    def post(self, request: HttpRequest) -> Any:
        form = ContentProposalForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form})
        operation = form.to_operation()
        provider_name = form.cleaned_data.get("provider_name", "")
        description = form.cleaned_data.get("description", "")
        from django.core.exceptions import ImproperlyConfigured
        try:
            service = _get_service()
        except ImproperlyConfigured:
            _handle_config_error(request)
            return render(request, self.template_name, {"form": form})
        try:
            result = service.create_change_request(
                user=request.user,
                operations=[operation],
                provider_name=provider_name,
                description=description,
            )
            if result.ok:
                messages.success(request, f"Proposal created: {result.request_id}")
                # Redirect to the shell-native change-request list, not the
                # Django admin changelist — the shell owns the operator UI.
                return HttpResponseRedirect(
                    reverse("cauldron_admin_content:change-request-list")
                )
            else:
                messages.error(request, html.escape(result.error.message))
        except Exception as exc:
            messages.error(request, html.escape(str(exc)[:200]))
        return render(request, self.template_name, {"form": form})


@method_decorator([
    login_required,
    permission_required("cauldron_content_operations.view_content_change_requests", raise_exception=True),
], name="dispatch")
class ChangeRequestListView(View):
    template_name = "cauldron_admin_content/change_request_list.html"

    def get(self, request):
        from cauldron_content_operations.models import ContentChangeRequest
        qs = ContentChangeRequest.objects.all().order_by("-created_at")[:50]
        return render(request, self.template_name, {
            "change_requests": qs,
            "breadcrumbs": [
                {"label": "Content", "url": reverse("cauldron_admin_content:content-browser")},
                {"label": "Change Requests", "url": ""},
            ],
        })


@method_decorator([
    login_required,
    permission_required("cauldron_content_operations.view_content_change_requests", raise_exception=True),
], name="dispatch")
class ChangeRequestDetailView(View):
    template_name = "cauldron_admin_content/change_request_detail.html"

    def get(self, request, request_id):
        from django.shortcuts import get_object_or_404
        from cauldron_content_operations.models import ContentChangeRequest, ContentAuditEvent
        cr = get_object_or_404(ContentChangeRequest, request_id=request_id)
        audit_events = list(ContentAuditEvent.objects.filter(change_request=cr).order_by("sequence"))
        return render(request, self.template_name, {
            "cr": cr,
            "audit_events": audit_events,
            "breadcrumbs": [
                {"label": "Content", "url": reverse("cauldron_admin_content:content-browser")},
                {"label": "Change Requests", "url": reverse("cauldron_admin_content:change-request-list")},
                {"label": request_id[:8] + "…", "url": ""},
            ],
        })


@method_decorator([
    login_required,
    permission_required("cauldron_content_operations.view_content_audit", raise_exception=True),
], name="dispatch")
class AuditListView(View):
    template_name = "cauldron_admin_content/audit_list.html"

    def get(self, request):
        from cauldron_content_operations.models import ContentAuditEvent
        events = ContentAuditEvent.objects.all().order_by("-occurred_at")[:100]
        return render(request, self.template_name, {
            "events": events,
            "breadcrumbs": [
                {"label": "Content", "url": reverse("cauldron_admin_content:content-browser")},
                {"label": "Audit", "url": ""},
            ],
        })


@method_decorator([
    login_required,
    permission_required("cauldron_content_operations.view_content_audit", raise_exception=True),
], name="dispatch")
class AuditDetailView(View):
    template_name = "cauldron_admin_content/audit_detail.html"

    def get(self, request, event_id):
        from django.shortcuts import get_object_or_404
        from cauldron_content_operations.models import ContentAuditEvent
        event = get_object_or_404(ContentAuditEvent, event_id=event_id)
        return render(request, self.template_name, {
            "event": event,
            "breadcrumbs": [
                {"label": "Content", "url": reverse("cauldron_admin_content:content-browser")},
                {"label": "Audit", "url": reverse("cauldron_admin_content:audit-list")},
                {"label": event_id[:8] + "…", "url": ""},
            ],
        })
