"""Django Admin views for content browser, proposal creation, and page authoring."""
from __future__ import annotations

import html
import json
import uuid
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core import signing
from django.http import Http404, HttpRequest, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.views import View
from django.views.generic import RedirectView
from django.utils.decorators import method_decorator

from .forms import ContentProposalForm, PageCreateForm, PageEditForm

_EDIT_TOKEN_SALT = "cauldron.page.edit"
_SUBMIT_TOKEN_SALT = "cauldron.page.submit"
_TOKEN_MAX_AGE = 3600  # 1 hour


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


def _make_submit_token() -> tuple[str, str]:
    """Return (raw_key, signed_token) for a single-use submission idempotency key."""
    raw_key = str(uuid.uuid4())
    token = signing.dumps(raw_key, salt=_SUBMIT_TOKEN_SALT)
    return raw_key, token


def _extract_idempotency_key(token: str) -> str:
    """Return the idempotency key from a signed submission token, or '' on failure."""
    try:
        return signing.loads(token, salt=_SUBMIT_TOKEN_SALT, max_age=_TOKEN_MAX_AGE)
    except (signing.BadSignature, signing.SignatureExpired):
        return ""


def _make_edit_token(item_id: str, collection: str, expected_hash: str) -> str:
    return signing.dumps(
        {"item_id": item_id, "collection": collection, "expected_hash": expected_hash},
        salt=_EDIT_TOKEN_SALT,
    )


def _load_edit_token(token: str) -> dict | None:
    try:
        data = signing.loads(token, salt=_EDIT_TOKEN_SALT, max_age=_TOKEN_MAX_AGE)
        if not isinstance(data, dict):
            return None
        return data
    except (signing.BadSignature, signing.SignatureExpired):
        return None


# ---------------------------------------------------------------------------
# Content Browser
# ---------------------------------------------------------------------------

@method_decorator([
    login_required,
    permission_required("cauldron_content_operations.view_published_content", raise_exception=True),
], name="dispatch")
class ContentBrowserView(View):
    """Browse published and draft content via ContentOperationService."""

    template_name = "cauldron_admin_content/content_browser.html"

    def get(self, request: HttpRequest) -> Any:
        from cauldron_content.pages import PAGE_COLLECTION
        collection = request.GET.get("collection", "")
        requested_drafts = request.GET.get("include_drafts", "").lower() in ("1", "true", "yes")
        has_draft_perm = request.user.has_perm(
            "cauldron_content_operations.view_draft_content"
        )
        include_drafts = requested_drafts and has_draft_perm
        can_propose = request.user.has_perm(
            "cauldron_content_operations.propose_content_changes"
        )
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
                "can_propose": can_propose,
                "is_pages_collection": False,
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
            "can_propose": can_propose,
            "is_pages_collection": collection == PAGE_COLLECTION,
            "page_collection": PAGE_COLLECTION,
            "error": error,
        })


class ContentBrowserRedirectView(RedirectView):
    """Permanent redirect from legacy content-browser/ to canonical content/ route."""
    permanent = True
    pattern_name = "cauldron_admin_content:content-browser"


# ---------------------------------------------------------------------------
# Generic content proposal (advanced / technical interface)
# ---------------------------------------------------------------------------

@method_decorator([
    login_required,
    permission_required("cauldron_content_operations.propose_content_changes", raise_exception=True),
], name="dispatch")
class ContentProposalView(View):
    """Create a content proposal via ContentOperationService. Advanced interface."""

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
                    reverse("cauldron_admin_content:change-request-list")
                )
            else:
                messages.error(request, html.escape(result.error.message))
        except Exception as exc:
            messages.error(request, html.escape(str(exc)[:200]))
        return render(request, self.template_name, {"form": form})


# ---------------------------------------------------------------------------
# Page Create
# ---------------------------------------------------------------------------

