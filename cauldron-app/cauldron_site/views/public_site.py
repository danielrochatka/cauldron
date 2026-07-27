"""Public static-site serving for the Cauldron self-hosted instance."""
from __future__ import annotations

import mimetypes
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
        relative = candidate.relative_to(base.resolve())
        if any(p.startswith(".") for p in relative.parts):
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


def serve_asset(request: HttpRequest, asset_path: str):
    """Serve a generated nested asset (JS, CSS, images) from output_root.

    Handles paths like _astro/chunk.js or images/hero/banner.png.
    Supports GET and HEAD.
    Rejects dotfiles, traversal, and paths outside output_root.
    """
    if request.method not in ("GET", "HEAD"):
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(["GET", "HEAD"])

    if not asset_path or ".." in asset_path:
        raise Http404

    root = _output_root()
    if root is None:
        raise Http404

    # Split on / and reject any dotfile component
    parts = [p for p in asset_path.split("/") if p]
    if not parts or any(p.startswith(".") for p in parts):
        raise Http404

    path = _safe_resolve(root, *parts)
    if path is None or not path.is_file():
        raise Http404

    content_type, _ = mimetypes.guess_type(str(path))
    response = FileResponse(
        open(path, "rb"),
        content_type=content_type or "application/octet-stream",
    )
    return response
