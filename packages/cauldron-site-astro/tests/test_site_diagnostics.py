"""Tests for cauldron_site_astro.site_diagnostics."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _actor():
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user, _ = User.objects.get_or_create(username="diag-test-user")
    return user


def _published_item(item_hash="abc123"):
    return SimpleNamespace(status="published", hash=item_hash)


def _draft_item(item_hash="def456"):
    return SimpleNamespace(status="draft", hash=item_hash)


def _mock_service(item=None, get_item_raises=False):
    svc = MagicMock()
    if get_item_raises:
        svc.get_item.side_effect = Exception("service error")
    else:
        svc.get_item.return_value = item
    return svc


# ---------------------------------------------------------------------------
# check_homepage_content
# ---------------------------------------------------------------------------


def test_homepage_content_service_none():
    from cauldron_site_astro.site_diagnostics import check_homepage_content
    result = check_homepage_content(actor=_actor(), service=None)
    assert result["status"] == "unavailable"
    assert result["ok"] is False


def test_homepage_content_missing():
    from cauldron_site_astro.site_diagnostics import check_homepage_content
    svc = _mock_service(item=None)
    result = check_homepage_content(actor=_actor(), service=svc)
    assert result["status"] == "missing"
    assert result["ok"] is False
    assert result["hash"] == ""


def test_homepage_content_published():
    from cauldron_site_astro.site_diagnostics import check_homepage_content
    svc = _mock_service(item=_published_item("h1"))
    result = check_homepage_content(actor=_actor(), service=svc)
    assert result["status"] == "published"
    assert result["ok"] is True
    assert result["hash"] == "h1"


def test_homepage_content_draft():
    from cauldron_site_astro.site_diagnostics import check_homepage_content
    svc = _mock_service(item=_draft_item("d1"))
    result = check_homepage_content(actor=_actor(), service=svc)
    assert result["status"] == "draft"
    assert result["ok"] is False
    assert result["hash"] == "d1"


def test_homepage_content_service_raises():
    from cauldron_site_astro.site_diagnostics import check_homepage_content
    svc = _mock_service(get_item_raises=True)
    result = check_homepage_content(actor=_actor(), service=svc)
    assert result["status"] == "error"
    assert result["ok"] is False


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


# ---------------------------------------------------------------------------
# check_root_route
# ---------------------------------------------------------------------------


def _mock_response(status_code=200, content_type="text/html; charset=utf-8", streaming=True):
    resp = MagicMock()
    resp.status_code = status_code
    resp.get.return_value = content_type
    if streaming:
        resp.streaming_content = iter([b"<html>Home</html>"])
    else:
        del resp.streaming_content
    resp.close.return_value = None
    return resp


def test_root_route_ok():
    from cauldron_site_astro.site_diagnostics import check_root_route
    mock_client = MagicMock()
    mock_client.get.return_value = _mock_response(200, "text/html; charset=utf-8")
    with patch("cauldron_site_astro.site_diagnostics._make_test_client", return_value=mock_client):
        result = check_root_route()
    assert result["status"] == "ok"
    assert result["ok"] is True
    assert result["http_status"] == 200


def test_root_route_not_found():
    from cauldron_site_astro.site_diagnostics import check_root_route
    mock_client = MagicMock()
    mock_client.get.return_value = _mock_response(404, "text/html", streaming=False)
    with patch("cauldron_site_astro.site_diagnostics._make_test_client", return_value=mock_client):
        result = check_root_route()
    assert result["status"] == "not_found"
    assert result["ok"] is False
    assert result["http_status"] == 404


def test_root_route_error_status():
    from cauldron_site_astro.site_diagnostics import check_root_route
    mock_client = MagicMock()
    mock_client.get.return_value = _mock_response(500, "text/plain", streaming=False)
    with patch("cauldron_site_astro.site_diagnostics._make_test_client", return_value=mock_client):
        result = check_root_route()
    assert result["status"] == "error"
    assert result["ok"] is False
    assert result["http_status"] == 500


def test_root_route_unreachable():
    from cauldron_site_astro.site_diagnostics import check_root_route
    with patch(
        "cauldron_site_astro.site_diagnostics._make_test_client",
        side_effect=Exception("connection refused"),
    ):
        result = check_root_route()
    assert result["status"] == "unreachable"
    assert result["ok"] is False


def test_root_route_200_non_html_is_error():
    from cauldron_site_astro.site_diagnostics import check_root_route
    mock_client = MagicMock()
    mock_client.get.return_value = _mock_response(200, "application/json", streaming=False)
    with patch("cauldron_site_astro.site_diagnostics._make_test_client", return_value=mock_client):
        result = check_root_route()
    assert result["status"] == "error"
    assert result["ok"] is False


def test_root_route_streaming_response_consumed():
    """Verify streaming_content is fully consumed (no resource leak)."""
    from cauldron_site_astro.site_diagnostics import check_root_route
    consumed = []

    def _gen():
        for chunk in [b"chunk1", b"chunk2"]:
            consumed.append(chunk)
            yield chunk

    resp = MagicMock()
    resp.status_code = 200
    resp.get.return_value = "text/html"
    resp.streaming_content = _gen()
    resp.close.return_value = None

    mock_client = MagicMock()
    mock_client.get.return_value = resp
    with patch("cauldron_site_astro.site_diagnostics._make_test_client", return_value=mock_client):
        check_root_route()

    assert consumed == [b"chunk1", b"chunk2"]


# ---------------------------------------------------------------------------
# run_site_diagnostics
# ---------------------------------------------------------------------------


def test_run_site_diagnostics_all_healthy(tmp_path: Path):
    from cauldron_site_astro.site_diagnostics import run_site_diagnostics

    (tmp_path / "index.html").write_text("<html>Home</html>")
    svc = _mock_service(item=_published_item())

    mock_client = MagicMock()
    mock_client.get.return_value = _mock_response(200, "text/html")
    with patch("cauldron_site_astro.site_diagnostics._make_test_client", return_value=mock_client):
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
    svc = _mock_service(item=None)

    mock_client = MagicMock()
    mock_client.get.return_value = _mock_response(200, "text/html")
    with patch("cauldron_site_astro.site_diagnostics._make_test_client", return_value=mock_client):
        result = run_site_diagnostics(
            actor=_actor(),
            service=svc,
            output_root=str(tmp_path),
        )

    assert result["healthy"] is False
    assert result["checks"]["homepage_content"]["status"] == "missing"


def test_run_site_diagnostics_artifact_missing_unhealthy(tmp_path: Path):
    from cauldron_site_astro.site_diagnostics import run_site_diagnostics

    svc = _mock_service(item=_published_item())

    mock_client = MagicMock()
    mock_client.get.return_value = _mock_response(200, "text/html")
    with patch("cauldron_site_astro.site_diagnostics._make_test_client", return_value=mock_client):
        result = run_site_diagnostics(
            actor=_actor(),
            service=svc,
            output_root=str(tmp_path),
        )

    assert result["healthy"] is False
    assert result["checks"]["root_artifact"]["status"] == "missing"


def test_run_site_diagnostics_checks_dict_keys(tmp_path: Path):
    from cauldron_site_astro.site_diagnostics import run_site_diagnostics

    svc = _mock_service(item=None)
    mock_client = MagicMock()
    mock_client.get.return_value = _mock_response(404, "text/html", streaming=False)
    with patch("cauldron_site_astro.site_diagnostics._make_test_client", return_value=mock_client):
        result = run_site_diagnostics(
            actor=_actor(),
            service=svc,
            output_root=None,
        )

    assert set(result["checks"].keys()) == {"homepage_content", "root_artifact", "root_route"}
