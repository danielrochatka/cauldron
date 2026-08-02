"""Structured diagnostics for the public Cauldron site.

Three discrete checks — homepage_content, root_artifact, root_route — each
return a dict with at minimum ``status`` and ``ok`` keys. No filesystem paths,
raw exception text, or item hashes appear in any check result.
"""
from __future__ import annotations

from pathlib import Path

_PERM_VIEW = "cauldron_content_operations.view_published_content"
_PERM_VIEW_DRAFT = "cauldron_content_operations.view_draft_content"


def _resolve_root():
    """Resolve ``'/'`` using Django's URL resolver.

    Returns a :class:`django.urls.ResolverMatch` on success, or ``None`` when
    no URL pattern matches (i.e. ``Resolver404``).
    """
    from django.urls import resolve, Resolver404
    try:
        return resolve("/")
    except Resolver404:
        return None


def check_homepage_content(*, actor, service) -> dict:
    """Check whether the homepage item exists and what state it is in.

    Uses only ``view_published_content`` for the primary query. When no
    published item exists and the actor holds ``view_draft_content``, a second
    query is issued with ``include_drafts=True`` to distinguish ``draft`` from
    ``missing``. Without draft visibility the function returns ``not_published``
    rather than claiming the item is absent.

    No item hash is included in the result.

    Returns a dict with:
      - ``status``: ``"published"`` | ``"draft"`` | ``"missing"``
        | ``"not_published"`` | ``"unavailable"`` | ``"error"``
      - ``ok``: ``True`` only when ``status == "published"``
    """
    if service is None:
        return {"status": "unavailable", "ok": False}

    try:
        from cauldron_content.homepage import HOMEPAGE_ITEM_ID, HOMEPAGE_COLLECTION
    except ImportError:
        return {"status": "unavailable", "ok": False}

    # Primary query: published items only (requires only view_published_content).
    try:
        result = service.get_item(
            HOMEPAGE_ITEM_ID, HOMEPAGE_COLLECTION, user=actor
        )
    except Exception:
        return {"status": "error", "ok": False}

    if result is not None:
        return {"status": "published", "ok": True}

    # Nothing published. Gate the draft fallback on explicit draft permission.
    can_see_drafts = bool(
        getattr(actor, "has_perm", lambda _: False)(_PERM_VIEW_DRAFT)
    )
    if not can_see_drafts:
        return {"status": "not_published", "ok": False}

    try:
        draft_result = service.get_item(
            HOMEPAGE_ITEM_ID, HOMEPAGE_COLLECTION, user=actor, include_drafts=True
        )
    except Exception:
        return {"status": "error", "ok": False}

    if draft_result is not None:
        return {"status": "draft", "ok": False}
    return {"status": "missing", "ok": False}


def check_root_artifact(*, output_root: str | None) -> dict:
    """Check whether the root ``index.html`` artifact is a non-empty regular file.

    Directories and broken symlinks do not pass the ``is_file()`` guard.
    Any ``OSError`` (permission denied, I/O error, etc.) is caught and mapped
    to a structured ``error`` status; the path and raw exception are never
    included in the result.

    Returns a dict with:
      - ``status``: ``"ok"`` | ``"empty"`` | ``"missing"``
        | ``"unconfigured"`` | ``"error"``
      - ``ok``: ``True`` only when ``status == "ok"``
    """
    if not output_root:
        return {"status": "unconfigured", "ok": False}

    try:
        index = Path(output_root) / "index.html"
        if not index.is_file():
            return {"status": "missing", "ok": False}
        if index.stat().st_size == 0:
            return {"status": "empty", "ok": False}
        return {"status": "ok", "ok": True}
    except OSError:
        return {"status": "error", "ok": False}


def check_root_route() -> dict:
    """Resolve ``'/'`` and execute the matched view in-process via ``RequestFactory``.

    Uses Django's URL resolver (not the test client) so the result reflects the
    production URLconf without ``testserver`` hostname side-effects. The response
    is closed in a ``finally`` block. Streaming bodies are *not* consumed — the
    file handle is released via ``close()`` alone.

    Returns a dict with:
      - ``status``: ``"ok"`` | ``"route_not_found"`` | ``"view_raised"``
        | ``"not_found"`` | ``"error"`` | ``"unreachable"``
      - ``ok``: ``True`` only when ``status == "ok"``
      - ``http_status``: integer HTTP status code (absent when no response)
    """
    response = None
    try:
        from django.http import Http404
        from django.test import RequestFactory

        match = _resolve_root()
        if match is None:
            return {"status": "route_not_found", "ok": False}

        request = RequestFactory().get("/")
        try:
            response = match.func(request, *match.args, **match.kwargs)
        except Http404:
            return {"status": "not_found", "ok": False, "http_status": 404}
        except Exception:
            return {"status": "view_raised", "ok": False}

        status_code = response.status_code
        content_type = response.get("Content-Type", "")

        if status_code == 200 and content_type.lower().startswith("text/html"):
            return {"status": "ok", "ok": True, "http_status": status_code}
        if status_code == 404:
            return {"status": "not_found", "ok": False, "http_status": status_code}
        return {"status": "error", "ok": False, "http_status": status_code}
    except Exception:
        return {"status": "unreachable", "ok": False}
    finally:
        if response is not None and hasattr(response, "close"):
            try:
                response.close()
            except Exception:
                pass


def run_site_diagnostics(*, actor, service, output_root: str | None) -> dict:
    """Run all three site diagnostics and aggregate results.

    Returns a dict with:
      - ``healthy``: ``True`` when all three checks passed
      - ``checks``: mapping of check name → check result dict
    """
    homepage = check_homepage_content(actor=actor, service=service)
    artifact = check_root_artifact(output_root=output_root)
    route = check_root_route()

    checks = {
        "homepage_content": homepage,
        "root_artifact": artifact,
        "root_route": route,
    }
    healthy = all(c.get("ok", False) for c in checks.values())
    return {"healthy": healthy, "checks": checks}
