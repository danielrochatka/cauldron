"""
Integration test for the 'Timmy' workflow:
AI requests -> SiteChangeSet -> preview -> publish.

Each test drives the site tools end-to-end with a mocked SiteBuildService
so we can verify the SiteChangeSet lifecycle transitions, the
preview_url contract (must be a Django URL path, never a filesystem
path), and the "no CSS promotion on failed build" invariant.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

pytestmark = pytest.mark.django_db


def _make_config(tmp_path, theme_root="", previews_root=""):
    from cauldron_site_astro.config import SiteAstroConfig
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    output = tmp_path / "output"
    return SiteAstroConfig(
        frontend_root=str(frontend),
        output_root=str(output),
        npm_command="npm",
        build_timeout=30,
        theme_root=theme_root,
        previews_root=previews_root,
    )


def _make_build_result(ok=True, pages_built=2, output_dir="/tmp/out", error=""):
    from cauldron_site_astro.service import BuildResult
    return BuildResult(
        ok=ok, pages_built=pages_built, output_dir=output_dir, error=error,
    )


def _ctx(username: str, run_id: str = ""):
    from django.contrib.auth import get_user_model
    from cauldron_ai_admin.tools import AdminAIToolContext

    User = get_user_model()
    user, _ = User.objects.get_or_create(username=username)
    return AdminAIToolContext(actor=user, run_id=run_id, correlation_id="c-" + username)


def test_prepare_change_set_creates_db_record(tmp_path):
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_site_astro.site_tools import _handle_prepare_change_set

    ctx = _ctx("timmy-test")

    previews_root = tmp_path / "previews"
    config = _make_config(tmp_path, previews_root=str(previews_root))
    build_result = _make_build_result(ok=True, pages_built=2)
    svc = MagicMock()
    svc._config = config
    svc.build_preview.return_value = build_result

    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        result = _handle_prepare_change_set(
            ctx,
            content_request_ids=["req-001", "req-002"],
            theme_css="body { color: blue; }",
        )

    assert result.success is True
    cs_id = result.data["change_set_id"]
    cs = SiteChangeSet.objects.get(id=cs_id)
    assert cs.status == SiteChangeSet.DRAFT_READY
    assert cs.content_request_ids == ["req-001", "req-002"]
    assert cs.staged_theme_css == "body { color: blue; }"
    assert cs.draft_ready_at is not None
    assert cs.creator == ctx.actor


def test_prepare_change_set_preview_failure_sets_status(tmp_path):
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_site_astro.site_tools import _handle_prepare_change_set

    ctx = _ctx("timmy-fail")

    previews_root = tmp_path / "previews"
    config = _make_config(tmp_path, previews_root=str(previews_root))
    build_result = _make_build_result(ok=False, pages_built=0)
    build_result.error = "Build crashed"
    svc = MagicMock()
    svc._config = config
    svc.build_preview.return_value = build_result

    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        result = _handle_prepare_change_set(ctx, content_request_ids=["req-003"])

    assert result.success is False
    cs_id = result.data["change_set_id"]
    cs = SiteChangeSet.objects.get(id=cs_id)
    assert cs.status == SiteChangeSet.PREVIEW_FAILED


def test_inspect_preview_returns_preview_url(tmp_path):
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_site_astro.site_tools import _handle_inspect_preview

    ctx = _ctx("timmy-inspect")

    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        content_request_ids=["req-004"],
        preview_dir="preview-subdir",
        draft_ready_at=timezone.now(),
    )

    previews_root = tmp_path / "previews"
    preview_dir = previews_root / "preview-subdir"
    preview_dir.mkdir(parents=True)
    (preview_dir / "index.html").write_text("<html>Preview</html>")

    config = _make_config(tmp_path, previews_root=str(previews_root))
    svc = MagicMock()
    svc._config = config

    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        result = _handle_inspect_preview(ctx, change_set_id=str(cs.id))

    assert result.success is True
    assert "preview_url" in result.data
    # preview_url must be a Django URL path, never a filesystem path.
    assert result.data["preview_url"].startswith("/")
    assert "/tmp" not in result.data["preview_url"]
    assert str(previews_root) not in result.data["preview_url"]


def test_publish_requires_draft_ready_status():
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_site_astro.site_tools import _handle_publish

    ctx = _ctx("timmy-pub")

    cs = SiteChangeSet.objects.create(status=SiteChangeSet.PREPARING)
    result = _handle_publish(ctx, change_set_id=str(cs.id), confirm=True)
    assert result.success is False
    assert "draft_ready" in result.message.lower()


def test_publish_does_not_promote_css_on_build_failure(tmp_path):
    """Staged CSS must NOT be promoted if the build fails."""
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_site_astro.site_tools import _handle_publish
    from cauldron_site_astro.theme import SiteThemeService

    ctx = _ctx("timmy-css")

    theme_dir = tmp_path / "theme"
    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        content_request_ids=[],
        staged_theme_css="body { color: red; }",
        preview_dir="some-preview",
    )

    config = _make_config(tmp_path, theme_root=str(theme_dir))
    build_result = _make_build_result(ok=False)
    build_result.error = "Build failed"
    svc = MagicMock()
    svc._config = config
    # Publish now builds via build_preview (not build) so draft content is
    # included without being applied first.
    svc.build_preview.return_value = build_result

    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        result = _handle_publish(ctx, change_set_id=str(cs.id), confirm=True)

    assert result.success is False
    theme_svc = SiteThemeService(theme_dir)
    assert theme_svc.get_active_css() == ""

    cs.refresh_from_db()
    assert cs.status == SiteChangeSet.PUBLISH_FAILED


def test_content_not_published_on_build_failure(tmp_path):
    """Content change requests must NOT be applied if the build fails.

    Publish is atomic with respect to the build: validate → build → apply.
    A failed build leaves the content store unchanged (items remain draft).
    """
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_site_astro.site_tools import _handle_publish

    ctx = _ctx("timmy-atomic")

    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        content_request_ids=["req-atomicity"],
        staged_theme_css="",
        affected_item_ids=["item-x"],
    )

    config = _make_config(tmp_path)
    build_result = _make_build_result(ok=False)
    build_result.error = "Astro crashed"
    svc = MagicMock()
    svc._config = config
    svc.build_preview.return_value = build_result

    fake_content_service = MagicMock()
    fake_validation = MagicMock(ok=True, request_version=1)
    fake_content_service.validate_change_request.return_value = fake_validation

    with patch(
        "cauldron_site_astro.site_tools._get_content_operation_service",
        return_value=fake_content_service,
    ):
        with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
            result = _handle_publish(ctx, change_set_id=str(cs.id), confirm=True)

    assert result.success is False

    # Validation ran (read-only pre-flight), but apply was never called.
    fake_content_service.validate_change_request.assert_called_once()
    fake_content_service.apply_change_request.assert_not_called()

    cs.refresh_from_db()
    assert cs.status == SiteChangeSet.PUBLISH_FAILED


def test_full_workflow_prepare_then_inspect_then_publish(tmp_path):
    """End-to-end: theme-only change set goes through the full lifecycle.

    This exercises the multi-tool sequence the Admin AI is expected to
    follow: prepare_change_set -> inspect_preview -> publish, ensuring
    each hop persists the correct status transition and returns only
    safe URL paths.
    """
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_site_astro.site_tools import (
        _handle_prepare_change_set,
        _handle_inspect_preview,
        _handle_publish,
    )
    from cauldron_site_astro.theme import SiteThemeService

    ctx = _ctx("timmy-full", run_id="not-a-uuid")  # exercise UUID coercion

    previews_root = tmp_path / "previews"
    theme_dir = tmp_path / "theme"
    config = _make_config(tmp_path, theme_root=str(theme_dir), previews_root=str(previews_root))
    svc = MagicMock()
    svc._config = config

    # 1. prepare
    def fake_build_preview(**kwargs):
        out = Path(kwargs["output_dir"])
        out.mkdir(parents=True, exist_ok=True)
        (out / "index.html").write_text("<html>x</html>")
        return _make_build_result(ok=True, pages_built=1, output_dir=str(out))

    svc.build_preview.side_effect = fake_build_preview

    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        prep = _handle_prepare_change_set(
            ctx,
            # A single fake content request id — the publish step below
            # short-circuits by patching the workflow so we never actually
            # need to hit ContentOperationService in this narrow test.
            content_request_ids=["req-only-theme"],
            theme_css="body { background: purple; }",
        )

    assert prep.success is True
    cs_id = prep.data["change_set_id"]

    # 2. inspect
    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        insp = _handle_inspect_preview(ctx, change_set_id=cs_id)
    assert insp.success is True
    assert insp.data["status"] == SiteChangeSet.DRAFT_READY
    assert insp.data["preview_url"].startswith("/")

    # 3. publish — stub out the content operations service so the publish
    # loop successfully "applies" the fake request id without needing a
    # real workspace-backed service.  Publish now calls build_preview
    # (not build) so the draft items are included without being applied
    # first; promote_output is a MagicMock no-op on the fake svc.
    svc.build_preview.side_effect = fake_build_preview

    fake_content_service = MagicMock()
    fake_ok = MagicMock()
    fake_ok.ok = True
    fake_ok.request_version = 1
    fake_content_service.validate_change_request.return_value = fake_ok
    fake_content_service.apply_change_request.return_value = fake_ok

    with patch(
        "cauldron_site_astro.site_tools._get_content_operation_service",
        return_value=fake_content_service,
    ):
        with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
            pub = _handle_publish(ctx, change_set_id=cs_id, confirm=True)

    assert pub.success is True, pub.message
    assert pub.data["preview_url"] == "/"

    cs = SiteChangeSet.objects.get(id=cs_id)
    assert cs.status == SiteChangeSet.PUBLISHED
    # Staged CSS was promoted on successful publish.
    assert SiteThemeService(theme_dir).get_active_css() == "body { background: purple; }"
