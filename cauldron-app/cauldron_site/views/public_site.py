"""Public static-site serving for the Cauldron self-hosted instance."""
from __future__ import annotations

import os
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404, HttpRequest


def _output_root() -> Path | None:
    modules = getattr(settings, "CAULDRON_MODULES", {}) or {}
    root = (modules.get("cauldron.site.astro") or {}).get("output_root", "")
    return Path(root) if root else None


def _safe_resolve(base: Path, *parts: str) -> Path | None:
    """Resolve a path under base. Return None if it escapes base or doesn't exist."""
    try:
        candidate = base.joinpath(*parts).resolve()
        candidate.relative_to(base.resolve())
        if any(p.startswith(".") for p in candidate.parts):
            return None
        return candidate
    except (ValueError, OSError):
        return None


def serve_index(request: HttpRequest):
    """Serve the generated homepage at /."""
    root = _output_root()
    if root is None:
        raise Http404
    path = _safe_resolve(root, "index.html")
    if path is None or not path.is_file():
        raise Http404
    return FileResponse(open(path, "rb"), content_type="text/html; charset=utf-8")


def serve_page(request: HttpRequest, slug: str):
    """Serve a generated page at /<slug>/."""
    # Reject traversal attempts
    if not slug or ".." in slug or slug.startswith("/"):
        raise Http404
    root = _output_root()
    if root is None:
        raise Http404
    path = _safe_resolve(root, slug, "index.html")
    if path is None or not path.is_file():
        raise Http404
    return FileResponse(open(path, "rb"), content_type="text/html; charset=utf-8")
