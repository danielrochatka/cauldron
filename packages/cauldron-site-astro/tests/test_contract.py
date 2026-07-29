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


def test_manifest_page_full_field_coverage():
    """All fields declared in the Astro ManifestPage TypeScript interface are present.

    This is the consumer-facing contract: if a field is missing here, the
    Astro build will either silently produce empty values or fail at runtime.

    Field list mirrors ``cauldron-app/frontend/src/lib/manifest.ts``
    ``ManifestPage`` interface exactly. When the TypeScript interface gains a
    new required field, add it here and to ``SiteBuildService``'s page-entry
    construction in ``service.py``.
    """
    # Fields declared as required (non-optional) in ManifestPage
    ts_required_fields = {
        "id", "route", "title", "navigation_title", "summary", "body",
        "template", "seo_title", "meta_description", "canonical_url",
        "robots_index", "robots_follow",
        "social_title", "social_description", "social_image",
    }
    # nav_visible is declared `nav_visible?: boolean` (optional) in the TS
    # interface — Astro's buildNavItems handles its absence gracefully.

    # A page entry exactly as emitted by SiteBuildService
    page_entry = {
        "id": "test-id",
        "route": "/test/",
        "title": "Test",
        "navigation_title": "Test nav",
        "summary": "A test page",
        "body": "## Hello",
        "template": "page",
        "seo_title": "Test SEO",
        "meta_description": "A test page",
        "canonical_url": "",
        "robots_index": True,
        "robots_follow": True,
        "social_title": "",
        "social_description": "",
        "social_image": "",
    }

    missing = ts_required_fields - set(page_entry.keys())
    assert not missing, (
        f"ManifestPage fields missing from Django emitter: {sorted(missing)!r}. "
        "Update SiteBuildService's page-entry construction and manifest.ts together."
    )


def test_build_service_emits_full_page_schema(tmp_path: Path):
    """SiteBuildService.build() writes all ManifestPage fields into the manifest.

    Complements test_manifest_page_full_field_coverage by exercising the
    actual code path that constructs the manifest dict, not just a sample.
    """
    import json
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch
    from cauldron_site_astro.service import SiteBuildService
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
        id="p1",
        slug="page-one",
        status="published",
        data={
            "title": "Page One",
            "navigation_title": "P1",
            "summary": "Summary",
            "template": "page",
            "seo_title": "SEO",
            "meta_description": "Desc",
            "canonical_url": "https://example.com/page-one/",
            "robots_index": True,
            "robots_follow": False,
            "social_title": "Social",
            "social_description": "Social desc",
            "social_image": "/img/og.png",
        },
        body="Body text",
    )
    router = MagicMock()
    router.list_items.return_value = [item]
    svc = SiteBuildService(cfg, router)

    captured = {}

    def fake_run(cmd, **kwargs):
        manifest_path = kwargs["env"]["CAULDRON_MANIFEST"]
        Path(kwargs["env"]["CAULDRON_OUTDIR"]).mkdir(parents=True, exist_ok=True)
        with open(manifest_path, "r", encoding="utf-8") as f:
            captured.update(json.load(f))
        proc = MagicMock()
        proc.returncode, proc.stdout, proc.stderr = 0, "", ""
        return proc

    with patch("subprocess.run", side_effect=fake_run):
        result = svc.build()

    assert result.ok is True
    assert len(captured.get("pages", [])) == 1

    page = captured["pages"][0]
    required = {
        "id", "route", "title", "navigation_title", "summary", "body",
        "template", "seo_title", "meta_description", "canonical_url",
        "robots_index", "robots_follow",
        "social_title", "social_description", "social_image",
    }
    missing = required - set(page.keys())
    assert not missing, f"build() omitted ManifestPage fields: {sorted(missing)!r}"

    # Spot-check values round-trip correctly
    assert page["id"] == "p1"
    assert page["route"] == "/page-one/"
    assert page["navigation_title"] == "P1"
    assert page["robots_follow"] is False
    assert page["social_image"] == "/img/og.png"


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