@method_decorator([
    login_required,
    permission_required("cauldron_content_operations.propose_content_changes", raise_exception=True),
], name="dispatch")
class PageCreateView(View):
    """Create a new page through the standard page content pipeline."""

    template_name = "cauldron_admin_content/page_form.html"

    def _context(self, form, submission_token):
        return {
            "form": form,
            "submission_token": submission_token,
            "is_edit": False,
            "form_title": "New Page",
            "breadcrumbs": [
                {"label": "Content", "url": reverse("cauldron_admin_content:content-browser")},
                {"label": "New Page", "url": ""},
            ],
        }

    def get(self, request: HttpRequest) -> Any:
        form = PageCreateForm()
        _, submission_token = _make_submit_token()
        return render(request, self.template_name, self._context(form, submission_token))

    def post(self, request: HttpRequest) -> Any:
        form = PageCreateForm(request.POST)
        submission_token = request.POST.get("submission_token", "")
        idempotency_key = _extract_idempotency_key(submission_token)

        if not form.is_valid():
            return render(request, self.template_name, self._context(form, submission_token))

        from cauldron_content.pages import build_page_operation
        item_id = str(uuid.uuid4())
        intended = form.cleaned_data["intended_status"]
        status = "draft" if intended == "draft" else "published"

        operation = build_page_operation(
            kind="create",
            item_id=item_id,
            slug=form.cleaned_data["slug"],
            status=status,
            title=form.cleaned_data["title"],
            body=form.cleaned_data["body"],
            navigation_title=form.cleaned_data["navigation_title"],
            summary=form.cleaned_data["summary"],
            seo_title=form.cleaned_data["seo_title"],
            meta_description=form.cleaned_data["meta_description"],
            canonical_url=form.cleaned_data["canonical_url"],
            robots_index=bool(form.cleaned_data.get("robots_index", True)),
            robots_follow=bool(form.cleaned_data.get("robots_follow", True)),
            social_title=form.cleaned_data["social_title"],
            social_description=form.cleaned_data["social_description"],
            social_image=form.cleaned_data["social_image"],
            template=form.cleaned_data["template"],
        )

        from django.core.exceptions import ImproperlyConfigured
        try:
            service = _get_service()
        except ImproperlyConfigured:
            _handle_config_error(request)
            return render(request, self.template_name, self._context(form, submission_token))

        try:
            result = service.create_change_request(
                user=request.user,
                operations=[operation],
                provider_name="",
                description=form.cleaned_data.get("change_description", ""),
                idempotency_key=idempotency_key,
            )
        except Exception as exc:
            messages.error(request, html.escape(str(exc)[:400]))
            return render(request, self.template_name, self._context(form, submission_token))

        if result.ok:
            messages.success(
                request,
                f"Page proposal created ({result.request_id[:8]}…). "
                "It will be published once the change request is approved and applied.",
            )
            return HttpResponseRedirect(
                reverse(
                    "cauldron_admin_content:change-request-detail",
                    kwargs={"request_id": result.request_id},
                )
            )

        error_msg = result.error.message if result.error else "An unknown error occurred."
        messages.error(request, html.escape(error_msg[:400]))
        return render(request, self.template_name, self._context(form, submission_token))


# ---------------------------------------------------------------------------
# Page Detail
# ---------------------------------------------------------------------------

@method_decorator([
    login_required,
    permission_required("cauldron_content_operations.view_published_content", raise_exception=True),
], name="dispatch")
class PageDetailView(View):
    """Display a page's metadata and Markdown body."""

    template_name = "cauldron_admin_content/page_detail.html"

    def get(self, request: HttpRequest, item_id: str) -> Any:
        from cauldron_content.pages import PAGE_COLLECTION
        has_draft_perm = request.user.has_perm("cauldron_content_operations.view_draft_content")
        can_propose = request.user.has_perm("cauldron_content_operations.propose_content_changes")

        from django.core.exceptions import ImproperlyConfigured
        try:
            service = _get_service()
        except ImproperlyConfigured:
            _handle_config_error(request)
            raise Http404

        item = service.get_item(
            item_id,
            PAGE_COLLECTION,
            user=request.user,
            include_drafts=has_draft_perm,
        )
        if item is None:
            raise Http404

        title = item.data.get("title", item.id)
        return render(request, self.template_name, {
            "item": item,
            "can_propose": can_propose,
            "breadcrumbs": [
                {"label": "Content", "url": reverse("cauldron_admin_content:content-browser")},
                {"label": title, "url": ""},
            ],
        })


# ---------------------------------------------------------------------------
# Page Edit
# ---------------------------------------------------------------------------

