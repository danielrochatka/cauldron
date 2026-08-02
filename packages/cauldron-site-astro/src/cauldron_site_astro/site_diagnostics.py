"""Structured diagnostics for the public Cauldron site.

Three discrete checks — homepage_content, root_artifact, root_route — each
return a dict with at minimum ``status`` and ``ok`` keys. No filesystem paths
appear in any check result; only status strings and safe metadata.
"""
from __future__ import annotations

from pathlib import Path


def _make_test_client():
    from django.test import Client
    return Client(raise_request_exception=False)


def check_homepage_content(*, actor, service) -> dict:
    """Check whether the homepage item exists and what state it's in.

    Returns a dict with:
      - ``status``: ``"published"`` | ``"draft"`` | ``"missing"``
        | ``"unavailable"`` | ``"error"``
      - ``ok``: ``True`` only when ``status == "published"``
      - ``hash``: current item hash (empty string when item is absent)
    """
    if service is None:
        return {"status": "unavailable", "ok": False, "hash": ""}

    try:
        from cauldron_content.homepage import HOMEPAGE_ITEM_ID, HOMEPAGE_COLLECTION
    except ImportError:
        return {"status": "unavailable", "ok": False, "hash": ""}

    try:
        result = service.get_item(HOMEPAGE_ITEM_ID, HOMEPAGE_COLLECTION, user=actor)
    except Exception:
        return {"status": "error", "ok": False, "hash": ""}

    if result is None:
        return {"status": "missing", "ok": False, "hash": ""}

    try:
        from cauldron_content.contracts import ContentStatus
        published_value = ContentStatus.PUBLISHED.value
    except ImportError:
        published_value = "published"

    item_status = getattr(result, "status", "")
    item_hash = getattr(result, "hash", "") or ""
    if item_status == published_value or item_status == "published":
        return {"status": "published", "ok": True, "hash": item_hash}
    return {"status": "draft", "ok": False, "hash": item_hash}


def check_root_artifact(*, output_root: str | None) -> dict:
    """Check whether the root ``index.html`` artifact exists and is non-empty.

    Returns a dict with:
      - ``status``: ``"ok"`` | ``"empty"`` | ``"missing"`` | ``"unconfigured"``
      - ``ok``: ``True`` only when ``status == "ok"``
    """
    if not output_root:
        return {"status": "unconfigured", "ok": False}

    index = Path(output_root) / "index.html"
    if not index.exists():
        return {"status": "missing", "ok": False}
    if index.stat().st_size == 0:
        return {"status": "empty", "ok": False}
    return {"status": "ok", "ok": True}


def check_root_route() -> dict:
    """Make an in-process Django GET request to ``/`` and verify 200 + text/html.

    Any streaming response is consumed and closed to avoid resource leaks.

    Returns a dict with:
      - ``status``: ``"ok"`` | ``"not_found"`` | ``"error"`` | ``"unreachable"``
      - ``ok``: ``True`` only when ``status == "ok"``
      - ``http_status``: integer HTTP status code (absent on exception)
    """
    try:
        client = _make_test_client()
        response = client.get("/")
        status_code = response.status_code
        content_type = response.get("Content-Type", "")

        # Consume and close any streaming response to free file handles.
        if hasattr(response, "streaming_content"):
            try:
                for _ in response.streaming_content:
                    pass
            except Exception:
                pass
        if hasattr(response, "close"):
            try:
                response.close()
            except Exception:
                pass

        if status_code == 200 and "text/html" in content_type:
            return {"status": "ok", "ok": True, "http_status": status_code}
        if status_code == 404:
            return {"status": "not_found", "ok": False, "http_status": status_code}
        return {"status": "error", "ok": False, "http_status": status_code}
    except Exception:
        return {"status": "unreachable", "ok": False}


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
