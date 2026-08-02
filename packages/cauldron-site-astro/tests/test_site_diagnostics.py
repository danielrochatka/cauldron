"""Tests for cauldron_site_astro.site_diagnostics."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PERM_VIEW = "cauldron_content_operations.view_published_content"
_PERM_DRAFT = "cauldron_content_operations.view_draft_content"


def _actor(has_draft_perm=True):
    def has_perm(perm):
        if perm == _PERM_DRAFT:
            return has_draft_perm
        return True
    return SimpleNamespace(has_perm=has_perm)


def _actor_without_draft_perm():
    return _actor(has_draft_perm=False)


def _published_item():
    return SimpleNamespace(status="published", hash="h1")


def _draft_item():
    return SimpleNamespace(status="draft", hash="d1")


def _mock_service(*, published=None, draft=None, first_raises=False, second_raises=False):
    """Service mock whose get_item distinguishes include_drafts kwarg.

    First call (include_drafts=False) returns *published*.
    Second call (include_drafts=True) returns *draft*.
    """
    svc = MagicMock()

    def get_item(item_id, collection, *, user, include_drafts=False):
        if include_drafts:
            if second_raises:
                raise Exception("service error on draft query")
            return draft
        else:
            if first_raises:
                raise Exception("service error")
            return published

    svc.get_item.side_effect = get_item
    return svc


# ---------------------------------------------------------------------------
# check_homepage_content
# ---------------------------------------------------------------------------


def test_homepage_content_service_none():
    from cauldron_site_astro.site_diagnostics import check_homepage_content
    result = check_homepage_content(actor=_actor(), service=None)
    assert result["status"] == "unavailable"
    assert result["ok"] is False


def test_homepage_content_published():
    from cauldron_site_astro.site_diagnostics import check_homepage_content
    svc = _mock_service(published=_published_item())
    result = check_homepage_content(actor=_actor(), service=svc)
    assert result["status"] == "published"
    assert result["ok"] is True
    assert "hash" not in result


def test_homepage_content_missing():
    """Actor with draft perm, no item at all → missing."""
    from cauldron_site_astro.site_diagnostics import check_homepage_content
    svc = _mock_service(published=None, draft=None)
    result = check_homepage_content(actor=_actor(has_draft_perm=True), service=svc)
    assert result["status"] == "missing"
    assert result["ok"] is False
    assert "hash" not in result


def test_homepage_content_not_published_without_draft_perm():
    """Actor without draft perm, nothing published → not_published (no second query)."""
    from cauldron_site_astro.site_diagnostics import check_homepage_content
    svc = _mock_service(published=None)
    result = check_homepage_content(actor=_actor_without_draft_perm(), service=svc)
    assert result["status"] == "not_published"
    assert result["ok"] is False


def test_homepage_content_draft():
    """Actor with draft perm, no published but a draft exists → draft."""
    from cauldron_site_astro.site_diagnostics import check_homepage_content
    svc = _mock_service(published=None, draft=_draft_item())
    result = check_homepage_content(actor=_actor(has_draft_perm=True), service=svc)
    assert result["status"] == "draft"
    assert result["ok"] is False
    assert "hash" not in result


def test_homepage_content_service_raises_primary():
    from cauldron_site_astro.site_diagnostics import check_homepage_content
    svc = _mock_service(first_raises=True)
    result = check_homepage_content(actor=_actor(), service=svc)
    assert result["status"] == "error"
    assert result["ok"] is False


def test_homepage_content_service_raises_on_draft_query():
    """Second query (include_drafts=True) raises → error."""
    from cauldron_site_astro.site_diagnostics import check_homepage_content
    svc = _mock_service(published=None, second_raises=True)
    result = check_homepage_content(actor=_actor(has_draft_perm=True), service=svc)
    assert result["status"] == "error"
    assert result["ok"] is False


def test_homepage_content_get_item_called_with_include_drafts():
    """When actor has draft perm and no published item, second query uses include_drafts=True."""
    from cauldron_site_astro.site_diagnostics import check_homepage_content

    svc = _mock_service(published=None, draft=None)
    check_homepage_content(actor=_actor(has_draft_perm=True), service=svc)

    assert svc.get_item.call_count == 2
    second_kwargs = svc.get_item.call_args_list[1][1]
    assert second_kwargs.get("include_drafts") is True


def test_homepage_content_no_draft_query_without_draft_perm():
    """Actor without draft perm: get_item called only once."""
    from cauldron_site_astro.site_diagnostics import check_homepage_content

    svc = _mock_service(published=None)
    check_homepage_content(actor=_actor_without_draft_perm(), service=svc)

    assert svc.get_item.call_count == 1


# ---------------------------------------------------------------------------
# check_root_artifact
# ---------------------------------------------------------------------------


def test_root_artifact_unconfigured():
    from cauldron_site_astro.site_diagnostics import check_root_artifact
    result = check_root_artifact(output_root=None)
    assert result["status"] == "unconfigured"
    assert result["ok"] is False


def test_root_artifact_missing(tmp_path: Path):
    from cauldron_site_astro.site_diagnostics import check_root_artifact
    result = check_root_artifact(output_root=str(tmp_path))
    assert result["status"] == "missing"
    assert result["ok"] is False


def test_root_artifact_empty(tmp_path: Path):
    from cauldron_site_astro.site_diagnostics import check_root_artifact
    (tmp_path / "index.html").write_text("")
    result = check_root_artifact(output_root=str(tmp_path))
    assert result["status"] == "empty"
    assert result["ok"] is False


def test_root_artifact_ok(tmp_path: Path):
    from cauldron_site_astro.site_diagnostics import check_root_artifact
    (tmp_path / "index.html").write_text("<html><body>Home</body></html>")
    result = check_root_artifact(output_root=str(tmp_path))
    assert result["status"] == "ok"
    assert result["ok"] is True


def test_root_artifact_directory_is_missing(tmp_path: Path):
    """A directory named index.html does not pass is_file()."""
    from cauldron_site_astro.site_diagnostics import check_root_artifact
    (tmp_path / "index.html").mkdir()
    result = check_root_artifact(output_root=str(tmp_path))
    assert result["status"] == "missing"
    assert result["ok"] is False


def test_root_artifact_broken_symlink_is_missing(tmp_path: Path):
    """A broken symlink does not pass is_file()."""
    from cauldron_site_astro.site_diagnostics import check_root_artifact
    link = tmp_path / "index.html"
    link.symlink_to(tmp_path / "nonexistent.html")
    result = check_root_artifact(output_root=str(tmp_path))
    assert result["status"] == "missing"
    assert result["ok"] is False


def test_root_artifact_oserror_returns_error():
    """OSError from is_file() maps to 'error' status; no path appears in result."""
    from cauldron_site_astro.site_diagnostics import check_root_artifact

    mock_index = MagicMock()
    mock_index.is_file.side_effect = OSError("permission denied")
    mock_root = MagicMock()
    mock_root.__truediv__ = MagicMock(return_value=mock_index)

    with patch("cauldron_site_astro.site_diagnostics.Path", return_value=mock_root):
        result = check_root_artifact(output_root="/any/path")

    assert result["status"] == "error"
    assert result["ok"] is False
    assert "path" not in result


def test_root_artifact_stat_oserror_returns_error():
    """OSError from stat() maps to 'error' status."""
    from cauldron_site_astro.site_diagnostics import check_root_artifact

    mock_index = MagicMock()
    mock_index.is_file.return_value = True
    mock_index.stat.side_effect = OSError("permission denied")
    mock_root = MagicMock()
    mock_root.__truediv__ = MagicMock(return_value=mock_index)

    with patch("cauldron_site_astro.site_diagnostics.Path", return_value=mock_root):
        result = check_root_artifact(output_root="/any/path")

    assert result["status"] == "error"
    assert result["ok"] is False
    assert "path" not in result


# ---------------------------------------------------------------------------
# check_root_route
# ---------------------------------------------------------------------------


def _mock_response(status_code=200, content_type="text/html; charset=utf-8"):
    resp = MagicMock()
    resp.status_code = status_code
    resp.get.return_value = content_type
    resp.close.return_value = None
    return resp


def _mock_match(response=None, raises=None, http404=False):
    """URL resolver match whose view returns *response* or raises."""
    from django.http import Http404
    match = MagicMock()
    match.args = ()
    match.kwargs = {}
    if http404:
        match.func.side_effect = Http404()
    elif raises is not None:
        match.func.side_effect = raises
    else:
        match.func.return_value = response
    return match


def test_root_route_ok():
    from cauldron_site_astro.site_diagnostics import check_root_route
    resp = _mock_response(200, "text/html; charset=utf-8")
    match = _mock_match(response=resp)
    with patch("cauldron_site_astro.site_diagnostics._resolve_root", return_value=match):
        result = check_root_route()
    assert result["status"] == "ok"
    assert result["ok"] is True
    assert result["http_status"] == 200


def test_root_route_route_not_found():
    from cauldron_site_astro.site_diagnostics import check_root_route
    with patch("cauldron_site_astro.site_diagnostics._resolve_root", return_value=None):
        result = check_root_route()
    assert result["status"] == "route_not_found"
    assert result["ok"] is False


def test_root_route_not_found_404():
    from cauldron_site_astro.site_diagnostics import check_root_route
    resp = _mock_response(404, "text/html")
    match = _mock_match(response=resp)
    with patch("cauldron_site_astro.site_diagnostics._resolve_root", return_value=match):
        result = check_root_route()
    assert result["status"] == "not_found"
    assert result["ok"] is False
    assert result["http_status"] == 404


def test_root_route_http404_exception():
    from cauldron_site_astro.site_diagnostics import check_root_route
    match = _mock_match(http404=True)
    with patch("cauldron_site_astro.site_diagnostics._resolve_root", return_value=match):
        result = check_root_route()
    assert result["status"] == "not_found"
    assert result["ok"] is False
    assert result["http_status"] == 404


def test_root_route_view_raised():
    from cauldron_site_astro.site_diagnostics import check_root_route
    match = _mock_match(raises=RuntimeError("view error"))
    with patch("cauldron_site_astro.site_diagnostics._resolve_root", return_value=match):
        result = check_root_route()
    assert result["status"] == "view_raised"
    assert result["ok"] is False


def test_root_route_error_status():
    from cauldron_site_astro.site_diagnostics import check_root_route
    resp = _mock_response(500, "text/plain")
    match = _mock_match(response=resp)
    with patch("cauldron_site_astro.site_diagnostics._resolve_root", return_value=match):
        result = check_root_route()
    assert result["status"] == "error"
    assert result["ok"] is False
    assert result["http_status"] == 500


def test_root_route_unreachable():
    from cauldron_site_astro.site_diagnostics import check_root_route
    with patch(
        "cauldron_site_astro.site_diagnostics._resolve_root",
        side_effect=Exception("unexpected"),
    ):
        result = check_root_route()
    assert result["status"] == "unreachable"
    assert result["ok"] is False


def test_root_route_200_non_html_is_error():
    from cauldron_site_astro.site_diagnostics import check_root_route
    resp = _mock_response(200, "application/json")
    match = _mock_match(response=resp)
    with patch("cauldron_site_astro.site_diagnostics._resolve_root", return_value=match):
        result = check_root_route()
    assert result["status"] == "error"
    assert result["ok"] is False


def test_root_route_response_closed_in_finally():
    """Response.close() is called in the finally block after a successful view call."""
    from cauldron_site_astro.site_diagnostics import check_root_route
    resp = _mock_response(200, "text/html")
    match = _mock_match(response=resp)
    with patch("cauldron_site_astro.site_diagnostics._resolve_root", return_value=match):
        check_root_route()
    resp.close.assert_called_once()


def test_resolve_root_does_not_raise():
    """_resolve_root() returns None or a valid ResolverMatch — never raises."""
    from django.urls import ResolverMatch
    from cauldron_site_astro.site_diagnostics import _resolve_root

    result = _resolve_root()
    assert result is None or isinstance(result, ResolverMatch)


# ---------------------------------------------------------------------------
# Real URLconf integration tests (no _resolve_root mock)
# ---------------------------------------------------------------------------

def _root_200_view(request):
    from django.http import HttpResponse
    return HttpResponse("<html><body>Home</body></html>", content_type="text/html")


def _root_404_view(request):
    from django.http import Http404
    raise Http404("not found")


# Module-level urlpatterns that override_settings can point at.
_urlpatterns_200 = [
    __import__("django.urls", fromlist=["path"]).path("", _root_200_view),
]
_urlpatterns_404 = [
    __import__("django.urls", fromlist=["path"]).path("", _root_404_view),
]


def test_check_root_route_real_urlconf_ok():
    """Real URLconf with a 200 text/html root view → status 'ok', no _resolve_root mock."""
    from django.test import override_settings
    from cauldron_site_astro.site_diagnostics import check_root_route

    urlconf_module = type(
        "RootOKConf", (), {"urlpatterns": _urlpatterns_200}
    )

    with override_settings(ROOT_URLCONF=urlconf_module):
        result = check_root_route()

    assert result["status"] == "ok"
    assert result["ok"] is True
    assert result["http_status"] == 200


def test_check_root_route_real_urlconf_http404():
    """Real URLconf whose root view raises Http404 → status 'not_found', not 'route_not_found'."""
    from django.test import override_settings
    from cauldron_site_astro.site_diagnostics import check_root_route

    urlconf_module = type(
        "RootHttp404Conf", (), {"urlpatterns": _urlpatterns_404}
    )

    with override_settings(ROOT_URLCONF=urlconf_module):
        result = check_root_route()

    assert result["status"] == "not_found"
    assert result["ok"] is False
    assert result["http_status"] == 404


# ---------------------------------------------------------------------------
# run_site_diagnostics
# ---------------------------------------------------------------------------


def test_run_site_diagnostics_all_healthy(tmp_path: Path):
    from cauldron_site_astro.site_diagnostics import run_site_diagnostics

    (tmp_path / "index.html").write_text("<html>Home</html>")
    svc = _mock_service(published=_published_item())
    resp = _mock_response(200, "text/html")
    match = _mock_match(response=resp)

    with patch("cauldron_site_astro.site_diagnostics._resolve_root", return_value=match):
        result = run_site_diagnostics(
            actor=_actor(),
            service=svc,
            output_root=str(tmp_path),
        )

    assert result["healthy"] is True
    assert result["checks"]["homepage_content"]["ok"] is True
    assert result["checks"]["root_artifact"]["ok"] is True
    assert result["checks"]["root_route"]["ok"] is True


def test_run_site_diagnostics_homepage_missing_unhealthy(tmp_path: Path):
    from cauldron_site_astro.site_diagnostics import run_site_diagnostics

    (tmp_path / "index.html").write_text("<html>Home</html>")
    svc = _mock_service(published=None, draft=None)
    resp = _mock_response(200, "text/html")
    match = _mock_match(response=resp)

    with patch("cauldron_site_astro.site_diagnostics._resolve_root", return_value=match):
        result = run_site_diagnostics(
            actor=_actor(),
            service=svc,
            output_root=str(tmp_path),
        )

    assert result["healthy"] is False
    assert result["checks"]["homepage_content"]["status"] == "missing"


def test_run_site_diagnostics_artifact_missing_unhealthy(tmp_path: Path):
    from cauldron_site_astro.site_diagnostics import run_site_diagnostics

    svc = _mock_service(published=_published_item())
    resp = _mock_response(200, "text/html")
    match = _mock_match(response=resp)

    with patch("cauldron_site_astro.site_diagnostics._resolve_root", return_value=match):
        result = run_site_diagnostics(
            actor=_actor(),
            service=svc,
            output_root=str(tmp_path),
        )

    assert result["healthy"] is False
    assert result["checks"]["root_artifact"]["status"] == "missing"


def test_run_site_diagnostics_checks_dict_keys():
    from cauldron_site_astro.site_diagnostics import run_site_diagnostics

    svc = _mock_service(published=None)
    with patch("cauldron_site_astro.site_diagnostics._resolve_root", return_value=None):
        result = run_site_diagnostics(
            actor=_actor(),
            service=svc,
            output_root=None,
        )

    assert set(result["checks"].keys()) == {"homepage_content", "root_artifact", "root_route"}
