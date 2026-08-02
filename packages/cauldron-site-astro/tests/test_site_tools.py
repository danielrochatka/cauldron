"""Tests for cauldron-site-astro site tools registered with Admin AI."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx():
    from django.contrib.auth import get_user_model
    from cauldron_ai_admin.tools import AdminAIToolContext
    User = get_user_model()
    user, _ = User.objects.get_or_create(
        username="site-tools-user",
        defaults={"is_superuser": True, "is_staff": True},
    )
    if not user.is_superuser:
        user.is_superuser = True
        user.save(update_fields=["is_superuser"])
    return AdminAIToolContext(actor=user, run_id="r1", correlation_id="c1")


def _ctx_deny(*perms_to_deny):
    """Context whose actor denies the specified permissions."""
    from types import SimpleNamespace
    from cauldron_ai_admin.tools import AdminAIToolContext
    denied = set(perms_to_deny)
    actor = SimpleNamespace(has_perm=lambda perm: perm not in denied)
    return AdminAIToolContext(actor=actor, run_id="r1", correlation_id="c1")


def _make_build_result(ok=True, pages_built=1, output_dir="/tmp/out", error="", build_log=""):
    from cauldron_site_astro.service import BuildResult
    return BuildResult(
        ok=ok,
        pages_built=pages_built,
        output_dir=output_dir,
        error=error,
        build_log=build_log,
    )


def _make_config(
    tmp_path: Path,
    theme_root: str = "",
    previews_root: str = "",
):
    from cauldron_site_astro.config import SiteAstroConfig
    frontend = tmp_path / "frontend"
    frontend.mkdir(exist_ok=True)
    output = tmp_path / "output"
    return SiteAstroConfig(
        frontend_root=str(frontend),
        output_root=str(output),
        npm_command="npm",
        build_timeout=30,
        theme_root=theme_root,
        previews_root=previews_root,
    )


def _make_mock_svc(config, pages_result=None):
    svc = MagicMock()
    svc._config = config
    if pages_result is not None:
        svc.build.return_value = pages_result
        svc.build_preview.return_value = pages_result
    return svc


# ---------------------------------------------------------------------------
# Import & registration
# ---------------------------------------------------------------------------


def test_site_tools_module_importable():
    from cauldron_site_astro import site_tools
    assert callable(site_tools.register)


def test_site_tools_register_into_fresh_registry():
    from cauldron_ai_admin.tools import AdminAIToolRegistry
    from cauldron_site_astro import site_tools

    reg = AdminAIToolRegistry()
    site_tools.register(reg)

    names = {d.name for d in reg.all_definitions()}
    assert "site.verify_root" in names
    assert "site.inspect" in names
    assert "site.stage_theme" in names
    assert "site.propose_homepage" in names
    assert "site.prepare_change_set" in names
    assert "site.inspect_preview" in names
    assert "site.publish" in names


def test_site_tools_register_is_idempotent():
    from cauldron_ai_admin.tools import AdminAIToolRegistry
    from cauldron_site_astro import site_tools

    reg = AdminAIToolRegistry()
    site_tools.register(reg)
    site_tools.register(reg)  # Second call must not raise


# ---------------------------------------------------------------------------
# site.inspect
# ---------------------------------------------------------------------------


def test_site_inspect_success_live_build_absent(tmp_path: Path):
    from cauldron_site_astro.site_tools import _handle_site_inspect as handle_site_inspect

    config = _make_config(tmp_path)
    svc = _make_mock_svc(config)

    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        result = handle_site_inspect(_ctx())

    assert result.success is True
    assert result.data["live_build_exists"] is False
    assert result.data["staged_theme_pending"] is False
    # Never leak filesystem paths.
    assert "output_root" not in result.data
    assert "frontend_root" not in result.data
    assert "theme_root" not in result.data
    assert "previews_root" not in result.data


def test_site_inspect_success_live_build_present(tmp_path: Path):
    from cauldron_site_astro.site_tools import _handle_site_inspect as handle_site_inspect

    output = tmp_path / "output"
    output.mkdir()
    (output / "index.html").write_text("<html>Live</html>")

    config = _make_config(tmp_path)
    config = config.__class__(
        frontend_root=config.frontend_root,
        output_root=str(output),
        npm_command="npm",
        build_timeout=30,
    )
    svc = _make_mock_svc(config)

    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        result = handle_site_inspect(_ctx())

    assert result.success is True
    assert result.data["live_build_exists"] is True


def test_site_inspect_staged_theme_pending(tmp_path: Path):
    from cauldron_site_astro.site_tools import _handle_site_inspect as handle_site_inspect
    from cauldron_site_astro.theme import SiteThemeService

    theme_dir = tmp_path / "theme"
    SiteThemeService(theme_dir).stage_css("body {}")

    config = _make_config(tmp_path, theme_root=str(theme_dir))
    svc = _make_mock_svc(config)

    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        result = handle_site_inspect(_ctx())

    assert result.success is True
    assert result.data["staged_theme_pending"] is True


def test_site_inspect_get_build_service_error():
    from cauldron_site_astro.site_tools import _handle_site_inspect as handle_site_inspect

    with patch("cauldron_site_astro.site_tools.get_build_service", side_effect=Exception("boom")):
        result = handle_site_inspect(_ctx())

    assert result.success is False
    assert "boom" in result.message


# ---------------------------------------------------------------------------
# site.stage_theme
# ---------------------------------------------------------------------------


def test_stage_theme_no_theme_root_returns_error(tmp_path: Path):
    from cauldron_site_astro.site_tools import _handle_stage_theme as handle_stage_theme

    config = _make_config(tmp_path, theme_root="")
    svc = _make_mock_svc(config)

    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        result = handle_stage_theme(_ctx(), css_content="body {}")

    assert result.success is False
    assert "theme_root" in result.message


def test_stage_theme_success(tmp_path: Path):
    from cauldron_site_astro.site_tools import _handle_stage_theme as handle_stage_theme
    from cauldron_site_astro.theme import SiteThemeService

    theme_dir = tmp_path / "theme"
    config = _make_config(tmp_path, theme_root=str(theme_dir))
    svc = _make_mock_svc(config)

    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        result = handle_stage_theme(_ctx(), css_content="nav { color: red; }")

    assert result.success is True
    assert result.data["staged"] is True
    assert result.data["css_length"] == len("nav { color: red; }")

    # Verify the CSS was actually written
    assert SiteThemeService(theme_dir).get_staged_css() == "nav { color: red; }"


def test_stage_theme_get_build_service_error():
    from cauldron_site_astro.site_tools import _handle_stage_theme as handle_stage_theme

    with patch("cauldron_site_astro.site_tools.get_build_service", side_effect=Exception("no config")):
        result = handle_stage_theme(_ctx(), css_content="body {}")

    assert result.success is False
    assert "no config" in result.message


# ---------------------------------------------------------------------------
# site.prepare_change_set
# ---------------------------------------------------------------------------


def test_prepare_change_set_requires_content_request_ids(tmp_path: Path):
    from cauldron_site_astro.site_tools import _handle_prepare_change_set as handle_prepare_change_set

    previews_root = tmp_path / "previews"
    config = _make_config(tmp_path, previews_root=str(previews_root))
    svc = _make_mock_svc(config)

    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        result = handle_prepare_change_set(_ctx(), content_request_ids=[])

    assert result.success is False
    assert "content_request_ids" in result.message


def test_prepare_change_set_no_previews_root_returns_error(tmp_path: Path):
    from cauldron_site_astro.site_tools import _handle_prepare_change_set as handle_prepare_change_set

    config = _make_config(tmp_path, previews_root="")
    svc = _make_mock_svc(config)

    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        result = handle_prepare_change_set(
            _ctx(), content_request_ids=["req-1"],
        )

    assert result.success is False
    assert "previews_root" in result.message


def test_prepare_change_set_success_persists_draft_ready(tmp_path: Path):
    from cauldron_site_astro.site_tools import _handle_prepare_change_set as handle_prepare_change_set
    from cauldron_site_astro.models import SiteChangeSet

    previews_root = tmp_path / "previews"
    config = _make_config(tmp_path, previews_root=str(previews_root))
    build_result = _make_build_result(ok=True, pages_built=2)
    svc = _make_mock_svc(config, pages_result=build_result)

    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        result = handle_prepare_change_set(
            _ctx(),
            content_request_ids=["req-a", "req-b"],
            theme_css="body { color: blue; }",
        )

    assert result.success is True
    assert result.data["pages_built"] == 2
    # preview_url must be a Django URL path, not a filesystem path
    assert result.data["preview_url"].startswith("/")

    cs_id = result.data["change_set_id"]
    cs = SiteChangeSet.objects.get(id=cs_id)
    assert cs.status == SiteChangeSet.DRAFT_READY
    assert cs.draft_ready_at is not None
    assert cs.content_request_ids == ["req-a", "req-b"]
    assert cs.staged_theme_css == "body { color: blue; }"


def test_prepare_change_set_preview_failed_persists_status(tmp_path: Path):
    from cauldron_site_astro.site_tools import _handle_prepare_change_set as handle_prepare_change_set
    from cauldron_site_astro.models import SiteChangeSet

    previews_root = tmp_path / "previews"
    config = _make_config(tmp_path, previews_root=str(previews_root))
    build_result = _make_build_result(ok=False, error="Build crashed", build_log="error log")
    svc = _make_mock_svc(config, pages_result=build_result)

    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        result = handle_prepare_change_set(
            _ctx(), content_request_ids=["req-x"],
        )

    assert result.success is False
    cs_id = result.data["change_set_id"]
    cs = SiteChangeSet.objects.get(id=cs_id)
    assert cs.status == SiteChangeSet.PREVIEW_FAILED


def test_prepare_change_set_forwards_theme_css_to_build(tmp_path: Path):
    from cauldron_site_astro.site_tools import _handle_prepare_change_set as handle_prepare_change_set

    previews_root = tmp_path / "previews"
    config = _make_config(tmp_path, previews_root=str(previews_root))
    build_result = _make_build_result(ok=True, pages_built=1)
    svc = _make_mock_svc(config, pages_result=build_result)

    captured = {}

    def capture_preview(**kwargs):
        captured.update(kwargs)
        return build_result

    svc.build_preview.side_effect = capture_preview

    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        result = handle_prepare_change_set(
            _ctx(),
            content_request_ids=["req-1"],
            theme_css="body { background: blue; }",
        )

    assert result.success is True
    assert captured.get("theme_css") == "body { background: blue; }"


# ---------------------------------------------------------------------------
# site.inspect_preview
# ---------------------------------------------------------------------------


def test_inspect_preview_not_found(tmp_path: Path):
    import uuid as _uuid
    from cauldron_site_astro.site_tools import _handle_inspect_preview as handle_inspect_preview

    result = handle_inspect_preview(_ctx(), change_set_id=str(_uuid.uuid4()))

    assert result.success is False
    assert "not found" in result.message


def test_inspect_preview_success(tmp_path: Path):
    from cauldron_site_astro.site_tools import _handle_inspect_preview as handle_inspect_preview
    from cauldron_site_astro.models import SiteChangeSet

    previews_root = tmp_path / "previews"

    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        content_request_ids=["req-1"],
        preview_dir="sub",
    )

    preview_dir = previews_root / "sub"
    preview_dir.mkdir(parents=True)
    (preview_dir / "index.html").write_text("<html>Home</html>")
    (preview_dir / "about").mkdir()
    (preview_dir / "about" / "index.html").write_text("<html>About</html>")

    config = _make_config(tmp_path, previews_root=str(previews_root))
    svc = _make_mock_svc(config)

    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        result = handle_inspect_preview(_ctx(), change_set_id=str(cs.id))

    assert result.success is True
    assert result.data["change_set_id"] == str(cs.id)
    assert result.data["status"] == SiteChangeSet.DRAFT_READY
    assert result.data["pages_built"] == 2
    assert result.data["preview_url"].startswith("/")


# ---------------------------------------------------------------------------
# site.publish
# ---------------------------------------------------------------------------


def test_publish_not_confirmed_returns_error():
    from cauldron_site_astro.site_tools import _handle_publish as handle_publish

    result = handle_publish(
        _ctx(), change_set_id="00000000-0000-0000-0000-000000000000", confirm=False,
    )

    assert result.success is False
    assert "confirm" in result.message.lower()


def test_publish_rejects_non_draft_ready_status(tmp_path: Path):
    from cauldron_site_astro.site_tools import _handle_publish as handle_publish
    from cauldron_site_astro.models import SiteChangeSet

    cs = SiteChangeSet.objects.create(status=SiteChangeSet.PREPARING)
    result = handle_publish(_ctx(), change_set_id=str(cs.id), confirm=True)
    assert result.success is False
    assert "draft_ready" in result.message


def test_publish_success_with_no_content_requests(tmp_path: Path):
    """Publish succeeds when the change set is theme-only (no content requests)."""
    from cauldron_site_astro.site_tools import _handle_publish as handle_publish
    from cauldron_site_astro.models import SiteChangeSet

    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        content_request_ids=[],
    )

    config = _make_config(tmp_path)
    build_result = _make_build_result(ok=True, pages_built=3, output_dir=str(tmp_path / "output"))
    svc = _make_mock_svc(config, pages_result=build_result)

    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        result = handle_publish(_ctx(), change_set_id=str(cs.id), confirm=True)

    assert result.success is True
    assert result.data["pages_built"] == 3
    # live_url for a published site is the live site root, not a fs path.
    assert result.data["live_url"] == "/"

    cs.refresh_from_db()
    assert cs.status == SiteChangeSet.PUBLISHED
    assert cs.published_at is not None


def test_publish_promotes_staged_theme_only_after_successful_build(tmp_path: Path):
    from cauldron_site_astro.site_tools import _handle_publish as handle_publish
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_site_astro.theme import SiteThemeService

    theme_dir = tmp_path / "theme"
    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        content_request_ids=[],
        staged_theme_css="body { color: green; }",
    )

    config = _make_config(tmp_path, theme_root=str(theme_dir))
    build_result = _make_build_result(ok=True, pages_built=1)
    svc = _make_mock_svc(config, pages_result=build_result)

    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        result = handle_publish(_ctx(), change_set_id=str(cs.id), confirm=True)

    assert result.success is True
    # active.css should now hold what was staged on the change set.
    theme_svc = SiteThemeService(theme_dir)
    assert theme_svc.get_active_css() == "body { color: green; }"


def test_publish_build_failure_leaves_active_css_untouched(tmp_path: Path):
    from cauldron_site_astro.site_tools import _handle_publish as handle_publish
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_site_astro.theme import SiteThemeService

    theme_dir = tmp_path / "theme"
    # Pre-existing active.css that must NOT be overwritten on failure.
    SiteThemeService(theme_dir).stage_css("body { old: 1; }")
    SiteThemeService(theme_dir).promote_staged()

    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        content_request_ids=[],
        staged_theme_css="body { color: NEW; }",
    )

    config = _make_config(tmp_path, theme_root=str(theme_dir))
    build_result = _make_build_result(ok=False, error="Astro failed", build_log="err")
    svc = _make_mock_svc(config, pages_result=build_result)

    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        result = handle_publish(_ctx(), change_set_id=str(cs.id), confirm=True)

    assert result.success is False
    theme_svc = SiteThemeService(theme_dir)
    # active.css must still be the pre-existing value.
    assert theme_svc.get_active_css() == "body { old: 1; }"
    cs.refresh_from_db()
    assert cs.status == SiteChangeSet.PUBLISH_FAILED


def test_publish_get_build_service_error():
    from cauldron_site_astro.site_tools import _handle_publish as handle_publish
    from cauldron_site_astro.models import SiteChangeSet

    cs = SiteChangeSet.objects.create(status=SiteChangeSet.DRAFT_READY)
    with patch(
        "cauldron_site_astro.site_tools.get_build_service",
        side_effect=Exception("config missing"),
    ):
        result = handle_publish(_ctx(), change_set_id=str(cs.id), confirm=True)

    assert result.success is False
    assert "config missing" in result.message


# ---------------------------------------------------------------------------
# site.verify_root
# ---------------------------------------------------------------------------


def test_verify_root_success_returns_diagnostics(tmp_path: Path):
    from cauldron_site_astro.site_tools import _handle_verify_root as handle_verify_root

    config = _make_config(tmp_path)
    svc = _make_mock_svc(config)

    mock_diag = {
        "healthy": True,
        "checks": {
            "homepage_content": {"status": "published", "ok": True},
            "root_artifact": {"status": "ok", "ok": True},
            "root_route": {"status": "ok", "ok": True, "http_status": 200},
        },
    }

    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        with patch(
            "cauldron_site_astro.site_tools.run_site_diagnostics",
            return_value=mock_diag,
        ):
            result = handle_verify_root(_ctx())

    assert result.success is True
    assert result.data["healthy"] is True
    assert "checks" in result.data


def test_verify_root_build_service_error_still_runs(tmp_path: Path):
    """Even when get_build_service fails, diagnostics run with output_root=None."""
    from cauldron_site_astro.site_tools import _handle_verify_root as handle_verify_root

    mock_diag = {
        "healthy": False,
        "checks": {
            "homepage_content": {"status": "unavailable", "ok": False},
            "root_artifact": {"status": "unconfigured", "ok": False},
            "root_route": {"status": "route_not_found", "ok": False},
        },
    }

    with patch(
        "cauldron_site_astro.site_tools.get_build_service",
        side_effect=Exception("no config"),
    ):
        with patch(
            "cauldron_site_astro.site_tools.run_site_diagnostics",
            return_value=mock_diag,
        ) as mock_run:
            result = handle_verify_root(_ctx())

    assert result.success is True
    assert result.data["healthy"] is False
    # output_root must have been passed as None when build service failed
    call_kwargs = mock_run.call_args.kwargs
    assert call_kwargs["output_root"] is None


def test_verify_root_diagnostics_exception_returns_failure(tmp_path: Path):
    from cauldron_site_astro.site_tools import _handle_verify_root as handle_verify_root

    config = _make_config(tmp_path)
    svc = _make_mock_svc(config)

    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        with patch(
            "cauldron_site_astro.site_tools.run_site_diagnostics",
            side_effect=RuntimeError("unexpected"),
        ):
            result = handle_verify_root(_ctx())

    assert result.success is False
    assert "unexpected" in result.message


# ---------------------------------------------------------------------------
# site.propose_homepage
# ---------------------------------------------------------------------------


def _make_change_request_result(ok=True, request_id="cr-uuid-1"):
    from types import SimpleNamespace
    err_ns = None
    if not ok:
        err_ns = SimpleNamespace(message="proposal failed", code="ops.error")
    return SimpleNamespace(ok=ok, request_id=request_id, error=err_ns)


def test_propose_homepage_service_unavailable():
    from cauldron_site_astro.site_tools import _handle_propose_homepage as handle_propose_homepage

    with patch(
        "cauldron_site_astro.site_tools._get_content_operation_service",
        return_value=None,
    ):
        result = handle_propose_homepage(_ctx(), title="Home", body="Welcome!")

    assert result.success is False
    assert "unavailable" in result.message.lower()


def test_propose_homepage_create_success():
    """When no existing homepage, kind='create' and cs_id is returned."""
    from cauldron_site_astro.site_tools import _handle_propose_homepage as handle_propose_homepage

    mock_svc = MagicMock()
    mock_svc.get_item.return_value = None
    mock_svc.create_change_request.return_value = _make_change_request_result(
        ok=True, request_id="new-cr-id"
    )

    with patch(
        "cauldron_site_astro.site_tools._get_content_operation_service",
        return_value=mock_svc,
    ):
        result = handle_propose_homepage(_ctx(), title="Home", body="Welcome!")

    assert result.success is True
    assert result.data["cs_id"] == "new-cr-id"
    assert result.data["kind"] == "create"
    assert result.data["status"] == "proposed"


def test_propose_homepage_update_uses_expected_hash():
    """When homepage exists, kind='update' and expected_hash from existing item."""
    from types import SimpleNamespace
    from cauldron_site_astro.site_tools import _handle_propose_homepage as handle_propose_homepage

    existing_item = SimpleNamespace(status="published", hash="existing-hash-abc")
    mock_svc = MagicMock()
    mock_svc.get_item.return_value = existing_item
    mock_svc.create_change_request.return_value = _make_change_request_result(
        ok=True, request_id="update-cr-id"
    )

    with patch(
        "cauldron_site_astro.site_tools._get_content_operation_service",
        return_value=mock_svc,
    ):
        result = handle_propose_homepage(_ctx(), title="Updated Home", body="New body.")

    assert result.success is True
    assert result.data["kind"] == "update"
    assert result.data["cs_id"] == "update-cr-id"

    # Verify expected_hash was passed to build_homepage_operation via create_change_request
    call_kwargs = mock_svc.create_change_request.call_args.kwargs
    ops = call_kwargs["operations"]
    assert len(ops) == 1
    assert ops[0]["expected_hash"] == "existing-hash-abc"


def test_propose_homepage_proposal_failure():
    from cauldron_site_astro.site_tools import _handle_propose_homepage as handle_propose_homepage

    mock_svc = MagicMock()
    mock_svc.get_item.return_value = None
    mock_svc.create_change_request.return_value = _make_change_request_result(ok=False)

    with patch(
        "cauldron_site_astro.site_tools._get_content_operation_service",
        return_value=mock_svc,
    ):
        result = handle_propose_homepage(_ctx(), title="Home", body="Welcome!")

    assert result.success is False
    assert "proposal failed" in result.message.lower()


def test_propose_homepage_singleton_fields_are_fixed():
    """Item ID, slug, collection, schema, template must not be caller-overrideable."""
    from cauldron_content.homepage import (
        HOMEPAGE_ITEM_ID,
        HOMEPAGE_COLLECTION,
        HOMEPAGE_SCHEMA,
        HOMEPAGE_TEMPLATE,
    )
    from cauldron_site_astro.site_tools import _handle_propose_homepage as handle_propose_homepage

    mock_svc = MagicMock()
    mock_svc.get_item.return_value = None
    mock_svc.create_change_request.return_value = _make_change_request_result(
        ok=True, request_id="singleton-cr"
    )

    with patch(
        "cauldron_site_astro.site_tools._get_content_operation_service",
        return_value=mock_svc,
    ):
        handle_propose_homepage(_ctx(), title="T", body="B")

    call_kwargs = mock_svc.create_change_request.call_args.kwargs
    op = call_kwargs["operations"][0]

    assert op["item_id"] == HOMEPAGE_ITEM_ID
    assert op["slug"] == HOMEPAGE_ITEM_ID
    assert op["collection"] == HOMEPAGE_COLLECTION
    assert op["schema"] == HOMEPAGE_SCHEMA
    assert op["data"]["template"] == HOMEPAGE_TEMPLATE


def test_propose_homepage_optional_fields_passed():
    from cauldron_site_astro.site_tools import _handle_propose_homepage as handle_propose_homepage

    mock_svc = MagicMock()
    mock_svc.get_item.return_value = None
    mock_svc.create_change_request.return_value = _make_change_request_result(ok=True)

    with patch(
        "cauldron_site_astro.site_tools._get_content_operation_service",
        return_value=mock_svc,
    ):
        handle_propose_homepage(
            _ctx(),
            title="Home",
            body="Body",
            navigation_title="Nav",
            summary="Sum",
            seo_title="SEO",
            meta_description="Meta",
        )

    call_kwargs = mock_svc.create_change_request.call_args.kwargs
    op = call_kwargs["operations"][0]
    data = op.get("data", {})
    assert data.get("navigation_title") == "Nav"
    assert data.get("summary") == "Sum"
    assert data.get("seo_title") == "SEO"
    assert data.get("meta_description") == "Meta"


def test_propose_homepage_create_request_exception():
    from cauldron_site_astro.site_tools import _handle_propose_homepage as handle_propose_homepage

    mock_svc = MagicMock()
    mock_svc.get_item.return_value = None
    mock_svc.create_change_request.side_effect = RuntimeError("DB failure")

    with patch(
        "cauldron_site_astro.site_tools._get_content_operation_service",
        return_value=mock_svc,
    ):
        result = handle_propose_homepage(_ctx(), title="Home", body="Welcome!")

    assert result.success is False
    assert "DB failure" in result.message


# ---------------------------------------------------------------------------
# site.propose_homepage — Section 6 additions
# ---------------------------------------------------------------------------


def test_propose_homepage_lacks_view_published_perm():
    """Actor without view_published_content → tool failure, no create_change_request."""
    from cauldron_site_astro.site_tools import _handle_propose_homepage as handle_propose_homepage

    mock_svc = MagicMock()
    ctx = _ctx_deny("cauldron_content_operations.view_published_content")

    with patch(
        "cauldron_site_astro.site_tools._get_content_operation_service",
        return_value=mock_svc,
    ):
        result = handle_propose_homepage(ctx, title="T", body="B")

    assert result.success is False
    assert "view_published_content" in result.message
    mock_svc.create_change_request.assert_not_called()


def test_propose_homepage_lacks_view_draft_perm():
    """Actor without view_draft_content → tool failure, no create_change_request."""
    from cauldron_site_astro.site_tools import _handle_propose_homepage as handle_propose_homepage

    mock_svc = MagicMock()
    ctx = _ctx_deny("cauldron_content_operations.view_draft_content")

    with patch(
        "cauldron_site_astro.site_tools._get_content_operation_service",
        return_value=mock_svc,
    ):
        result = handle_propose_homepage(ctx, title="T", body="B")

    assert result.success is False
    assert "view_draft_content" in result.message
    mock_svc.create_change_request.assert_not_called()


def test_propose_homepage_lookup_failure_is_tool_failure():
    """get_item raising never silently falls through to a create operation."""
    from cauldron_site_astro.site_tools import _handle_propose_homepage as handle_propose_homepage

    mock_svc = MagicMock()
    mock_svc.get_item.side_effect = RuntimeError("DB timeout")

    with patch(
        "cauldron_site_astro.site_tools._get_content_operation_service",
        return_value=mock_svc,
    ):
        result = handle_propose_homepage(_ctx(), title="Home", body="Body")

    assert result.success is False
    assert "lookup failed" in result.message.lower() or "DB timeout" in result.message
    mock_svc.create_change_request.assert_not_called()


def test_propose_homepage_draft_homepage_produces_update():
    """Draft homepage detected via include_drafts=True → kind='update'."""
    from types import SimpleNamespace
    from cauldron_site_astro.site_tools import _handle_propose_homepage as handle_propose_homepage

    draft_item = SimpleNamespace(status="draft", hash="draft-hash-xyz")
    mock_svc = MagicMock()
    mock_svc.get_item.return_value = draft_item
    mock_svc.create_change_request.return_value = _make_change_request_result(
        ok=True, request_id="cr-draft-update"
    )

    with patch(
        "cauldron_site_astro.site_tools._get_content_operation_service",
        return_value=mock_svc,
    ):
        result = handle_propose_homepage(_ctx(), title="T", body="B")

    assert result.success is True
    assert result.data["kind"] == "update"

    call_kwargs = mock_svc.get_item.call_args.kwargs
    assert call_kwargs.get("include_drafts") is True

    op_kwargs = mock_svc.create_change_request.call_args.kwargs
    assert op_kwargs["operations"][0]["expected_hash"] == "draft-hash-xyz"


def test_propose_homepage_get_item_called_with_include_drafts_true():
    """get_item is always called with include_drafts=True."""
    from cauldron_site_astro.site_tools import _handle_propose_homepage as handle_propose_homepage

    mock_svc = MagicMock()
    mock_svc.get_item.return_value = None
    mock_svc.create_change_request.return_value = _make_change_request_result(ok=True)

    with patch(
        "cauldron_site_astro.site_tools._get_content_operation_service",
        return_value=mock_svc,
    ):
        handle_propose_homepage(_ctx(), title="T", body="B")

    call_kwargs = mock_svc.get_item.call_args.kwargs
    assert call_kwargs.get("include_drafts") is True


def test_propose_homepage_provider_name_is_empty():
    """provider_name must be '' so content routing selects the authoritative provider."""
    from cauldron_site_astro.site_tools import _handle_propose_homepage as handle_propose_homepage

    mock_svc = MagicMock()
    mock_svc.get_item.return_value = None
    mock_svc.create_change_request.return_value = _make_change_request_result(ok=True)

    with patch(
        "cauldron_site_astro.site_tools._get_content_operation_service",
        return_value=mock_svc,
    ):
        handle_propose_homepage(_ctx(), title="T", body="B")

    call_kwargs = mock_svc.create_change_request.call_args.kwargs
    assert call_kwargs["provider_name"] == ""


def test_propose_homepage_idempotency_key_forwarded():
    from cauldron_site_astro.site_tools import _handle_propose_homepage as handle_propose_homepage

    mock_svc = MagicMock()
    mock_svc.get_item.return_value = None
    mock_svc.create_change_request.return_value = _make_change_request_result(ok=True)

    with patch(
        "cauldron_site_astro.site_tools._get_content_operation_service",
        return_value=mock_svc,
    ):
        handle_propose_homepage(_ctx(), title="T", body="B", idempotency_key="my-key")

    call_kwargs = mock_svc.create_change_request.call_args.kwargs
    assert call_kwargs["idempotency_key"] == "my-key"


def test_propose_homepage_caller_description_forwarded():
    from cauldron_site_astro.site_tools import _handle_propose_homepage as handle_propose_homepage

    mock_svc = MagicMock()
    mock_svc.get_item.return_value = None
    mock_svc.create_change_request.return_value = _make_change_request_result(ok=True)

    with patch(
        "cauldron_site_astro.site_tools._get_content_operation_service",
        return_value=mock_svc,
    ):
        handle_propose_homepage(_ctx(), title="T", body="B", description="My custom desc")

    call_kwargs = mock_svc.create_change_request.call_args.kwargs
    assert call_kwargs["description"] == "My custom desc"


def test_propose_homepage_generated_description_when_no_caller_desc():
    from cauldron_site_astro.site_tools import _handle_propose_homepage as handle_propose_homepage

    mock_svc = MagicMock()
    mock_svc.get_item.return_value = None
    mock_svc.create_change_request.return_value = _make_change_request_result(ok=True)

    with patch(
        "cauldron_site_astro.site_tools._get_content_operation_service",
        return_value=mock_svc,
    ):
        handle_propose_homepage(_ctx(), title="My Homepage", body="B")

    call_kwargs = mock_svc.create_change_request.call_args.kwargs
    assert "My Homepage" in call_kwargs["description"]


def test_propose_homepage_schema_has_additional_properties_false():
    from cauldron_ai_admin.tools import AdminAIToolRegistry
    from cauldron_site_astro import site_tools

    reg = AdminAIToolRegistry()
    site_tools.register(reg)

    defn = next(d for d in reg.all_definitions() if d.name == "site.propose_homepage")
    assert defn.argument_schema.get("additionalProperties") is False


def test_propose_homepage_all_seo_fields_passed():
    from cauldron_site_astro.site_tools import _handle_propose_homepage as handle_propose_homepage

    mock_svc = MagicMock()
    mock_svc.get_item.return_value = None
    mock_svc.create_change_request.return_value = _make_change_request_result(ok=True)

    with patch(
        "cauldron_site_astro.site_tools._get_content_operation_service",
        return_value=mock_svc,
    ):
        handle_propose_homepage(
            _ctx(),
            title="T",
            body="B",
            canonical_url="https://example.com/",
            robots_index=False,
            robots_follow=False,
            social_title="Social T",
            social_description="Social D",
            social_image="/img/social.jpg",
        )

    call_kwargs = mock_svc.create_change_request.call_args.kwargs
    data = call_kwargs["operations"][0].get("data", {})
    assert data.get("canonical_url") == "https://example.com/"
    assert data.get("robots_index") is False
    assert data.get("robots_follow") is False
    assert data.get("social_title") == "Social T"
    assert data.get("social_description") == "Social D"
    assert data.get("social_image") == "/img/social.jpg"
