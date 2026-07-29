"""Tests for cauldron-site-astro site tools registered with Admin AI."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
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
    assert "site.prepare_preview" in names
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
    from cauldron_site_astro.site_tools import _handle_site_inspect

    config = _make_config(tmp_path)
    svc = _make_mock_svc(config)

    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        result = _handle_site_inspect(_ctx())

    assert result.success is True
    assert result.data["live_build_exists"] is False
    assert result.data["staged_theme_pending"] is False


def test_site_inspect_success_live_build_present(tmp_path: Path):
    from cauldron_site_astro.site_tools import _handle_site_inspect

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
        result = _handle_site_inspect(_ctx())

    assert result.success is True
    assert result.data["live_build_exists"] is True


def test_site_inspect_staged_theme_pending(tmp_path: Path):
    from cauldron_site_astro.site_tools import _handle_site_inspect
    from cauldron_site_astro.theme import SiteThemeService

    theme_dir = tmp_path / "theme"
    SiteThemeService(theme_dir).stage_css("body {}")

    config = _make_config(tmp_path, theme_root=str(theme_dir))
    svc = _make_mock_svc(config)

    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        result = _handle_site_inspect(_ctx())

    assert result.success is True
    assert result.data["staged_theme_pending"] is True


def test_site_inspect_get_build_service_error():
    from cauldron_site_astro.site_tools import _handle_site_inspect

    with patch("cauldron_site_astro.site_tools.get_build_service", side_effect=Exception("boom")):
        result = _handle_site_inspect(_ctx())

    assert result.success is False
    assert "boom" in result.message


# ---------------------------------------------------------------------------
# site.stage_theme
# ---------------------------------------------------------------------------


def test_stage_theme_no_theme_root_returns_error(tmp_path: Path):
    from cauldron_site_astro.site_tools import _handle_stage_theme

    config = _make_config(tmp_path, theme_root="")
    svc = _make_mock_svc(config)

    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        result = _handle_stage_theme(_ctx(), css_content="body {}")

    assert result.success is False
    assert "theme_root" in result.message


def test_stage_theme_success(tmp_path: Path):
    from cauldron_site_astro.site_tools import _handle_stage_theme
    from cauldron_site_astro.theme import SiteThemeService

    theme_dir = tmp_path / "theme"
    config = _make_config(tmp_path, theme_root=str(theme_dir))
    svc = _make_mock_svc(config)

    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        result = _handle_stage_theme(_ctx(), css_content="nav { color: red; }")

    assert result.success is True
    assert result.data["staged"] is True
    assert result.data["css_length"] == len("nav { color: red; }")

    # Verify the CSS was actually written
    assert SiteThemeService(theme_dir).get_staged_css() == "nav { color: red; }"


def test_stage_theme_get_build_service_error():
    from cauldron_site_astro.site_tools import _handle_stage_theme

    with patch("cauldron_site_astro.site_tools.get_build_service", side_effect=Exception("no config")):
        result = _handle_stage_theme(_ctx(), css_content="body {}")

    assert result.success is False
    assert "no config" in result.message


# ---------------------------------------------------------------------------
# site.prepare_preview
# ---------------------------------------------------------------------------


def test_prepare_preview_no_previews_root_returns_error(tmp_path: Path):
    from cauldron_site_astro.site_tools import _handle_prepare_preview

    config = _make_config(tmp_path, previews_root="")
    svc = _make_mock_svc(config)

    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        result = _handle_prepare_preview(_ctx())

    assert result.success is False
    assert "previews_root" in result.message


def test_prepare_preview_success(tmp_path: Path):
    from cauldron_site_astro.site_tools import _handle_prepare_preview

    previews_root = tmp_path / "previews"
    config = _make_config(tmp_path, previews_root=str(previews_root))
    build_result = _make_build_result(ok=True, pages_built=2, output_dir=str(previews_root / "preview-id"))
    svc = _make_mock_svc(config, pages_result=build_result)

    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        result = _handle_prepare_preview(_ctx())

    assert result.success is True
    assert "preview_id" in result.data
    assert result.data["pages_built"] == 2


def test_prepare_preview_uses_staged_theme(tmp_path: Path):
    from cauldron_site_astro.site_tools import _handle_prepare_preview
    from cauldron_site_astro.theme import SiteThemeService

    previews_root = tmp_path / "previews"
    theme_dir = tmp_path / "theme"
    SiteThemeService(theme_dir).stage_css("body { background: blue; }")

    config = _make_config(tmp_path, theme_root=str(theme_dir), previews_root=str(previews_root))
    build_result = _make_build_result(ok=True, pages_built=1)
    svc = _make_mock_svc(config, pages_result=build_result)

    captured_css = {}

    def capture_preview(output_dir, theme_css="", extra_items=None):
        captured_css["theme_css"] = theme_css
        return build_result

    svc.build_preview.side_effect = capture_preview

    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        result = _handle_prepare_preview(_ctx())

    assert result.success is True
    assert captured_css.get("theme_css") == "body { background: blue; }"


def test_prepare_preview_failed_build(tmp_path: Path):
    from cauldron_site_astro.site_tools import _handle_prepare_preview

    previews_root = tmp_path / "previews"
    config = _make_config(tmp_path, previews_root=str(previews_root))
    build_result = _make_build_result(ok=False, error="Build crashed", build_log="error log")
    svc = _make_mock_svc(config, pages_result=build_result)

    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        result = _handle_prepare_preview(_ctx())

    assert result.success is False
    assert "Build crashed" in result.message


# ---------------------------------------------------------------------------
# site.inspect_preview
# ---------------------------------------------------------------------------


def test_inspect_preview_not_found(tmp_path: Path):
    from cauldron_site_astro.site_tools import _handle_inspect_preview

    previews_root = tmp_path / "previews"
    config = _make_config(tmp_path, previews_root=str(previews_root))
    svc = _make_mock_svc(config)

    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        result = _handle_inspect_preview(_ctx(), preview_id="nonexistent-id")

    assert result.success is False
    assert "not found" in result.message


def test_inspect_preview_success(tmp_path: Path):
    from cauldron_site_astro.site_tools import _handle_inspect_preview

    previews_root = tmp_path / "previews"
    preview_id = "test-preview-123"
    preview_dir = previews_root / preview_id
    preview_dir.mkdir(parents=True)
    (preview_dir / "index.html").write_text("<html>Home</html>")
    (preview_dir / "about").mkdir()
    (preview_dir / "about" / "index.html").write_text("<html>About</html>")

    config = _make_config(tmp_path, previews_root=str(previews_root))
    svc = _make_mock_svc(config)

    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        result = _handle_inspect_preview(_ctx(), preview_id=preview_id)

    assert result.success is True
    assert result.data["preview_id"] == preview_id
    assert result.data["html_file_count"] == 2
    assert "index.html" in result.data["html_files"]


def test_inspect_preview_no_previews_root_returns_error(tmp_path: Path):
    from cauldron_site_astro.site_tools import _handle_inspect_preview

    config = _make_config(tmp_path, previews_root="")
    svc = _make_mock_svc(config)

    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        result = _handle_inspect_preview(_ctx(), preview_id="any-id")

    assert result.success is False
    assert "previews_root" in result.message


# ---------------------------------------------------------------------------
# site.publish
# ---------------------------------------------------------------------------


def test_publish_not_confirmed_returns_error(tmp_path: Path):
    from cauldron_site_astro.site_tools import _handle_publish

    config = _make_config(tmp_path)
    svc = _make_mock_svc(config)

    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        result = _handle_publish(_ctx(), confirm=False)

    assert result.success is False
    assert "confirm" in result.message.lower() or "not confirmed" in result.message.lower()


def test_publish_success(tmp_path: Path):
    from cauldron_site_astro.site_tools import _handle_publish

    config = _make_config(tmp_path)
    build_result = _make_build_result(ok=True, pages_built=3, output_dir=str(tmp_path / "output"))
    svc = _make_mock_svc(config, pages_result=build_result)

    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        result = _handle_publish(_ctx(), confirm=True)

    assert result.success is True
    assert result.data["pages_built"] == 3


def test_publish_promotes_staged_theme(tmp_path: Path):
    from cauldron_site_astro.site_tools import _handle_publish
    from cauldron_site_astro.theme import SiteThemeService

    theme_dir = tmp_path / "theme"
    SiteThemeService(theme_dir).stage_css("staged css")

    config = _make_config(tmp_path, theme_root=str(theme_dir))
    build_result = _make_build_result(ok=True, pages_built=1)
    svc = _make_mock_svc(config, pages_result=build_result)

    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        result = _handle_publish(_ctx(), confirm=True)

    assert result.success is True
    # staged.css promoted to active.css
    theme_svc = SiteThemeService(theme_dir)
    assert theme_svc.get_active_css() == "staged css"
    assert theme_svc.get_staged_css() is None


def test_publish_failed_build_returns_error(tmp_path: Path):
    from cauldron_site_astro.site_tools import _handle_publish

    config = _make_config(tmp_path)
    build_result = _make_build_result(ok=False, error="Astro failed", build_log="error output")
    svc = _make_mock_svc(config, pages_result=build_result)

    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        result = _handle_publish(_ctx(), confirm=True)

    assert result.success is False
    assert "Astro failed" in result.message


def test_publish_get_build_service_error():
    from cauldron_site_astro.site_tools import _handle_publish

    with patch("cauldron_site_astro.site_tools.get_build_service", side_effect=Exception("config missing")):
        result = _handle_publish(_ctx(), confirm=True)

    assert result.success is False
    assert "config missing" in result.message
