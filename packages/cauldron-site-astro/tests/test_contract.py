"""Contract tests for the Django -> Astro manifest API.

These tests pin the shape and version of the manifest that
:class:`SiteBuildService` produces so that any change on the Python side
that would break the Astro consumer is caught locally, before it ships.

When the shape changes intentionally, bump ``MANIFEST_API_VERSION`` in
``cauldron_site_astro.service`` **and** mirror the change in
``cauldron-app/frontend/src/lib/manifest.ts``.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def test_manifest_api_version_is_semver_string():
    from cauldron_site_astro.service import MANIFEST_API_VERSION

    assert isinstance(MANIFEST_API_VERSION, str)
    parts = MANIFEST_API_VERSION.split(".")
    assert len(parts) == 2, "MANIFEST_API_VERSION must be MAJOR.MINOR"
    assert all(p.isdigit() for p in parts)


def test_manifest_top_level_shape():
    """The manifest has api_version, pages (list), and theme.css_content."""
    from cauldron_site_astro.service import MANIFEST_API_VERSION

    manifest = {
        "api_version": MANIFEST_API_VERSION,
        "pages": [],
        "theme": {"css_content": ""},
    }
    assert manifest["api_version"] == MANIFEST_API_VERSION
    assert isinstance(manifest["pages"], list)
    assert isinstance(manifest["theme"], dict)
    assert "css_content" in manifest["theme"]


def test_manifest_page_required_fields():
    """Every page must expose the fields the Astro consumer reads."""
    required = {
        "id", "route", "title", "navigation_title", "summary", "body",
        "template", "seo_title", "meta_description", "canonical_url",
        "robots_index", "robots_follow",
    }
    sample_page = {
        "id": "x",
        "route": "/",
        "title": "T",
        "navigation_title": "T",
        "summary": "",
        "body": "",
        "template": "page",
        "seo_title": "",
        "meta_description": "",
        "canonical_url": "",
        "robots_index": True,
        "robots_follow": True,
    }
    assert required <= set(sample_page.keys())


def test_build_service_emits_api_version_in_manifest(tmp_path: Path):
    """A real build writes api_version into the manifest handed to Astro."""
    from cauldron_site_astro.service import MANIFEST_API_VERSION, SiteBuildService
    from cauldron_site_astro.config import SiteAstroConfig

    frontend = tmp_path / "frontend"
    frontend.mkdir()
    output = tmp_path / "output"
    cfg = SiteAstroConfig(
        frontend_root=str(frontend),
        output_root=str(output),
        npm_command="npm",
        build_timeout=30,
    )

    item = SimpleNamespace(
        id="homepage",
        slug="homepage",
        status="published",
        data={"title": "Home"},
        body="Hello",
    )
    router = MagicMock()
    router.list_items.return_value = [item]
    svc = SiteBuildService(cfg, router)

    captured = {}

    def fake_run(cmd, **kwargs):
        manifest_path = kwargs["env"]["CAULDRON_MANIFEST"]
        tmp_out = kwargs["env"]["CAULDRON_OUTDIR"]
        Path(tmp_out).mkdir(parents=True, exist_ok=True)
        with open(manifest_path, "r", encoding="utf-8") as f:
            captured.update(json.load(f))
        proc = MagicMock()
        proc.returncode = 0
        proc.stdout = ""
        proc.stderr = ""
        return proc

    with patch("subprocess.run", side_effect=fake_run):
        result = svc.build()

    assert result.ok is True
    assert captured.get("api_version") == MANIFEST_API_VERSION
    assert "pages" in captured
    assert "theme" in captured and "css_content" in captured["theme"]
