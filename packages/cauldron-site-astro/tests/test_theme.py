"""Unit tests for SiteThemeService."""
from __future__ import annotations

from pathlib import Path

import pytest

from cauldron_site_astro.theme import SiteThemeService


def test_get_active_css_returns_empty_when_no_file(tmp_path: Path):
    svc = SiteThemeService(tmp_path / "theme")
    assert svc.get_active_css() == ""


def test_stage_css_writes_staged_file(tmp_path: Path):
    svc = SiteThemeService(tmp_path / "theme")
    svc.stage_css("body { color: red; }")
    assert (tmp_path / "theme" / "staged.css").read_text(encoding="utf-8") == "body { color: red; }"


def test_get_staged_css_returns_none_when_not_staged(tmp_path: Path):
    svc = SiteThemeService(tmp_path / "theme")
    assert svc.get_staged_css() is None


def test_get_staged_css_returns_content_when_staged(tmp_path: Path):
    svc = SiteThemeService(tmp_path / "theme")
    svc.stage_css("nav { display: flex; }")
    assert svc.get_staged_css() == "nav { display: flex; }"


def test_promote_staged_returns_false_when_nothing_staged(tmp_path: Path):
    svc = SiteThemeService(tmp_path / "theme")
    assert svc.promote_staged() is False


def test_promote_staged_moves_to_active(tmp_path: Path):
    svc = SiteThemeService(tmp_path / "theme")
    svc.stage_css("h1 { color: blue; }")
    result = svc.promote_staged()
    assert result is True
    assert svc.get_active_css() == "h1 { color: blue; }"
    assert svc.get_staged_css() is None


def test_promote_staged_removes_staged_file(tmp_path: Path):
    svc = SiteThemeService(tmp_path / "theme")
    svc.stage_css("css-content")
    svc.promote_staged()
    assert not (tmp_path / "theme" / "staged.css").exists()


def test_discard_staged_removes_staged_file(tmp_path: Path):
    svc = SiteThemeService(tmp_path / "theme")
    svc.stage_css("temporary css")
    svc.discard_staged()
    assert svc.get_staged_css() is None


def test_discard_staged_no_op_when_nothing_staged(tmp_path: Path):
    svc = SiteThemeService(tmp_path / "theme")
    # Should not raise
    svc.discard_staged()


def test_stage_creates_dir_automatically(tmp_path: Path):
    nested = tmp_path / "deep" / "nested" / "theme"
    svc = SiteThemeService(nested)
    svc.stage_css("body {}")
    assert nested.exists()


def test_overwrite_staged(tmp_path: Path):
    svc = SiteThemeService(tmp_path / "theme")
    svc.stage_css("first version")
    svc.stage_css("second version")
    assert svc.get_staged_css() == "second version"


def test_active_css_persists_across_instances(tmp_path: Path):
    theme_dir = tmp_path / "theme"
    svc1 = SiteThemeService(theme_dir)
    svc1.stage_css("my css")
    svc1.promote_staged()

    # New instance reading from same directory
    svc2 = SiteThemeService(theme_dir)
    assert svc2.get_active_css() == "my css"


def test_multiple_promote_staged_calls_after_first_returns_false(tmp_path: Path):
    svc = SiteThemeService(tmp_path / "theme")
    svc.stage_css("css")
    assert svc.promote_staged() is True
    assert svc.promote_staged() is False
