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
    user, _ = User.objects.get_or_create(username="site-tools-user")
    return AdminAIToolContext(actor=user, run_id="r1", correlation_id="c1")


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
    assert "site.inspect" in names
    assert "site.stage_theme" in names
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
    # preview_url for a published site is the live site root, not a fs path.
    assert result.data["preview_url"] == "/"

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
