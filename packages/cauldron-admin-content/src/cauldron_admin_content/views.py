"""Django Admin views for content browser and proposal creation."""
from __future__ import annotations

import html
import json
from typing import Any

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpRequest, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.views import View
from django.contrib.auth.decorators import login_required
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


@method_decorator([login_required, staff_member_required], name="dispatch")
class ContentBrowserView(View):
    """Browse published and draft content via ContentOperationService."""

    template_name = "cauldron_admin_content/content_browser.html"

    def get(self, request: HttpRequest) -> Any:
        collection = request.GET.get("collection", "")
        include_drafts = request.GET.get("include_drafts", "").lower() in ("1", "true", "yes")
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
            "error": error,
        })


@method_decorator([login_required, staff_member_required], name="dispatch")
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
                return HttpResponseRedirect(
                    reverse("admin:cauldron_content_operations_contentchangerequest_changelist")
                )
            else:
                messages.error(request, html.escape(result.error.message))
        except Exception as exc:
            messages.error(request, html.escape(str(exc)[:200]))
        return render(request, self.template_name, {"form": form})
