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

# Schema that PageCreateView/PageEditView handle. Items with other schemas
# are not exposed through the page form to prevent cross-schema data merging.
_PAGE_SCHEMA = "page"


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


def _make_submit_token(item_id: str = "") -> tuple[str, str]:
    """Return (idempotency_key, signed_token).

    For create flows, item_id is embedded in the token so that the same token
    always produces the same item_id, keeping the payload hash stable across
    idempotent retries.
    """
    idempotency_key = str(uuid.uuid4())
    token = signing.dumps(
        {"key": idempotency_key, "item_id": item_id},
        salt=_SUBMIT_TOKEN_SALT,
    )
    return idempotency_key, token


def _extract_submit_token(token: str) -> tuple[str, str]:
    """Return (idempotency_key, item_id) from a signed submission token.

    Returns ('', '') on any failure — callers proceed without idempotency
    protection rather than blocking the user.
    """
    try:
        data = signing.loads(token, salt=_SUBMIT_TOKEN_SALT, max_age=_TOKEN_MAX_AGE)
        if not isinstance(data, dict):
            return "", ""
        return data.get("key", ""), data.get("item_id", "")
    except (signing.BadSignature, signing.SignatureExpired):
        return "", ""


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


def _can_publish(request: HttpRequest, require_approval: bool) -> bool:
    """True when the user has all permissions required for the publish flow.

    Publishing is a proposal that skips the normal review queue, so it requires
    propose + validate (always) and apply when approval is disabled.
    """
    if not request.user.has_perm("cauldron_content_operations.propose_content_changes"):
        return False
    if not request.user.has_perm("cauldron_content_operations.validate_content_changes"):
        return False
    if not require_approval:
        return request.user.has_perm("cauldron_content_operations.apply_content_changes")
    return True


def _redirect_after_proposal(request: HttpRequest, request_id: str) -> HttpResponseRedirect:
    """Redirect to change-request detail when permitted, else content browser.

    Proposal authors hold propose_content_changes, which does not imply
    view_content_change_requests. Redirecting unconditionally to the detail
    view would produce a 403 for authors without the broader permission.
    """
    if request.user.has_perm("cauldron_content_operations.view_content_change_requests"):
        return HttpResponseRedirect(
            reverse(
                "cauldron_admin_content:change-request-detail",
                kwargs={"request_id": request_id},
            )
        )
    return HttpResponseRedirect(reverse("cauldron_admin_content:content-browser"))


def _redirect_after_publish(request: HttpRequest, request_id: str) -> HttpResponseRedirect:
    """After a successful apply, go to content browser if permitted else CR detail."""
    if request.user.has_perm("cauldron_content_operations.view_published_content"):
        return HttpResponseRedirect(reverse("cauldron_admin_content:content-browser"))
    return _redirect_after_proposal(request, request_id)


def _get_publication_service():
    """Return :class:`SiteChangeSetService`, or ``None`` if Site Astro is absent.

    Admin content is optional-dependent on cauldron-site-astro: when it is
    installed we route human publish through the unified SiteChangeSet
    workflow (scoped preview + atomic publish, shared with the AI path);
    when it is missing we fall back to inline validate+apply so admin-content
    remains functional in content-only deployments.
    """
    try:
        from cauldron_site_astro.publication_service import get_publication_service
    except ImportError:
        return None
    try:
        return get_publication_service()
    except Exception:
        return None


def _redirect_to_change_set_review(change_set_id: str) -> HttpResponseRedirect:
    return HttpResponseRedirect(
        reverse(
            "cauldron_admin_content:change-set-review",
            kwargs={"change_set_id": change_set_id},
        )
    )


def _try_route_publish_via_site_change_set(
    request: HttpRequest,
    request_id: str,
) -> HttpResponseRedirect | None:
    """Create a SiteChangeSet + preview and redirect to the review page.

    Returns ``None`` when Site Astro is unavailable so the caller falls back
    to the direct validate+apply path. Returns a redirect on both success
    (to the review page) and preview-build failure (also to the review page
    so the operator can see the failure state and retry).
    """
    pub_service = _get_publication_service()
    if pub_service is None:
        return None

    try:
        prep = pub_service.prepare(
            actor=request.user,
            content_request_ids=[request_id],
            description="",
        )
    except Exception as exc:
        messages.error(request, html.escape(str(exc)[:400]))
        return None

    if not prep.ok and not prep.change_set_id:
        # No change set was even created — surface the error and fall back.
        messages.error(request, html.escape(prep.message[:400]))
        return None

    # Whether the preview built or failed, redirect to the review page so
    # the operator sees the durable state and can retry or abandon.
    if not prep.ok:
        messages.warning(
            request,
            html.escape(f"Preview build failed: {prep.message[:300]}"),
        )
    else:
        messages.success(
            request,
            "Draft ready — review the preview and publish when ready.",
        )
    return _redirect_to_change_set_review(prep.change_set_id)


