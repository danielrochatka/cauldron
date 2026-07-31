"""Safe CSS override serving view."""
from __future__ import annotations

import hashlib
from pathlib import Path

from django.http import HttpRequest, HttpResponse
from django.views import View


def get_override_root() -> Path | None:
    from django.conf import settings
    override_dir = getattr(settings, "CAULDRON_UI_OVERRIDES_DIR", None)
    if override_dir is None:
        base_dir = getattr(settings, "BASE_DIR", None)
        if base_dir is None:
            return None
        override_dir = Path(base_dir) / "cauldron-overrides"
    return Path(override_dir)


class CSSOverrideView(View):
    """Serve a site CSS override file safely.

    URL pattern: ``cauldron-overrides/<str:scope>/<path:rel_path>``
    The ``<path:rel_path>`` captures nested paths like ``subdir/file.css``.
    """

    def get(self, request: HttpRequest, scope: str, rel_path: str) -> HttpResponse:
        from .override_store import (
            UIOverrideStore,
            TraversalError, InvalidFileError, InvalidScopeError, FileSizeError,
        )

        root = get_override_root()
        if root is None or not root.is_dir():
            return HttpResponse(b"", content_type="text/css; charset=utf-8", status=200)

        store = UIOverrideStore(root)

        try:
            content = store.read_file(scope, rel_path)
        except FileNotFoundError:
            return HttpResponse(b"", content_type="text/css; charset=utf-8", status=200)
        except (TraversalError, InvalidFileError, InvalidScopeError, FileSizeError):
            return HttpResponse(b"", content_type="text/css; charset=utf-8", status=403)

        encoded = content.encode("utf-8")
        etag = f'"{hashlib.sha256(encoded).hexdigest()[:16]}"'

        if_none_match = request.META.get("HTTP_IF_NONE_MATCH", "")
        if if_none_match == etag:
            return HttpResponse(status=304)

        response = HttpResponse(encoded, content_type="text/css; charset=utf-8")
        response["ETag"] = etag
        response["Cache-Control"] = "private, no-cache"
        return response
