"""Django views for serving authenticated preview builds."""
from __future__ import annotations

import mimetypes
from pathlib import Path

from django.contrib.auth.decorators import login_required, permission_required
from django.http import FileResponse, Http404, HttpResponse
from django.utils.decorators import method_decorator
from django.views import View


PREVIEW_PERMISSION = "cauldron_content_operations.view_draft_content"
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