def _handle_publish_flow(
    request: HttpRequest,
    service,
    request_id: str,
    request_version: int,
    *,
    on_form_error,
    approval_message="Page submitted for review.",
    published_message="Page published successfully.",
):
    """Shared publish flow.

    When Site Astro is available and approval is not required, the publish is
    routed through :class:`SiteChangeSetService` — the same service the AI
    workflow uses — so both human and AI publish get scoped preview builds
    and atomic content+asset promotion.

    When approval IS required, the change request goes through the normal
    review queue (same as before).

    When Site Astro is NOT installed, we fall back to the previous inline
    validate+apply behaviour so content-only deployments keep working.
    """
    from cauldron_content_operations.config import get_operations_config
    cfg = get_operations_config()

    try:
        validate_result = service.validate_change_request(
            request_id, user=request.user, expected_version=request_version,
        )
    except Exception as exc:
        messages.error(request, html.escape(str(exc)[:400]))
        _, fresh_token = _make_submit_token()
        return on_form_error([], fresh_token)

    if not validate_result.ok:
        issues = validate_result.meta.get("validation_issues", [])
        error_msg = validate_result.error.message if validate_result.error else "Validation failed."
        messages.error(request, html.escape(error_msg[:400]))
        _, fresh_token = _make_submit_token()
        return on_form_error(issues, fresh_token)

    if cfg.require_approval:
        messages.success(request, approval_message)
        return _redirect_after_proposal(request, request_id)

    # Preferred path: route through the shared SiteChangeSet workflow when
    # Site Astro is installed. Both human and AI publishes then flow through
    # the same scoped-preview + atomic-publish service.
    redirected = _try_route_publish_via_site_change_set(request, request_id)
    if redirected is not None:
        return redirected

    # Fallback: no Site Astro — apply inline (legacy content-only behaviour).
    try:
        apply_result = service.apply_change_request(
            request_id, user=request.user, expected_version=validate_result.request_version,
        )
    except Exception as exc:
        messages.error(request, html.escape(str(exc)[:400]))
        return _redirect_after_proposal(request, request_id)

    if apply_result.ok:
        messages.success(request, published_message)
        return _redirect_after_publish(request, request_id)

    error_msg = apply_result.error.message if apply_result.error else "Apply failed."
    messages.error(request, html.escape(error_msg[:400]))
    return _redirect_after_proposal(request, request_id)


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
        # Default to 'pages' collection if none specified
        collection = request.GET.get("collection", PAGE_COLLECTION)
        has_draft_perm = request.user.has_perm(
            "cauldron_content_operations.view_draft_content"
        )
        # Editors with view_draft_content always see drafts + published; there
        # is no "Include Drafts" checkbox any more. Authors without draft
        # permission continue to see published items only.
        include_drafts = has_draft_perm
        can_propose = request.user.has_perm(
            "cauldron_content_operations.propose_content_changes"
        )
        from cauldron_content_operations.config import get_operations_config
        cfg = get_operations_config()
        require_approval = cfg.require_approval
        from django.core.exceptions import ImproperlyConfigured
        try:
            service = _get_service()
        except ImproperlyConfigured:
            _handle_config_error(request)
            return render(request, self.template_name, {
                "collections": [],
                "selected_collection": "",
                "items": [],
                "can_view_drafts": has_draft_perm,
                "can_propose": can_propose,
                "can_publish": _can_publish(request, require_approval),
                "is_pages_collection": False,
                "error": "Service unavailable",
            })

        collections = []
        items = []
        error = ""

        try:
            collections = [c.name for c in service.list_collections(user=request.user)]
        except Exception as exc:
            error = html.escape(str(exc)[:200])

        # Auto-load items for the selected collection (always, no Browse submit needed)
        if collection:
            try:
                items_raw = service.list_items(collection, user=request.user, include_drafts=include_drafts)
                items = [item.to_dict() for item in items_raw]
                if collection == PAGE_COLLECTION:
                    from cauldron_content.site import get_public_url
                    for item in items:
                        url = get_public_url(
                            item_id=item["id"],
                            slug=item["slug"],
                            collection=collection,
                        )
                        if url is not None:
                            item["public_url"] = url
            except Exception as exc:
                error = html.escape(str(exc)[:200])

        return render(request, self.template_name, {
            "collections": collections,
            "selected_collection": collection,
            "items": items,
            "can_view_drafts": has_draft_perm,
            "can_propose": can_propose,
            "can_publish": _can_publish(request, require_approval),
            "is_pages_collection": collection == PAGE_COLLECTION,
            "page_collection": PAGE_COLLECTION,
            "page_schema": _PAGE_SCHEMA,
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

    def _context(self, request, form, submission_token, *, validation_issues=None):
        from cauldron_content_operations.config import get_operations_config
        cfg = get_operations_config()
        return {
            "form": form,
            "submission_token": submission_token,
            "is_edit": False,
            "form_title": "New Page",
            "require_approval": cfg.require_approval,
            "can_publish": _can_publish(request, cfg.require_approval),
            "validation_issues": validation_issues or [],
            "breadcrumbs": [
                {"label": "Content", "url": reverse("cauldron_admin_content:content-browser")},
                {"label": "New Page", "url": ""},
            ],
        }

    def get(self, request: HttpRequest) -> Any:
        form = PageCreateForm()
        # Embed item_id in the submission token so that retries of the same
        # signed token produce the same item_id, keeping the payload hash stable
        # for ContentOperationService's idempotency check.
        item_id = str(uuid.uuid4())
        _, submission_token = _make_submit_token(item_id=item_id)
        return render(request, self.template_name, self._context(request, form, submission_token))

    def post(self, request: HttpRequest) -> Any:
        action = request.POST.get("action", "save_draft")
        form = PageCreateForm(request.POST)
        submission_token = request.POST.get("submission_token", "")

        # Extract stable item_id and idempotency key from the signed token.
        idempotency_key, item_id = _extract_submit_token(submission_token)
        if not item_id:
            # Fallback: fresh UUID. Idempotency protection is lost on this path
            # (e.g. tampered token), but the form can still be submitted.
            item_id = str(uuid.uuid4())

        if not form.is_valid():
            return render(request, self.template_name, self._context(request, form, submission_token))

        from cauldron_content.pages import build_page_operation
        status = "published" if action == "publish" else "draft"

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
            return render(request, self.template_name, self._context(request, form, submission_token))

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
            return render(request, self.template_name, self._context(request, form, submission_token))

        if not result.ok:
            error_msg = result.error.message if result.error else "An unknown error occurred."
            messages.error(request, html.escape(error_msg[:400]))
            return render(request, self.template_name, self._context(request, form, submission_token))

        if action == "publish":
            return _handle_publish_flow(
                request, service, result.request_id, result.request_version,
                on_form_error=lambda issues, fresh_token: render(
                    request, self.template_name,
                    self._context(request, form, fresh_token, validation_issues=issues),
                ),
            )

        messages.success(
            request,
            f"Draft saved ({result.request_id[:8]}…).",
        )
        return _redirect_after_proposal(request, result.request_id)


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
        # Offer the Edit action for schema=="page" items and empty-schema items.
        # Empty schema indicates an AI-created page that hasn't been upgraded yet;
        # the UPDATE operation will set schema to "page" on apply.
        can_edit = can_propose and item.schema in (_PAGE_SCHEMA, "")
        from cauldron_content_operations.config import get_operations_config
        cfg = get_operations_config()
        return render(request, self.template_name, {
            "item": item,
            "can_propose": can_propose,
            "can_edit": can_edit,
            "can_publish": _can_publish(request, cfg.require_approval),
            "require_approval": cfg.require_approval,
            "breadcrumbs": [
                {"label": "Content", "url": reverse("cauldron_admin_content:content-browser")},
                {"label": title, "url": ""},
            ],
        })

    def post(self, request: HttpRequest, item_id: str) -> Any:
        """Publish an existing draft page directly from the detail view."""
        from cauldron_content.pages import PAGE_COLLECTION, build_page_operation
        from cauldron_content_operations.config import get_operations_config
        from django.core.exceptions import ImproperlyConfigured

        detail_url = reverse("cauldron_admin_content:page-detail", kwargs={"item_id": item_id})
        action = request.POST.get("action", "")
        if action != "publish":
            return HttpResponseRedirect(detail_url)

        cfg = get_operations_config()
        if not _can_publish(request, cfg.require_approval):
            messages.error(request, "You do not have permission to publish content.")
            return HttpResponseRedirect(detail_url)

        has_draft_perm = request.user.has_perm("cauldron_content_operations.view_draft_content")
        try:
            service = _get_service()
        except ImproperlyConfigured:
            _handle_config_error(request)
            return HttpResponseRedirect(detail_url)

        item = service.get_item(
            item_id, PAGE_COLLECTION, user=request.user, include_drafts=has_draft_perm,
        )
        if item is None:
            raise Http404

        if item.schema not in (_PAGE_SCHEMA, ""):
            raise Http404

        if item.status == "published":
            messages.info(request, "This page is already published.")
            return HttpResponseRedirect(detail_url)

        data = item.data
        operation = build_page_operation(
            kind="update",
            item_id=item.id,
            slug=item.slug,
            status="published",
            title=data.get("title", ""),
            body=item.body,
            expected_hash=item.hash,
            description=data.get("description", ""),
            navigation_title=data.get("navigation_title", ""),
            summary=data.get("summary", ""),
            seo_title=data.get("seo_title", ""),
            meta_description=data.get("meta_description", ""),
            canonical_url=data.get("canonical_url", ""),
            robots_index=bool(data.get("robots_index", True)),
            robots_follow=bool(data.get("robots_follow", True)),
            social_title=data.get("social_title", ""),
            social_description=data.get("social_description", ""),
            social_image=data.get("social_image", ""),
            template=data.get("template", "page"),
        )

        try:
            result = service.create_change_request(
                user=request.user,
                operations=[operation],
                provider_name="",
                description=f"Publish draft: {data.get('title', item.id)}",
            )
        except Exception as exc:
            messages.error(request, html.escape(str(exc)[:400]))
            return HttpResponseRedirect(detail_url)

        if not result.ok:
            error_msg = result.error.message if result.error else "An unknown error occurred."
            messages.error(request, html.escape(error_msg[:400]))
            return HttpResponseRedirect(detail_url)

        return _handle_publish_flow(
            request, service, result.request_id, result.request_version,
            on_form_error=lambda issues, fresh_token: HttpResponseRedirect(detail_url),
        )


# ---------------------------------------------------------------------------
# Page Edit
# ---------------------------------------------------------------------------

@method_decorator([
    login_required,
    permission_required("cauldron_content_operations.propose_content_changes", raise_exception=True),
], name="dispatch")
class PageEditView(View):
    """Edit an existing page through the standard page content pipeline.

    Only items with schema == "page" are accepted. Items using other schemas
    must not be presented through this editor because the page schema uses
    additionalProperties:false and data merging at apply time could produce
    validation failures for retained legacy fields.
    """

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

    def _render_form(self, request, form, item, edit_token, submission_token, *, validation_issues=None):
        from cauldron_content_operations.config import get_operations_config
        cfg = get_operations_config()
        title = item.data.get("title", item.id)
        return render(request, self.template_name, {
            "form": form,
            "item": item,
            "edit_token": edit_token,
            "submission_token": submission_token,
            "is_edit": True,
            "form_title": f"Edit: {title}",
            "require_approval": cfg.require_approval,
            "can_publish": _can_publish(request, cfg.require_approval),
            "validation_issues": validation_issues or [],
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
        from cauldron_content.pages import PAGE_COLLECTION
        item, _ = self._load_item(request, item_id)
        if item is None:
            raise Http404

        # Guard against editing pages with non-page schemas. Merging page-form
        # data into an item that uses a different schema (e.g. "pages") could
        # corrupt existing fields not present in page.schema.json.
        # Empty schema ("") is treated as "page" — AI-generated items have no
        # schema set yet, and the UPDATE operation will upgrade it to "page".
        if item.schema not in (_PAGE_SCHEMA, ""):
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
            "change_description": "",
        })

        edit_token = _make_edit_token(item.id, PAGE_COLLECTION, item.hash)
        _, submission_token = _make_submit_token()
        return self._render_form(request, form, item, edit_token, submission_token)

    def post(self, request: HttpRequest, item_id: str) -> Any:
        from cauldron_content.pages import PAGE_COLLECTION, build_page_operation

        action = request.POST.get("action", "save_draft")
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
        idempotency_key, _ = _extract_submit_token(submission_token)

        form = PageEditForm(request.POST)

        # Load item to get current slug (read-only in Phase 1) and validate schema.
        item, service = self._load_item(request, item_id)
        if item is None:
            raise Http404

        if item.schema not in (_PAGE_SCHEMA, ""):
            raise Http404

        if not form.is_valid():
            return self._render_form(request, form, item, edit_token, submission_token)

        status = "published" if action == "publish" else "draft"

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

        if not result.ok:
            error_msg = result.error.message if result.error else "An unknown error occurred."
            messages.error(request, html.escape(error_msg[:400]))
            return self._render_form(request, form, item, edit_token, submission_token)

        if action == "publish":
            return _handle_publish_flow(
                request, service, result.request_id, result.request_version,
                on_form_error=lambda issues, fresh_token: self._render_form(
                    request, form, item, edit_token, fresh_token,
                    validation_issues=issues,
                ),
            )

        messages.success(
            request,
            f"Draft update saved ({result.request_id[:8]}…).",
        )
        return _redirect_after_proposal(request, result.request_id)


# ---------------------------------------------------------------------------
# Homepage (singleton)
# ---------------------------------------------------------------------------

@method_decorator([
    login_required,
    permission_required("cauldron_content_operations.view_published_content", raise_exception=True),
], name="dispatch")
class HomepageView(View):
    """Combined create/edit view for the Homepage singleton content item."""

    template_name = "cauldron_admin_content/homepage.html"

    def _build_exists(self) -> bool:
        """Return True if the Astro build has produced an index.html."""
        from django.conf import settings
        modules = getattr(settings, "CAULDRON_MODULES", {}) or {}
        output_root = (modules.get("cauldron.site.astro") or {}).get("output_root", "")
        if not output_root:
            return False
        from pathlib import Path
        return (Path(output_root) / "index.html").exists()

    def _render(
        self,
        request: HttpRequest,
        *,
        form,
        item=None,
        edit_token="",
        submission_token="",
        validation_issues=None,
    ) -> Any:
        from cauldron_content_operations.config import get_operations_config
        cfg = get_operations_config()
        can_propose = request.user.has_perm(
            "cauldron_content_operations.propose_content_changes"
        )
        status = item.status if item else "not_created"
        return render(request, self.template_name, {
            "form": form,
            "item": item,
            "status": status,
            "edit_token": edit_token,
            "submission_token": submission_token,
            "is_edit": item is not None,
            "build_exists": self._build_exists(),
            "can_publish": _can_publish(request, cfg.require_approval),
            "require_approval": cfg.require_approval,
            "can_propose": can_propose,
            "validation_issues": validation_issues or [],
            "breadcrumbs": [
                {"label": "Content", "url": reverse("cauldron_admin_content:content-browser")},
                {"label": "Homepage", "url": ""},
            ],
        })

    def _load_homepage(self, request: HttpRequest):
        """Return (item, service) for the homepage singleton, or (None, service) if not found."""
        from cauldron_content.homepage import HOMEPAGE_ITEM_ID, HOMEPAGE_COLLECTION
        from django.core.exceptions import ImproperlyConfigured
        try:
            service = _get_service()
        except ImproperlyConfigured:
            _handle_config_error(request)
            return None, None
        item = service.get_item(
            HOMEPAGE_ITEM_ID,
            HOMEPAGE_COLLECTION,
            user=request.user,
            include_drafts=True,
        )
        return item, service

    def get(self, request: HttpRequest) -> Any:
        item, service = self._load_homepage(request)
        if service is None:
            return self._render(request, form=PageEditForm())

        if item is not None:
            data = item.data
            form = PageEditForm(initial={
                "title": data.get("title", ""),
                "navigation_title": data.get("navigation_title", ""),
                "summary": data.get("summary", ""),
                "template": data.get("template", "homepage"),
                "seo_title": data.get("seo_title", ""),
                "meta_description": data.get("meta_description", ""),
                "canonical_url": data.get("canonical_url", ""),
                "robots_index": data.get("robots_index", True),
                "robots_follow": data.get("robots_follow", True),
                "social_title": data.get("social_title", ""),
                "social_description": data.get("social_description", ""),
                "social_image": data.get("social_image", ""),
                "body": item.body,
                "change_description": "",
            })
            from cauldron_content.homepage import HOMEPAGE_COLLECTION
            edit_token = _make_edit_token(item.id, HOMEPAGE_COLLECTION, item.hash)
            _, submission_token = _make_submit_token()
            return self._render(
                request,
                form=form,
                item=item,
                edit_token=edit_token,
                submission_token=submission_token,
            )

        # Homepage does not exist yet — show create form
        form = PageCreateForm()
        _, submission_token = _make_submit_token()
        return self._render(request, form=form, submission_token=submission_token)

    def post(self, request: HttpRequest) -> Any:
        from cauldron_content.homepage import (
            HOMEPAGE_COLLECTION,
            build_homepage_operation,
        )
        from django.core.exceptions import ImproperlyConfigured

        action = request.POST.get("action", "save_draft")
        homepage_url = reverse("cauldron_admin_content:homepage")

        item, service = self._load_homepage(request)
        if service is None:
            return HttpResponseRedirect(homepage_url)

        status = "published" if action == "publish" else "draft"

        if item is None:
            # ---- CREATE path ----
            form = PageCreateForm(request.POST)
            submission_token = request.POST.get("submission_token", "")
            idempotency_key, _ = _extract_submit_token(submission_token)

            if not form.is_valid():
                return self._render(request, form=form, submission_token=submission_token)

            operation = build_homepage_operation(
                kind="create",
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
                return self._render(request, form=form, submission_token=submission_token)

            if not result.ok:
                error_msg = result.error.message if result.error else "An unknown error occurred."
                messages.error(request, html.escape(error_msg[:400]))
                return self._render(request, form=form, submission_token=submission_token)

        else:
            # ---- UPDATE path ----
            edit_token_str = request.POST.get("edit_token", "")
            token_data = _load_edit_token(edit_token_str)
            if token_data is None:
                messages.error(
                    request,
                    "Your editing session has expired or the token is invalid. "
                    "Please reload the page to continue.",
                )
                return HttpResponseRedirect(homepage_url)

            if (
                token_data.get("item_id") != item.id
                or token_data.get("collection") != HOMEPAGE_COLLECTION
            ):
                messages.error(request, "Invalid edit token. Please try again.")
                return HttpResponseRedirect(homepage_url)

            expected_hash = token_data.get("expected_hash", "")
            submission_token = request.POST.get("submission_token", "")
            idempotency_key, _ = _extract_submit_token(submission_token)

            form = PageEditForm(request.POST)
            if not form.is_valid():
                edit_token_str = _make_edit_token(item.id, HOMEPAGE_COLLECTION, item.hash)
                return self._render(
                    request,
                    form=form,
                    item=item,
                    edit_token=edit_token_str,
                    submission_token=submission_token,
                )

            operation = build_homepage_operation(
                kind="update",
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
                edit_token_str = _make_edit_token(item.id, HOMEPAGE_COLLECTION, item.hash)
                return self._render(
                    request,
                    form=form,
                    item=item,
                    edit_token=edit_token_str,
                    submission_token=submission_token,
                )

            if not result.ok:
                error_msg = result.error.message if result.error else "An unknown error occurred."
                messages.error(request, html.escape(error_msg[:400]))
                edit_token_str = _make_edit_token(item.id, HOMEPAGE_COLLECTION, item.hash)
                return self._render(
                    request,
                    form=form,
                    item=item,
                    edit_token=edit_token_str,
                    submission_token=submission_token,
                )

        # ---- Shared publish/draft redirect ----
        if action == "publish":
            def _form_error(issues, fresh_token):
                if item is None:
                    return self._render(
                        request,
                        form=form,
                        submission_token=fresh_token,
                        validation_issues=issues,
                    )
                edit_token_str = _make_edit_token(item.id, HOMEPAGE_COLLECTION, item.hash)
                return self._render(
                    request,
                    form=form,
                    item=item,
                    edit_token=edit_token_str,
                    submission_token=fresh_token,
                    validation_issues=issues,
                )

            return _handle_publish_flow(
                request, service, result.request_id, result.request_version,
                on_form_error=_form_error,
                approval_message="Homepage submitted for review.",
                published_message="Homepage queued for publishing.",
            )

        messages.success(request, "Homepage draft saved.")
        return HttpResponseRedirect(homepage_url)


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


_TERMINAL_STATES = frozenset({"applied", "rejected", "rolled_back"})

_ACTION_PERMISSIONS = {
    "validate": "cauldron_content_operations.validate_content_changes",
    "approve": "cauldron_content_operations.approve_content_changes",
    "reject": "cauldron_content_operations.reject_content_changes",
    "apply": "cauldron_content_operations.apply_content_changes",
}

_VALID_ACTIONS_BY_STATE = {
    "proposed": ("validate", "reject", "publish"),
    "approved": ("apply",),
    "apply_failed": ("apply",),
}

_SUCCESS_LABELS = {
    "validate": "validated",
    "approve": "approved",
    "reject": "rejected",
    "apply": "applied",
}


def _valid_actions_for_state(state: str, require_approval: bool) -> tuple[str, ...]:
    if state == "validated":
        return ("approve", "reject") if require_approval else ("apply", "reject")
    return _VALID_ACTIONS_BY_STATE.get(state, ())


@method_decorator([
    login_required,
    permission_required("cauldron_content_operations.view_content_change_requests", raise_exception=True),
], name="dispatch")
class ChangeRequestDetailView(View):
    template_name = "cauldron_admin_content/change_request_detail.html"

    def _build_context(self, request, cr, audit_events, *, validation_issues=None):
        from cauldron_content_operations.config import get_operations_config

        try:
            service = _get_service()
        except Exception:
            service = None

        previews = None
        if service is not None:
            try:
                changeset = service.get_preview(cr.request_id, user=request.user)
                if changeset is not None:
                    previews = changeset.operations
            except Exception:
                previews = None

        state = cr.lifecycle_state
        cfg = get_operations_config()
        valid_actions = _valid_actions_for_state(state, cfg.require_approval)

        return {
            "cr": cr,
            "audit_events": audit_events,
            "previews": previews,
            "valid_actions": valid_actions,
            "is_terminal": state in _TERMINAL_STATES,
            "require_approval": cfg.require_approval,
            "can_validate": request.user.has_perm(_ACTION_PERMISSIONS["validate"]),
            "can_approve": request.user.has_perm(_ACTION_PERMISSIONS["approve"]),
            "can_reject": request.user.has_perm(_ACTION_PERMISSIONS["reject"]),
            "can_apply": request.user.has_perm(_ACTION_PERMISSIONS["apply"]),
            "can_publish": _can_publish(request, cfg.require_approval),
            "validation_issues": validation_issues or [],
            "breadcrumbs": [
                {"label": "Content", "url": reverse("cauldron_admin_content:content-browser")},
                {"label": "Change Requests", "url": reverse("cauldron_admin_content:change-request-list")},
                {"label": cr.request_id[:8] + "…", "url": ""},
            ],
        }

    def get(self, request, request_id):
        from django.shortcuts import get_object_or_404
        from cauldron_content_operations.models import ContentChangeRequest, ContentAuditEvent
        cr = get_object_or_404(ContentChangeRequest, request_id=request_id)
        audit_events = list(ContentAuditEvent.objects.filter(change_request=cr).order_by("sequence"))
        return render(request, self.template_name, self._build_context(request, cr, audit_events))

    def post(self, request, request_id):
        from django.shortcuts import get_object_or_404
        from cauldron_content_operations.models import ContentChangeRequest, ContentAuditEvent

        cr = get_object_or_404(ContentChangeRequest, request_id=request_id)
        action = request.POST.get("action", "")
        detail_url = reverse("cauldron_admin_content:change-request-detail", kwargs={"request_id": request_id})

        from cauldron_content_operations.config import get_operations_config
        cfg = get_operations_config()

        valid_actions = _valid_actions_for_state(cr.lifecycle_state, cfg.require_approval)
        if action not in valid_actions:
            messages.error(request, f"Action '{action}' is not allowed in state '{cr.lifecycle_state}'.")
            return HttpResponseRedirect(detail_url)

        try:
            expected_version = int(request.POST.get("expected_version", "0"))
        except (ValueError, TypeError):
            expected_version = 0

        try:
            service = _get_service()
        except Exception:
            service = None

        if service is None:
            _handle_config_error(request)
            return HttpResponseRedirect(detail_url)

        if action == "publish":
            if not _can_publish(request, cfg.require_approval):
                messages.error(request, "You do not have permission to perform this action.")
                return HttpResponseRedirect(detail_url)
            try:
                validate_result = service.validate_change_request(
                    request_id, user=request.user, expected_version=expected_version,
                )
            except Exception as exc:
                messages.error(request, html.escape(str(exc)[:400]))
                return HttpResponseRedirect(detail_url)
            if not validate_result.ok:
                issues = validate_result.meta.get("validation_issues", [])
                error_msg = validate_result.error.message if validate_result.error else "Validation failed."
                messages.error(request, html.escape(error_msg[:400]))
                cr.refresh_from_db()
                audit_events = list(ContentAuditEvent.objects.filter(change_request=cr).order_by("sequence"))
                return render(request, self.template_name, self._build_context(
                    request, cr, audit_events, validation_issues=issues,
                ))
            if cfg.require_approval:
                messages.success(request, "Change request submitted for review.")
                return HttpResponseRedirect(detail_url)
            try:
                apply_result = service.apply_change_request(
                    request_id, user=request.user, expected_version=validate_result.request_version,
                )
            except Exception as exc:
                messages.error(request, html.escape(str(exc)[:400]))
                return HttpResponseRedirect(detail_url)
            if apply_result.ok:
                messages.success(request, "Change request published successfully.")
            else:
                error_msg = apply_result.error.message if apply_result.error else "An unknown error occurred."
                messages.error(request, html.escape(error_msg[:400]))
            return HttpResponseRedirect(detail_url)

        if action not in _ACTION_PERMISSIONS:
            messages.error(request, "Unknown action.")
            return HttpResponseRedirect(detail_url)

        required_perm = _ACTION_PERMISSIONS[action]
        if not request.user.has_perm(required_perm):
            messages.error(request, "You do not have permission to perform this action.")
            return HttpResponseRedirect(detail_url)

        if action == "validate":
            result = service.validate_change_request(request_id, user=request.user, expected_version=expected_version)
        elif action == "approve":
            result = service.approve_change_request(request_id, user=request.user, expected_version=expected_version)
        elif action == "reject":
            reason = request.POST.get("rejection_reason", "").strip()[:500]
            result = service.reject_change_request(request_id, user=request.user, reason=reason, expected_version=expected_version)
        elif action == "apply":
            result = service.apply_change_request(request_id, user=request.user, expected_version=expected_version)

        if result.ok:
            messages.success(request, f"Change request {_SUCCESS_LABELS[action]} successfully.")
        else:
            error_msg = result.error.message if result.error else "An unknown error occurred."
            messages.error(request, html.escape(error_msg[:400]))

        return HttpResponseRedirect(detail_url)


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


# ---------------------------------------------------------------------------
# SiteChangeSet review + publish (only active when cauldron.site.astro is installed)
# ---------------------------------------------------------------------------


@method_decorator([
    login_required,
    permission_required(
        "cauldron_content_operations.view_content_change_requests",
        raise_exception=True,
    ),
], name="dispatch")
class ChangeSetReviewView(View):
    """Review a :class:`SiteChangeSet` preview and publish it.

    GET renders the review page (preview link, status, build errors).
    POST with action=publish triggers the atomic publish via the shared
    :class:`SiteChangeSetService`. All state transitions are durable so
    a preview_failed or publish_failed change set can be retried.
    """

    template_name = "cauldron_admin_content/change_set_review.html"

    def _breadcrumbs(self, change_set_id: str):
        return [
            {"label": "Content", "url": reverse("cauldron_admin_content:content-browser")},
            {"label": f"Change set {change_set_id[:8]}…", "url": ""},
        ]

    def _can_publish_site_change_set(self, request: HttpRequest) -> bool:
        return request.user.has_perm(
            "cauldron_content_operations.apply_content_changes"
        )

    def _render(
        self,
        request: HttpRequest,
        *,
        change_set_id: str,
        status: str = "",
        preview_url: str = "",
        created_at: str = "",
        content_request_ids=None,
        error_message: str = "",
    ):
        return render(request, self.template_name, {
            "change_set_id": change_set_id,
            "status": status,
            "preview_url": preview_url,
            "created_at": created_at,
            "content_request_ids": content_request_ids or [],
            "error_message": error_message,
            "can_publish": self._can_publish_site_change_set(request),
            "breadcrumbs": self._breadcrumbs(change_set_id),
        })

    def _inspect(self, change_set_id: str):
        pub_service = _get_publication_service()
        if pub_service is None:
            return None
        try:
            return pub_service.inspect(change_set_id)
        except Exception:
            return None

    def get(self, request: HttpRequest, change_set_id: str):
        inspect_result = self._inspect(change_set_id)
        if inspect_result is None or not inspect_result.ok:
            raise Http404
        return self._render(
            request,
            change_set_id=inspect_result.change_set_id,
            status=inspect_result.status,
            preview_url=inspect_result.preview_url,
            created_at=inspect_result.created_at,
            content_request_ids=inspect_result.content_request_ids,
            error_message=(inspect_result.publish_build_result or {}).get("error", ""),
        )

    def post(self, request: HttpRequest, change_set_id: str):
        action = request.POST.get("action", "")
        review_url = reverse(
            "cauldron_admin_content:change-set-review",
            kwargs={"change_set_id": change_set_id},
        )

        if action != "publish":
            return HttpResponseRedirect(review_url)

        if not self._can_publish_site_change_set(request):
            messages.error(request, "You do not have permission to publish content.")
            return HttpResponseRedirect(review_url)

        pub_service = _get_publication_service()
        if pub_service is None:
            messages.error(
                request,
                "Site publication service is not available.",
            )
            return HttpResponseRedirect(review_url)

        try:
            pub_result = pub_service.publish(
                actor=request.user,
                change_set_id=change_set_id,
            )
        except Exception as exc:
            messages.error(request, html.escape(str(exc)[:400]))
            return HttpResponseRedirect(review_url)

        if pub_result.ok:
            messages.success(request, "Change set published successfully.")
            if request.user.has_perm("cauldron_content_operations.view_published_content"):
                return HttpResponseRedirect(
                    reverse("cauldron_admin_content:content-browser")
                )
            return HttpResponseRedirect(review_url)

        messages.error(request, html.escape(pub_result.message[:400]))
        return HttpResponseRedirect(review_url)