@method_decorator([
    login_required,
    permission_required("cauldron_content_operations.propose_content_changes", raise_exception=True),
], name="dispatch")
class PageEditView(View):
    """Edit an existing page through the standard page content pipeline."""

    template_name = "cauldron_admin_content/page_form.html"

    def _load_item(self, request, item_id):
        from cauldron_content.pages import PAGE_COLLECTION
        has_draft_perm = request.user.has_perm("cauldron_content_operations.view_draft_content")
        from django.core.exceptions import ImproperlyConfigured
        try:
            service = _get_service()
        except ImproperlyConfigured:
            _handle_config_error(request)
            return None, None
        item = service.get_item(
            item_id,
            PAGE_COLLECTION,
            user=request.user,
            include_drafts=has_draft_perm,
        )
        return item, service

    def _render_form(self, request, form, item, edit_token, submission_token):
        from cauldron_content.pages import PAGE_COLLECTION
        title = item.data.get("title", item.id)
        return render(request, self.template_name, {
            "form": form,
            "item": item,
            "edit_token": edit_token,
            "submission_token": submission_token,
            "is_edit": True,
            "form_title": f"Edit: {title}",
            "breadcrumbs": [
                {"label": "Content", "url": reverse("cauldron_admin_content:content-browser")},
                {
                    "label": title,
                    "url": reverse("cauldron_admin_content:page-detail", kwargs={"item_id": item.id}),
                },
                {"label": "Edit", "url": ""},
            ],
        })

    def get(self, request: HttpRequest, item_id: str) -> Any:
        item, _ = self._load_item(request, item_id)
        if item is None:
            raise Http404

        data = item.data
        form = PageEditForm(initial={
            "title": data.get("title", ""),
            "navigation_title": data.get("navigation_title", ""),
            "summary": data.get("summary", ""),
            "template": data.get("template", "page"),
            "seo_title": data.get("seo_title", ""),
            "meta_description": data.get("meta_description", ""),
            "canonical_url": data.get("canonical_url", ""),
            "robots_index": data.get("robots_index", True),
            "robots_follow": data.get("robots_follow", True),
            "social_title": data.get("social_title", ""),
            "social_description": data.get("social_description", ""),
            "social_image": data.get("social_image", ""),
            "body": item.body,
            "intended_status": item.status if item.status in ("draft", "published") else "draft",
            "change_description": "",
        })

        from cauldron_content.pages import PAGE_COLLECTION
        edit_token = _make_edit_token(item.id, PAGE_COLLECTION, item.hash)
        _, submission_token = _make_submit_token()
        return self._render_form(request, form, item, edit_token, submission_token)

    def post(self, request: HttpRequest, item_id: str) -> Any:
        from cauldron_content.pages import PAGE_COLLECTION, build_page_operation

        edit_token = request.POST.get("edit_token", "")
        token_data = _load_edit_token(edit_token)
        if token_data is None:
            messages.error(
                request,
                "Your editing session has expired or the token is invalid. "
                "Please reload the page to continue.",
            )
            return HttpResponseRedirect(
                reverse("cauldron_admin_content:page-edit", kwargs={"item_id": item_id})
            )

        if token_data.get("item_id") != item_id or token_data.get("collection") != PAGE_COLLECTION:
            messages.error(request, "Invalid edit token. Please try again.")
            return HttpResponseRedirect(
                reverse("cauldron_admin_content:page-edit", kwargs={"item_id": item_id})
            )

        expected_hash = token_data.get("expected_hash", "")
        submission_token = request.POST.get("submission_token", "")
        idempotency_key = _extract_idempotency_key(submission_token)

        form = PageEditForm(request.POST)

        # Load item to get current slug (read-only in Phase 1)
        item, service = self._load_item(request, item_id)
        if item is None:
            raise Http404

        if not form.is_valid():
            return self._render_form(request, form, item, edit_token, submission_token)

        intended = form.cleaned_data["intended_status"]
        status = "draft" if intended == "draft" else "published"

        operation = build_page_operation(
            kind="update",
            item_id=item.id,
            slug=item.slug,
            status=status,
            title=form.cleaned_data["title"],
            body=form.cleaned_data["body"],
            expected_hash=expected_hash,
            navigation_title=form.cleaned_data["navigation_title"],
            summary=form.cleaned_data["summary"],
            seo_title=form.cleaned_data["seo_title"],
            meta_description=form.cleaned_data["meta_description"],
            canonical_url=form.cleaned_data["canonical_url"],
            robots_index=bool(form.cleaned_data.get("robots_index", True)),
            robots_follow=bool(form.cleaned_data.get("robots_follow", True)),
            social_title=form.cleaned_data["social_title"],
            social_description=form.cleaned_data["social_description"],
            social_image=form.cleaned_data["social_image"],
            template=form.cleaned_data["template"],
        )

        try:
            result = service.create_change_request(
                user=request.user,
                operations=[operation],
                provider_name="",
                description=form.cleaned_data.get("change_description", ""),
                idempotency_key=idempotency_key,
            )
        except Exception as exc:
            messages.error(request, html.escape(str(exc)[:400]))
            return self._render_form(request, form, item, edit_token, submission_token)

        if result.ok:
            messages.success(
                request,
                f"Page update proposal created ({result.request_id[:8]}…). "
                "Changes will go live once the change request is approved and applied.",
            )
            return HttpResponseRedirect(
                reverse(
                    "cauldron_admin_content:change-request-detail",
                    kwargs={"request_id": result.request_id},
                )
            )

        error_msg = result.error.message if result.error else "An unknown error occurred."
        messages.error(request, html.escape(error_msg[:400]))
        return self._render_form(request, form, item, edit_token, submission_token)


# ---------------------------------------------------------------------------
# Change Request views
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Audit views
# ---------------------------------------------------------------------------

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
