"""Tests for SiteBuildService."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from cauldron_site_astro.service import BuildResult, SiteBuildService
from cauldron_site_astro.config import SiteAstroConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(tmp_path: Path, **overrides) -> SiteAstroConfig:
    frontend = tmp_path / "frontend"
    frontend.mkdir(exist_ok=True)
    output = tmp_path / "output"
    defaults = dict(
        frontend_root=str(frontend),
        output_root=str(output),
        npm_command="npm",
        build_timeout=30,
    )
    defaults.update(overrides)
    return SiteAstroConfig(**defaults)


def _make_item(
    item_id: str,
    slug: str,
    status: str = "published",
    data: dict | None = None,
    body: str = "",
) -> SimpleNamespace:
    """Create a duck-typed ContentItem substitute."""
    return SimpleNamespace(
        id=item_id,
        slug=slug,
        status=status,
        data=data or {},
        body=body,
    )


def _make_router(items: list) -> MagicMock:
    router = MagicMock()
    router.list_items.return_value = items
    return router


def _ok_proc(stdout: str = "Build complete.\n", stderr: str = "") -> MagicMock:
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


def _fail_proc(returncode: int = 1, stdout: str = "", stderr: str = "Error!") -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


# ---------------------------------------------------------------------------
# No-pages path
# ---------------------------------------------------------------------------


def test_build_no_published_pages_clears_output_root(tmp_path: Path):
    """When router returns no published pages, build returns ok=True, pages_built=0
    and output_root is replaced with an empty directory."""
    config = _make_config(tmp_path)
    router = _make_router([])
    svc = SiteBuildService(config, router)

    with patch("subprocess.run") as mock_run:
        result = svc.build()

    assert result.ok is True
    assert result.pages_built == 0
    mock_run.assert_not_called()
    output_root = Path(config.output_root)
    assert output_root.exists()
    assert output_root.is_dir()
    assert list(output_root.iterdir()) == []


def test_build_only_draft_pages_clears_output_root(tmp_path: Path):
    """Draft pages are excluded; if that leaves nothing, Astro is not invoked
    and output_root is replaced with an empty directory."""
    draft = _make_item("page.draft", "draft", status="draft")
    config = _make_config(tmp_path)
    router = _make_router([draft])
    svc = SiteBuildService(config, router)

    with patch("subprocess.run") as mock_run:
        result = svc.build()

    assert result.ok is True
    assert result.pages_built == 0
    mock_run.assert_not_called()
    output_root = Path(config.output_root)
    assert output_root.exists()
    assert output_root.is_dir()
    assert list(output_root.iterdir()) == []


# ---------------------------------------------------------------------------
# Homepage route
# ---------------------------------------------------------------------------


def test_homepage_route_is_slash(tmp_path: Path):
    """Homepage item always gets route '/' regardless of its slug."""
    homepage = _make_item(
        "homepage",
        "homepage",
        data={"title": "Home", "template": "homepage"},
        body="# Welcome",
    )
    config = _make_config(tmp_path)
    router = _make_router([homepage])
    svc = SiteBuildService(config, router)

    captured_manifest = {}

    def fake_run(cmd, **kwargs):
        manifest_path = kwargs["env"]["CAULDRON_MANIFEST"]
        tmp_out = kwargs["env"]["CAULDRON_OUTDIR"]
        # Write a fake output dir so the copy succeeds
        Path(tmp_out).mkdir(parents=True, exist_ok=True)
        with open(manifest_path, "r", encoding="utf-8") as f:
            captured_manifest.update(json.load(f))
        return _ok_proc()

    with patch("subprocess.run", side_effect=fake_run):
        result = svc.build()

    assert result.ok is True
    assert captured_manifest["pages"][0]["route"] == "/"


# ---------------------------------------------------------------------------
# Non-homepage slug routing
# ---------------------------------------------------------------------------


def test_non_homepage_gets_slug_route(tmp_path: Path):
    """Non-homepage items get route /<slug>/."""
    about = _make_item(
        "page.about",
        "about",
        data={"title": "About"},
        body="About us.",
    )
    config = _make_config(tmp_path)
    router = _make_router([about])
    svc = SiteBuildService(config, router)

    captured_manifest = {}

    def fake_run(cmd, **kwargs):
        manifest_path = kwargs["env"]["CAULDRON_MANIFEST"]
        tmp_out = kwargs["env"]["CAULDRON_OUTDIR"]
        Path(tmp_out).mkdir(parents=True, exist_ok=True)
        with open(manifest_path, "r", encoding="utf-8") as f:
            captured_manifest.update(json.load(f))
        return _ok_proc()

    with patch("subprocess.run", side_effect=fake_run):
        result = svc.build()

    assert result.ok is True
    assert captured_manifest["pages"][0]["route"] == "/about/"


# ---------------------------------------------------------------------------
# Manifest contents
# ---------------------------------------------------------------------------


def test_manifest_contains_all_page_fields(tmp_path: Path):
    """All expected fields are present in the manifest JSON."""
    item = _make_item(
        "homepage",
        "homepage",
        data={
            "title": "Home",
            "navigation_title": "Home",
            "summary": "Welcome page",
            "template": "homepage",
            "seo_title": "SEO Home",
            "meta_description": "Meta desc",
            "canonical_url": "https://example.com/",
            "robots_index": True,
            "robots_follow": False,
            "social_title": "Social Home",
            "social_description": "Social desc",
            "social_image": "/img/home.png",
        },
        body="# Welcome\n\nWelcome.",
    )
    config = _make_config(tmp_path)
    router = _make_router([item])
    svc = SiteBuildService(config, router)

    captured_page = {}

    def fake_run(cmd, **kwargs):
        manifest_path = kwargs["env"]["CAULDRON_MANIFEST"]
        tmp_out = kwargs["env"]["CAULDRON_OUTDIR"]
        Path(tmp_out).mkdir(parents=True, exist_ok=True)
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        captured_page.update(data["pages"][0])
        return _ok_proc()

    with patch("subprocess.run", side_effect=fake_run):
        svc.build()

    expected_keys = {
        "id", "route", "title", "navigation_title", "summary", "body",
        "template", "seo_title", "meta_description", "canonical_url",
        "robots_index", "robots_follow", "social_title", "social_description",
        "social_image",
    }
    assert expected_keys <= set(captured_page.keys())
    assert captured_page["title"] == "Home"
    assert captured_page["body"] == "# Welcome\n\nWelcome."
    assert captured_page["robots_follow"] is False


# ---------------------------------------------------------------------------
# Successful build — output directory
# ---------------------------------------------------------------------------


def test_successful_build_creates_output_dir(tmp_path: Path):
    """A successful build copies the Astro output to output_root."""
    homepage = _make_item("homepage", "homepage", data={"title": "Home"}, body="Hello")
    config = _make_config(tmp_path)
    router = _make_router([homepage])
    svc = SiteBuildService(config, router)

    def fake_run(cmd, **kwargs):
        tmp_out = kwargs["env"]["CAULDRON_OUTDIR"]
        # Simulate Astro writing output files
        out_dir = Path(tmp_out)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "index.html").write_text("<html>Home</html>", encoding="utf-8")
        return _ok_proc()

    with patch("subprocess.run", side_effect=fake_run):
        result = svc.build()

    assert result.ok is True
    assert result.pages_built == 1
    output_root = Path(config.output_root)
    assert output_root.exists()
    assert (output_root / "index.html").exists()


# ---------------------------------------------------------------------------
# Failed build — leaves existing output untouched
# ---------------------------------------------------------------------------


def test_failed_build_leaves_existing_output_untouched(tmp_path: Path):
    """On Astro build failure, the existing output_root must not be modified."""
    homepage = _make_item("homepage", "homepage", data={"title": "Home"}, body="Hello")

    # Pre-create existing output
    output_root = tmp_path / "output"
    output_root.mkdir()
    existing_file = output_root / "index.html"
    existing_file.write_text("<html>Old</html>", encoding="utf-8")

    config = _make_config(tmp_path)
    router = _make_router([homepage])
    svc = SiteBuildService(config, router)

    with patch("subprocess.run", return_value=_fail_proc(returncode=1)):
        result = svc.build()

    assert result.ok is False
    assert "exited 1" in result.error
    # Existing output must still be intact
    assert existing_file.exists()
    assert existing_file.read_text(encoding="utf-8") == "<html>Old</html>"


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------


def test_build_timeout_returns_ok_false(tmp_path: Path):
    """subprocess.TimeoutExpired causes BuildResult(ok=False) with timeout message."""
    homepage = _make_item("homepage", "homepage", data={"title": "Home"}, body="Hello")
    config = _make_config(tmp_path, build_timeout=5)
    router = _make_router([homepage])
    svc = SiteBuildService(config, router)

    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["npm", "run", "build"], timeout=5)

    with patch("subprocess.run", side_effect=raise_timeout):
        result = svc.build()

    assert result.ok is False
    assert "timed out" in result.error
    assert "5s" in result.error


# ---------------------------------------------------------------------------
# Missing config
# ---------------------------------------------------------------------------


def test_build_missing_frontend_root_returns_error(tmp_path: Path):
    """Empty frontend_root short-circuits before any subprocess call."""
    config = SiteAstroConfig(
        frontend_root="",
        output_root=str(tmp_path / "out"),
    )
    router = _make_router([])
    svc = SiteBuildService(config, router)

    with patch("subprocess.run") as mock_run:
        result = svc.build()

    assert result.ok is False
    assert "frontend_root" in result.error
    mock_run.assert_not_called()


def test_build_missing_output_root_returns_error(tmp_path: Path):
    """Empty output_root short-circuits before any subprocess call."""
    config = SiteAstroConfig(
        frontend_root=str(tmp_path / "frontend"),
        output_root="",
    )
    router = _make_router([])
    svc = SiteBuildService(config, router)

    with patch("subprocess.run") as mock_run:
        result = svc.build()

    assert result.ok is False
    assert "output_root" in result.error
    mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# Router failure
# ---------------------------------------------------------------------------


def test_router_exception_returns_error(tmp_path: Path):
    """When the router raises, build returns ok=False with the error message."""
    config = _make_config(tmp_path)
    router = MagicMock()
    router.list_items.side_effect = RuntimeError("connection refused")
    svc = SiteBuildService(config, router)

    with patch("subprocess.run") as mock_run:
        result = svc.build()

    assert result.ok is False
    assert "connection refused" in result.error
    mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# Temp file cleanup
# ---------------------------------------------------------------------------


def test_manifest_temp_files_cleaned_up_after_success(tmp_path: Path):
    """After a successful build, the temp directory is removed."""
    homepage = _make_item("homepage", "homepage", data={"title": "Home"}, body="Hello")
    config = _make_config(tmp_path)
    router = _make_router([homepage])
    svc = SiteBuildService(config, router)

    created_tmp_dirs: list[str] = []
    original_mkdtemp = __import__("tempfile").mkdtemp

    def tracking_mkdtemp(**kwargs):
        d = original_mkdtemp(**kwargs)
        created_tmp_dirs.append(d)
        return d

    def fake_run(cmd, **kwargs):
        tmp_out = kwargs["env"]["CAULDRON_OUTDIR"]
        Path(tmp_out).mkdir(parents=True, exist_ok=True)
        return _ok_proc()

    with patch("tempfile.mkdtemp", side_effect=tracking_mkdtemp):
        with patch("subprocess.run", side_effect=fake_run):
            result = svc.build()

    assert result.ok is True
    for d in created_tmp_dirs:
        assert not Path(d).exists(), f"Temp dir {d!r} was not cleaned up"


def test_manifest_temp_files_cleaned_up_after_failure(tmp_path: Path):
    """After a failed build, the temp directory is still removed."""
    homepage = _make_item("homepage", "homepage", data={"title": "Home"}, body="Hello")
    config = _make_config(tmp_path)
    router = _make_router([homepage])
    svc = SiteBuildService(config, router)

    created_tmp_dirs: list[str] = []
    original_mkdtemp = __import__("tempfile").mkdtemp

    def tracking_mkdtemp(**kwargs):
        d = original_mkdtemp(**kwargs)
        created_tmp_dirs.append(d)
        return d

    with patch("tempfile.mkdtemp", side_effect=tracking_mkdtemp):
        with patch("subprocess.run", return_value=_fail_proc(returncode=2)):
            result = svc.build()

    assert result.ok is False
    for d in created_tmp_dirs:
        assert not Path(d).exists(), f"Temp dir {d!r} was not cleaned up"


# ---------------------------------------------------------------------------
# pages_built count
# ---------------------------------------------------------------------------


def test_pages_built_count_is_correct(tmp_path: Path):
    """pages_built reflects the number of published items passed to Astro."""
    items = [
        _make_item("homepage", "homepage", data={"title": "Home"}, body="Hello"),
        _make_item("page.about", "about", data={"title": "About"}, body="About"),
        _make_item("page.contact", "contact", data={"title": "Contact"}, body="Contact"),
    ]
    config = _make_config(tmp_path)
    router = _make_router(items)
    svc = SiteBuildService(config, router)

    def fake_run(cmd, **kwargs):
        tmp_out = kwargs["env"]["CAULDRON_OUTDIR"]
        Path(tmp_out).mkdir(parents=True, exist_ok=True)
        return _ok_proc()

    with patch("subprocess.run", side_effect=fake_run):
        result = svc.build()

    assert result.ok is True
    assert result.pages_built == 3


# ---------------------------------------------------------------------------
# build_log is propagated
# ---------------------------------------------------------------------------


def test_build_log_included_in_success(tmp_path: Path):
    homepage = _make_item("homepage", "homepage", data={"title": "Home"}, body="Hello")
    config = _make_config(tmp_path)
    router = _make_router([homepage])
    svc = SiteBuildService(config, router)

    def fake_run(cmd, **kwargs):
        tmp_out = kwargs["env"]["CAULDRON_OUTDIR"]
        Path(tmp_out).mkdir(parents=True, exist_ok=True)
        return _ok_proc(stdout="Astro output\n", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        result = svc.build()

    assert result.ok is True
    assert "Astro output" in result.build_log


def test_build_log_included_in_failure(tmp_path: Path):
    homepage = _make_item("homepage", "homepage", data={"title": "Home"}, body="Hello")
    config = _make_config(tmp_path)
    router = _make_router([homepage])
    svc = SiteBuildService(config, router)

    with patch("subprocess.run", return_value=_fail_proc(stderr="Build error!\n")):
        result = svc.build()

    assert result.ok is False
    assert "Build error!" in result.build_log


# ---------------------------------------------------------------------------
# Failure-injection tests for _promote_output
# ---------------------------------------------------------------------------


def test_staging_copy_failure_leaves_existing_output(tmp_path):
    """If staging copy fails, the existing output is preserved."""
    # Pre-create existing output
    output_root = tmp_path / "output"
    output_root.mkdir()
    (output_root / "index.html").write_text("<html>Old</html>")

    homepage = _make_item("homepage", "homepage", data={"title": "Home"}, body="Hello")
    config = _make_config(tmp_path)
    router = _make_router([homepage])
    svc = SiteBuildService(config, router)

    import shutil
    original_copytree = shutil.copytree

    def fail_copytree(src, dst, **kwargs):
        raise OSError("disk full")

    def fake_run(cmd, **kwargs):
        tmp_out = kwargs["env"]["CAULDRON_OUTDIR"]
        Path(tmp_out).mkdir(parents=True, exist_ok=True)
        (Path(tmp_out) / "index.html").write_text("<html>New</html>")
        return _ok_proc()

    with patch("subprocess.run", side_effect=fake_run):
        with patch("shutil.copytree", side_effect=fail_copytree):
            result = svc.build()

    assert result.ok is False
    # Old output must still be readable
    assert (output_root / "index.html").read_text() == "<html>Old</html>"


def test_first_rename_failure_leaves_existing_output(tmp_path):
    """If the one-time migration rename (output_root→releases/legacy) fails, existing output is preserved."""
    output_root = tmp_path / "output"
    output_root.mkdir()
    (output_root / "index.html").write_text("<html>Old</html>")

    homepage = _make_item("homepage", "homepage", data={"title": "Home"}, body="Hello")
    config = _make_config(tmp_path)
    router = _make_router([homepage])
    svc = SiteBuildService(config, router)

    real_rename = Path.rename
    call_count = [0]

    def fail_first_rename(self, target):
        call_count[0] += 1
        if call_count[0] == 1:  # First Path.rename is output_root → releases/legacy-<uuid>
            raise OSError("rename failed")
        return real_rename(self, target)

    def fake_run(cmd, **kwargs):
        tmp_out = kwargs["env"]["CAULDRON_OUTDIR"]
        Path(tmp_out).mkdir(parents=True, exist_ok=True)
        return _ok_proc()

    with patch("subprocess.run", side_effect=fake_run):
        with patch.object(Path, "rename", fail_first_rename):
            result = svc.build()

    assert result.ok is False
    assert (output_root / "index.html").read_text() == "<html>Old</html>"


def test_activation_rename_failure_restores_previous_output(tmp_path):
    """If the atomic symlink-activation os.rename fails, previous output is restored.

    The activation step is os.rename(next_link, output_root) — a single syscall.
    If it fails after the migration rename moved output_root aside, the exception
    handler must restore the migrated directory back to output_root.
    """
    output_root = tmp_path / "output"
    output_root.mkdir()
    (output_root / "index.html").write_text("<html>Old</html>")

    homepage = _make_item("homepage", "homepage", data={"title": "Home"}, body="Hello")
    config = _make_config(tmp_path)
    router = _make_router([homepage])
    svc = SiteBuildService(config, router)

    def fail_os_rename(src, dst):
        raise OSError("rename failed")

    def fake_run(cmd, **kwargs):
        tmp_out = kwargs["env"]["CAULDRON_OUTDIR"]
        Path(tmp_out).mkdir(parents=True, exist_ok=True)
        return _ok_proc()

    with patch("subprocess.run", side_effect=fake_run):
        with patch("cauldron_site_astro.service.os.rename", side_effect=fail_os_rename):
            result = svc.build()

    assert result.ok is False
    # Previous output must be restored
    assert output_root.exists()
    assert (output_root / "index.html").read_text() == "<html>Old</html>"


def test_existing_output_readable_after_every_failed_build(tmp_path):
    """After any build failure, the existing output must remain readable."""
    output_root = tmp_path / "output"
    output_root.mkdir()
    sentinel = output_root / "index.html"
    sentinel.write_text("<html>Sentinel</html>")

    homepage = _make_item("homepage", "homepage", data={"title": "Home"}, body="Hello")
    config = _make_config(tmp_path)
    router = _make_router([homepage])
    svc = SiteBuildService(config, router)

    # Failure mode: Astro returns non-zero
    with patch("subprocess.run", return_value=_fail_proc(returncode=1)):
        result = svc.build()
    assert result.ok is False
    assert sentinel.read_text() == "<html>Sentinel</html>"


# ---------------------------------------------------------------------------
# Theme in manifest
# ---------------------------------------------------------------------------


def test_manifest_includes_theme_key_without_theme_root(tmp_path: Path):
    """When theme_root is empty, manifest has theme.css_content == ''."""
    homepage = _make_item("homepage", "homepage", data={"title": "Home"}, body="Hello")
    config = _make_config(tmp_path)
    router = _make_router([homepage])
    svc = SiteBuildService(config, router)

    captured_manifest = {}

    def fake_run(cmd, **kwargs):
        manifest_path = kwargs["env"]["CAULDRON_MANIFEST"]
        tmp_out = kwargs["env"]["CAULDRON_OUTDIR"]
        Path(tmp_out).mkdir(parents=True, exist_ok=True)
        with open(manifest_path, "r", encoding="utf-8") as f:
            captured_manifest.update(json.load(f))
        return _ok_proc()

    with patch("subprocess.run", side_effect=fake_run):
        result = svc.build()

    assert result.ok is True
    assert "theme" in captured_manifest
    assert "css_content" in captured_manifest["theme"]
    assert captured_manifest["theme"]["css_content"] == ""


def test_manifest_includes_active_theme_css_when_theme_root_set(tmp_path: Path):
    """When theme_root has active.css, its content appears in manifest."""
    # Set up active.css
    theme_dir = tmp_path / "theme"
    theme_dir.mkdir()
    (theme_dir / "active.css").write_text("body { background: #fff; }", encoding="utf-8")

    homepage = _make_item("homepage", "homepage", data={"title": "Home"}, body="Hello")
    config = _make_config(tmp_path, theme_root=str(theme_dir))
    router = _make_router([homepage])
    svc = SiteBuildService(config, router)

    captured_manifest = {}

    def fake_run(cmd, **kwargs):
        manifest_path = kwargs["env"]["CAULDRON_MANIFEST"]
        tmp_out = kwargs["env"]["CAULDRON_OUTDIR"]
        Path(tmp_out).mkdir(parents=True, exist_ok=True)
        with open(manifest_path, "r", encoding="utf-8") as f:
            captured_manifest.update(json.load(f))
        return _ok_proc()

    with patch("subprocess.run", side_effect=fake_run):
        result = svc.build()

    assert result.ok is True
    assert captured_manifest["theme"]["css_content"] == "body { background: #fff; }"


# ---------------------------------------------------------------------------
# build_preview
# ---------------------------------------------------------------------------


def test_build_preview_missing_frontend_root_returns_error(tmp_path: Path):
    """Empty frontend_root short-circuits before any subprocess call."""
    config = SiteAstroConfig(
        frontend_root="",
        output_root=str(tmp_path / "out"),
        previews_root=str(tmp_path / "previews"),
    )
    router = _make_router([])
    svc = SiteBuildService(config, router)

    with patch("subprocess.run") as mock_run:
        result = svc.build_preview(output_dir=tmp_path / "preview_out")

    assert result.ok is False
    assert "frontend_root" in result.error
    mock_run.assert_not_called()


def test_build_preview_no_pages_creates_empty_output(tmp_path: Path):
    """When router returns no items, preview creates an empty output dir."""
    config = _make_config(tmp_path)
    router = _make_router([])
    svc = SiteBuildService(config, router)
    preview_out = tmp_path / "preview"

    with patch("subprocess.run") as mock_run:
        result = svc.build_preview(output_dir=preview_out)

    assert result.ok is True
    assert result.pages_built == 0
    mock_run.assert_not_called()
    assert preview_out.exists()


def test_build_preview_includes_only_scoped_drafts(tmp_path: Path):
    """build_preview includes ONLY drafts listed in item_ids_to_include.

    The old behaviour of surfacing every draft on the workspace is a leak:
    unrelated in-flight authoring work must not appear in another user's
    preview. The router is called twice — once for the published baseline
    (include_drafts=False) and once for opted-in drafts.
    """
    draft_included = _make_item(
        "page.draft.in", "draft-in", status="draft", data={"title": "In"},
    )
    draft_excluded = _make_item(
        "page.draft.out", "draft-out", status="draft", data={"title": "Out"},
    )
    published = _make_item(
        "homepage", "homepage", status="published", data={"title": "Home"},
    )

    config = _make_config(tmp_path)
    router = MagicMock()

    def fake_list_items(collection, include_drafts=False):
        if include_drafts:
            return [draft_included, draft_excluded, published]
        return [published]

    router.list_items.side_effect = fake_list_items
    svc = SiteBuildService(config, router)
    preview_out = tmp_path / "preview"

    captured_manifest = {}

    def fake_run(cmd, **kwargs):
        manifest_path = kwargs["env"]["CAULDRON_MANIFEST"]
        preview_out.mkdir(parents=True, exist_ok=True)
        with open(manifest_path, "r", encoding="utf-8") as f:
            captured_manifest.update(json.load(f))
        return _ok_proc()

    with patch("subprocess.run", side_effect=fake_run):
        result = svc.build_preview(
            output_dir=preview_out,
            item_ids_to_include=["page.draft.in"],
        )

    assert result.ok is True
    page_ids = {p["id"] for p in captured_manifest["pages"]}
    assert "homepage" in page_ids                # baseline (published)
    assert "page.draft.in" in page_ids           # opted-in
    assert "page.draft.out" not in page_ids      # NOT opted-in => excluded


def test_build_preview_default_excludes_all_drafts(tmp_path: Path):
    """With no item_ids_to_include, the preview shows only published items."""
    draft = _make_item("page.draft", "draft-page", status="draft", data={"title": "Draft"})
    published = _make_item("homepage", "homepage", status="published", data={"title": "Home"})
    config = _make_config(tmp_path)
    router = MagicMock()

    def fake_list_items(collection, include_drafts=False):
        if include_drafts:
            return [draft, published]
        return [published]

    router.list_items.side_effect = fake_list_items
    svc = SiteBuildService(config, router)
    preview_out = tmp_path / "preview"

    captured_manifest = {}

    def fake_run(cmd, **kwargs):
        manifest_path = kwargs["env"]["CAULDRON_MANIFEST"]
        preview_out.mkdir(parents=True, exist_ok=True)
        with open(manifest_path, "r", encoding="utf-8") as f:
            captured_manifest.update(json.load(f))
        return _ok_proc()

    with patch("subprocess.run", side_effect=fake_run):
        result = svc.build_preview(output_dir=preview_out)

    assert result.ok is True
    page_ids = {p["id"] for p in captured_manifest["pages"]}
    assert "homepage" in page_ids
    assert "page.draft" not in page_ids


def test_build_preview_uses_provided_theme_css(tmp_path: Path):
    """theme_css arg overrides the active theme in the preview manifest."""
    homepage = _make_item("homepage", "homepage", data={"title": "Home"})
    config = _make_config(tmp_path)
    router = _make_router([homepage])
    svc = SiteBuildService(config, router)
    preview_out = tmp_path / "preview"

    captured_manifest = {}

    def fake_run(cmd, **kwargs):
        manifest_path = kwargs["env"]["CAULDRON_MANIFEST"]
        preview_out.mkdir(parents=True, exist_ok=True)
        with open(manifest_path, "r", encoding="utf-8") as f:
            captured_manifest.update(json.load(f))
        return _ok_proc()

    custom_css = "body { color: green; }"
    with patch("subprocess.run", side_effect=fake_run):
        result = svc.build_preview(output_dir=preview_out, theme_css=custom_css)

    assert result.ok is True
    assert captured_manifest["theme"]["css_content"] == custom_css


def test_build_preview_sets_cauldron_is_preview_env(tmp_path: Path):
    """CAULDRON_IS_PREVIEW=1 is set in the subprocess environment."""
    homepage = _make_item("homepage", "homepage", data={"title": "Home"})
    config = _make_config(tmp_path)
    router = _make_router([homepage])
    svc = SiteBuildService(config, router)
    preview_out = tmp_path / "preview"

    captured_env = {}

    def fake_run(cmd, **kwargs):
        captured_env.update(kwargs["env"])
        preview_out.mkdir(parents=True, exist_ok=True)
        return _ok_proc()

    with patch("subprocess.run", side_effect=fake_run):
        svc.build_preview(output_dir=preview_out)

    assert captured_env.get("CAULDRON_IS_PREVIEW") == "1"


def test_build_preview_does_not_touch_output_root(tmp_path: Path):
    """build_preview writes to output_dir, not output_root."""
    output_root = tmp_path / "output"
    output_root.mkdir()
    (output_root / "existing.html").write_text("<html>Live</html>")

    homepage = _make_item("homepage", "homepage", data={"title": "Home"})
    config = _make_config(tmp_path)
    router = _make_router([homepage])
    svc = SiteBuildService(config, router)
    preview_out = tmp_path / "preview"

    def fake_run(cmd, **kwargs):
        preview_out.mkdir(parents=True, exist_ok=True)
        return _ok_proc()

    with patch("subprocess.run", side_effect=fake_run):
        result = svc.build_preview(output_dir=preview_out)

    assert result.ok is True
    # Live output_root must be untouched
    assert (output_root / "existing.html").read_text() == "<html>Live</html>"


def test_build_preview_failed_build_returns_error(tmp_path: Path):
    """When Astro returns non-zero, build_preview returns ok=False."""
    homepage = _make_item("homepage", "homepage", data={"title": "Home"})
    config = _make_config(tmp_path)
    router = _make_router([homepage])
    svc = SiteBuildService(config, router)
    preview_out = tmp_path / "preview"

    with patch("subprocess.run", return_value=_fail_proc(returncode=1, stderr="Build error")):
        result = svc.build_preview(output_dir=preview_out)

    assert result.ok is False
    assert "exited 1" in result.error


def test_build_preview_timeout_returns_error(tmp_path: Path):
    """TimeoutExpired causes BuildResult(ok=False) with timeout message."""
    homepage = _make_item("homepage", "homepage", data={"title": "Home"})
    config = _make_config(tmp_path, build_timeout=5)
    router = _make_router([homepage])
    svc = SiteBuildService(config, router)
    preview_out = tmp_path / "preview"

    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["npm", "run", "build"], timeout=5)

    with patch("subprocess.run", side_effect=raise_timeout):
        result = svc.build_preview(output_dir=preview_out)

    assert result.ok is False
    assert "timed out" in result.error


def test_build_preview_extra_items_win_deduplication(tmp_path: Path):
    """Extra items override router items with the same id."""
    from types import SimpleNamespace

    router_item = _make_item("homepage", "homepage", data={"title": "Old Home"})
    extra_item = SimpleNamespace(
        id="homepage",
        slug="homepage",
        status="draft",
        data={"title": "New Home Draft"},
        body="Updated body",
    )
    config = _make_config(tmp_path)
    router = _make_router([router_item])
    svc = SiteBuildService(config, router)
    preview_out = tmp_path / "preview"

    captured_manifest = {}

    def fake_run(cmd, **kwargs):
        manifest_path = kwargs["env"]["CAULDRON_MANIFEST"]
        preview_out.mkdir(parents=True, exist_ok=True)
        with open(manifest_path, "r", encoding="utf-8") as f:
            captured_manifest.update(json.load(f))
        return _ok_proc()

    with patch("subprocess.run", side_effect=fake_run):
        result = svc.build_preview(output_dir=preview_out, extra_items=[extra_item])

    assert result.ok is True
    # Only one page (deduplicated), and extra_item wins
    assert len(captured_manifest["pages"]) == 1
    assert captured_manifest["pages"][0]["title"] == "New Home Draft"


# ---------------------------------------------------------------------------
# migrate_output_root management command
# ---------------------------------------------------------------------------


def test_migrate_output_root_converts_real_dir_to_symlink(tmp_path):
    """migrate_output_root converts an existing real directory to a symlink release."""
    from cauldron_site_astro.management.commands.cauldron_migrate_output_root import (
        migrate_output_root,
    )

    output_root = tmp_path / "public"
    output_root.mkdir()
    (output_root / "index.html").write_text("<html>legacy</html>")
    (output_root / "about").mkdir()
    (output_root / "about" / "index.html").write_text("<html>about</html>")

    release = migrate_output_root(output_root)

    # output_root is now a symlink
    assert output_root.is_symlink(), "output_root must be a symlink after migration"
    # It points to the new release inside .releases/
    releases_dir = tmp_path / "public.releases"
    assert releases_dir.is_dir(), "releases dir must exist"
    assert release.parent == releases_dir, "release must be inside .releases/"

    # Content is preserved via the symlink
    assert (output_root / "index.html").read_text() == "<html>legacy</html>"
    assert (output_root / "about" / "index.html").read_text() == "<html>about</html>"

    # The legacy copy is cleaned up (only the new release remains)
    remaining = list(releases_dir.iterdir())
    assert len(remaining) == 1, "only the new release should remain; legacy must be deleted"
    assert remaining[0] == release


def test_migrate_output_root_already_symlink_raises(tmp_path):
    """migrate_output_root raises ValueError if output_root is already a symlink."""
    from cauldron_site_astro.management.commands.cauldron_migrate_output_root import (
        migrate_output_root,
    )

    target = tmp_path / "release"
    target.mkdir()
    link = tmp_path / "public"
    link.symlink_to(target)

    with pytest.raises(ValueError, match="already a symlink"):
        migrate_output_root(link)


def test_migrate_output_root_missing_raises(tmp_path):
    """migrate_output_root raises FileNotFoundError if output_root does not exist."""
    from cauldron_site_astro.management.commands.cauldron_migrate_output_root import (
        migrate_output_root,
    )

    with pytest.raises(FileNotFoundError):
        migrate_output_root(tmp_path / "nonexistent")


def test_migrate_output_root_rollback_on_activation_failure(tmp_path):
    """If the os.rename activation fails, the original directory is restored."""
    from cauldron_site_astro.management.commands.cauldron_migrate_output_root import (
        migrate_output_root,
    )

    output_root = tmp_path / "public"
    output_root.mkdir()
    (output_root / "index.html").write_text("<html>original</html>")

    def fail_os_rename(src, dst):
        raise OSError("simulated rename failure")

    with pytest.raises(OSError, match="simulated rename failure"):
        with patch("cauldron_site_astro.management.commands.cauldron_migrate_output_root.os.rename", side_effect=fail_os_rename):
            migrate_output_root(output_root)

    # The original real directory must be restored
    assert output_root.is_dir() and not output_root.is_symlink()
    assert (output_root / "index.html").read_text() == "<html>original</html>"


def test_migrate_output_root_followed_by_atomic_promote(tmp_path):
    """After migration, subsequent promote_output calls are fully atomic (no real-dir branch)."""
    from cauldron_site_astro.management.commands.cauldron_migrate_output_root import (
        migrate_output_root,
    )

    output_root = tmp_path / "public"
    output_root.mkdir()
    (output_root / "old.html").write_text("<html>old</html>")

    migrate_output_root(output_root)
    assert output_root.is_symlink()

    # Now a subsequent promote_output must take the is_symlink() branch (atomic, no window)
    new_build = tmp_path / "new_build"
    new_build.mkdir()
    (new_build / "new.html").write_text("<html>new</html>")

    from cauldron_site_astro.service import _promote_output

    _promote_output(new_build, output_root)

    assert output_root.is_symlink()
    assert (output_root / "new.html").read_text() == "<html>new</html>"
    assert not (output_root / "old.html").exists()
