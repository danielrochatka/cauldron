"""Django views for serving authenticated preview builds."""
from __future__ import annotations

import mimetypes
from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View


PREVIEW_PERMISSION = "cauldron_content_operations.view_draft_content"
_PERM_PROPOSE = "cauldron_content_operations.propose_content_changes"
_PERM_PUBLISH = "cauldron_content_operations.apply_content_changes"
PREVIEW_BANNER = (
    b'<div style="position:fixed;top:0;left:0;right:0;background:#f59e0b;'
    b'color:#000;padding:6px 12px;font:bold 13px/1.4 sans-serif;'
    b'z-index:999999;text-align:center">PREVIEW - not published</div>'
    b'<div style="height:32px"></div>'
)


@method_decorator([
    login_required,
    permission_required(PREVIEW_PERMISSION, raise_exception=True),
], name="dispatch")
class PreviewServeView(View):
    """Serve a file from a preview build directory with path-traversal protection.

    URL layout::

        /preview/<uuid>/               -> index.html
        /preview/<uuid>/<subpath>      -> that file, or subpath/index.html

    Access requires a Django login *and* the ``view_draft_content`` permission.
    The change set must be in ``draft_ready`` or ``preview_failed`` status;
    published or in-progress change sets are not served here.
    """

    def get(self, request, change_set_id: str, path: str = ""):
        # Deferred imports so tests that don't hit this view don't pay setup cost
        from cauldron_site_astro.models import SiteChangeSet
        from cauldron_site_astro.config import get_site_astro_config

        try:
            cs = SiteChangeSet.objects.get(id=change_set_id)
        except (SiteChangeSet.DoesNotExist, ValueError):
            raise Http404

        if cs.status not in (SiteChangeSet.DRAFT_READY, SiteChangeSet.PREVIEW_FAILED):
            raise Http404

        cfg = get_site_astro_config()
        if not cfg.previews_root or not cs.preview_dir:
            raise Http404

        previews_root = Path(cfg.previews_root).resolve()
        preview_dir = (previews_root / cs.preview_dir).resolve()

        # Path traversal guard: preview_dir must live inside previews_root
        try:
            preview_dir.relative_to(previews_root)
        except ValueError:
            raise Http404

        if not preview_dir.exists() or not preview_dir.is_dir():
            raise Http404

        # Normalise the requested path: "" or "/" -> "index.html"
        requested = path.strip("/") or "index.html"
        candidate = (preview_dir / requested).resolve()

        # Symlink escape guard: resolved candidate must stay in preview_dir
        try:
            candidate.relative_to(preview_dir)
        except ValueError:
            raise Http404

        if candidate.is_dir():
            candidate = candidate / "index.html"
        if not candidate.is_file():
            raise Http404

        content_type, _ = mimetypes.guess_type(str(candidate))
        content_type = content_type or "application/octet-stream"

        if content_type.startswith("text/html"):
            body = candidate.read_bytes()
            lower = body.lower()
            if b"<body" in lower:
                idx = lower.index(b"<body")
                end = body.index(b">", idx) + 1
                body = body[:end] + PREVIEW_BANNER + body[end:]
            else:
                body = PREVIEW_BANNER + body
            return HttpResponse(body, content_type=content_type)

        return FileResponse(open(candidate, "rb"), content_type=content_type)


@method_decorator([
    login_required,
    permission_required(_PERM_PUBLISH, raise_exception=True),
], name="dispatch")
class StylePublicationPrepareView(View):
    """Create a SiteChangeSet preview for an approved pages-scope style proposal.

    Requires the publish permission so that the operator who initiates the
    preview already holds the rights needed to publish.

    GET: read-only redirect — if the proposal already has a reusable
    SiteChangeSet (DRAFT_READY or PUBLISH_FAILED), redirect to the review
    page; otherwise redirect back to the style-detail page.  GET never
    creates a SiteChangeSet.

    POST: check for a reusable SiteChangeSet first (same redirect); if none
    exists, create one with all style commit metadata and redirect to review.
    The CSS source write (UIOverrideStore) is deferred to publish() Step 2.5
    so the atomic pre-promotion lock happens there, not here.
    """

    def get(self, request, request_id: str):
        try:
            from cauldron_ai_admin.models import UIStyleChangeRequest
        except ImportError:
            raise Http404("cauldron-ai-admin is not installed.")

        proposal = get_object_or_404(UIStyleChangeRequest, request_id=request_id)

        if proposal.scope != "pages":
            messages.error(request, "Only pages-scope style proposals use the Astro publication flow.")
            return redirect(reverse("cauldron_ai_admin:style-detail", args=[request_id]))

        if proposal.status != "approved":
            messages.error(request, f"Style proposal must be approved before preview (status: {proposal.status}).")
            return redirect(reverse("cauldron_ai_admin:style-detail", args=[request_id]))

        review_url = self._reusable_review_url(proposal)
        if review_url:
            return redirect(review_url)

        return redirect(reverse("cauldron_ai_admin:style-detail", args=[request_id]))

    def post(self, request, request_id: str):
        try:
            from cauldron_ai_admin.models import UIStyleChangeRequest
        except ImportError:
            raise Http404("cauldron-ai-admin is not installed.")

        proposal = get_object_or_404(UIStyleChangeRequest, request_id=request_id)

        if proposal.scope != "pages":
            messages.error(request, "Only pages-scope style proposals use the Astro publication flow.")
            return redirect(reverse("cauldron_ai_admin:style-detail", args=[request_id]))

        if proposal.status != "approved":
            messages.error(request, f"Style proposal must be approved before preview (status: {proposal.status}).")
            return redirect(reverse("cauldron_ai_admin:style-detail", args=[request_id]))

        review_url = self._reusable_review_url(proposal)
        if review_url:
            return redirect(review_url)

        from cauldron_site_astro.publication_service import get_publication_service
        svc = get_publication_service()
        result = svc.prepare(
            actor=request.user,
            content_request_ids=[],
            staged_theme_css=None,
            style_request_id=str(proposal.request_id),
            style_scope=proposal.scope,
            style_target=proposal.target_path,
            style_proposed_content=proposal.proposed_content,
            style_base_hash=proposal.base_hash or "",
            style_base_exists=bool(proposal.base_exists),
        )

        if result.change_set_id:
            proposal.site_changeset_id = result.change_set_id
            proposal.save(update_fields=["site_changeset_id"])

        if not result.ok:
            messages.error(request, f"Preview build failed: {result.message}")
            return redirect(reverse("cauldron_ai_admin:style-detail", args=[request_id]))

        return redirect(
            reverse("cauldron_admin_content:change-set-review", args=[result.change_set_id])
        )

    @staticmethod
    def _reusable_review_url(proposal) -> str | None:
        """Return a redirect URL if proposal.site_changeset_id points to a reusable CS."""
        cs_id = getattr(proposal, "site_changeset_id", None)
        if not cs_id:
            return None
        try:
            from cauldron_site_astro.models import SiteChangeSet
            cs = SiteChangeSet.objects.get(id=cs_id)
            if cs.status in (SiteChangeSet.DRAFT_READY, SiteChangeSet.PUBLISH_FAILED):
                return reverse("cauldron_admin_content:change-set-review", args=[str(cs.id)])
        except Exception:
            pass
        return None
