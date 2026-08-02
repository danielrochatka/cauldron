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
    from cauldron_site_astro.site_tools import _handle_prepare_change_set as handle_prepare_change_set

    ctx = _ctx("timmy-test")

    previews_root = tmp_path / "previews"
    config = _make_config(tmp_path, previews_root=str(previews_root))
    build_result = _make_build_result(ok=True, pages_built=2)
    svc = MagicMock()
    svc._config = config
    svc.build_preview.return_value = build_result

    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        result = handle_prepare_change_set(
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
    from cauldron_site_astro.site_tools import _handle_prepare_change_set as handle_prepare_change_set

    ctx = _ctx("timmy-fail")

    previews_root = tmp_path / "previews"
    config = _make_config(tmp_path, previews_root=str(previews_root))
    build_result = _make_build_result(ok=False, pages_built=0)
    build_result.error = "Build crashed"
    svc = MagicMock()
    svc._config = config
    svc.build_preview.return_value = build_result

    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        result = handle_prepare_change_set(ctx, content_request_ids=["req-003"])

    assert result.success is False
    cs_id = result.data["change_set_id"]
    cs = SiteChangeSet.objects.get(id=cs_id)
    assert cs.status == SiteChangeSet.PREVIEW_FAILED


def test_inspect_preview_returns_preview_url(tmp_path):
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_site_astro.site_tools import _handle_inspect_preview as handle_inspect_preview

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
        result = handle_inspect_preview(ctx, change_set_id=str(cs.id))

    assert result.success is True
    assert "preview_url" in result.data
    # preview_url must be a Django URL path, never a filesystem path.
    assert result.data["preview_url"].startswith("/")
    assert "/tmp" not in result.data["preview_url"]
    assert str(previews_root) not in result.data["preview_url"]


def test_publish_requires_draft_ready_status():
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_site_astro.site_tools import _handle_publish as handle_publish

    ctx = _ctx("timmy-pub")

    cs = SiteChangeSet.objects.create(status=SiteChangeSet.PREPARING)
    result = handle_publish(ctx, change_set_id=str(cs.id), confirm=True)
    assert result.success is False
    assert "draft_ready" in result.message.lower()


def test_publish_does_not_promote_css_on_build_failure(tmp_path):
    """Staged CSS must NOT be promoted if the build fails."""
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_site_astro.site_tools import _handle_publish as handle_publish
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
        result = handle_publish(ctx, change_set_id=str(cs.id), confirm=True)

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
    from cauldron_site_astro.site_tools import _handle_publish as handle_publish

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
            result = handle_publish(ctx, change_set_id=str(cs.id), confirm=True)

    assert result.success is False

    # Validation ran (read-only pre-flight), but apply was never called.
    fake_content_service.validate_change_request.assert_called_once()
    fake_content_service.apply_change_request.assert_not_called()

    cs.refresh_from_db()
    assert cs.status == SiteChangeSet.PUBLISH_FAILED


# ---------------------------------------------------------------------------
# Post-build failure path tests: snapshot/restore guarantees
# ---------------------------------------------------------------------------


def _make_real_svc(config):
    """Real SiteBuildService with a mock router (no DB access needed)."""
    from unittest.mock import MagicMock
    from cauldron_site_astro.service import SiteBuildService
    return SiteBuildService(config, MagicMock())


def _fake_build_ok(new_filename="new.html"):
    """Return a build_preview side_effect that writes one file to output_dir."""
    from cauldron_site_astro.service import BuildResult

    def _impl(**kwargs):
        out = Path(kwargs["output_dir"])
        out.mkdir(parents=True, exist_ok=True)
        (out / new_filename).write_text("<html>new</html>")
        return BuildResult(ok=True, pages_built=1, output_dir=str(out))

    return _impl


def test_output_promotion_failure__content_unpublished_previous_output_intact(tmp_path):
    """If output promotion fails, no content requests are applied.

    _promote_output_snapshotted fails before performing any rename, so
    output_root content is entirely unchanged after the failed publish.
    """
    from unittest.mock import MagicMock, patch
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_site_astro.site_tools import _handle_publish as handle_publish

    ctx = _ctx("p-out-fail")
    config = _make_config(tmp_path)
    output_root = Path(config.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "original.html").write_text("<html>original</html>")

    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        content_request_ids=["req-out"],
        affected_item_ids=["item-out"],
    )

    svc = _make_real_svc(config)
    svc.build_preview = MagicMock(side_effect=_fake_build_ok())
    svc.promote_output_with_backup = MagicMock(side_effect=OSError("no space left"))

    fake_cs = MagicMock(ok=True, request_version=1)
    fake_svc = MagicMock()
    fake_svc.validate_change_request.return_value = fake_cs

    with patch("cauldron_site_astro.site_tools._get_content_operation_service", return_value=fake_svc):
        with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
            result = handle_publish(ctx, change_set_id=str(cs.id), confirm=True)

    assert result.success is False
    fake_svc.apply_change_request.assert_not_called()

    assert (output_root / "original.html").exists(), "previous output must be intact"
    assert not (output_root / "new.html").exists()

    cs.refresh_from_db()
    assert cs.status == SiteChangeSet.PUBLISH_FAILED


def test_css_promotion_failure__content_unpublished_output_restored(tmp_path):
    """If CSS promotion fails after the output swap, the output is restored.

    Sequence: output promoted → CSS promote raises → output restored from
    snapshot → no DB changes applied. active.css is never created.
    """
    from unittest.mock import MagicMock, patch
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_site_astro.site_tools import _handle_publish as handle_publish
    from cauldron_site_astro.theme import SiteThemeService

    ctx = _ctx("p-css-fail")
    theme_dir = tmp_path / "theme"
    config = _make_config(tmp_path, theme_root=str(theme_dir))
    output_root = Path(config.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "original.html").write_text("<html>original</html>")

    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        content_request_ids=["req-css"],
        staged_theme_css="body { color: crimson; }",
        affected_item_ids=["item-css"],
    )

    svc = _make_real_svc(config)
    svc.build_preview = MagicMock(side_effect=_fake_build_ok())

    fake_cs = MagicMock(ok=True, request_version=1)
    fake_content_svc = MagicMock()
    fake_content_svc.validate_change_request.return_value = fake_cs

    with patch("cauldron_site_astro.site_tools._get_content_operation_service", return_value=fake_content_svc):
        with patch("cauldron_site_astro.theme.SiteThemeService.promote_staged", side_effect=OSError("disk full")):
            with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
                result = handle_publish(ctx, change_set_id=str(cs.id), confirm=True)

    assert result.success is False
    fake_content_svc.apply_change_request.assert_not_called()

    assert SiteThemeService(theme_dir).get_active_css() == "", "active CSS must be unchanged"
    assert (output_root / "original.html").exists(), "previous output must be restored"
    assert not (output_root / "new.html").exists(), "new output must not remain live"

    cs.refresh_from_db()
    assert cs.status == SiteChangeSet.PUBLISH_FAILED


def test_db_apply_failure__output_and_css_restored(tmp_path):
    """If the DB transaction fails, both output and active CSS are rolled back.

    By the time apply runs, both FS promotions have succeeded. The
    transaction.atomic() block raises, triggering DB rollback. The publish
    handler then uses the output snapshot and the in-memory CSS snapshot to
    restore both FS artefacts.
    """
    from unittest.mock import MagicMock, patch
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_site_astro.site_tools import _handle_publish as handle_publish
    from cauldron_site_astro.theme import SiteThemeService

    ctx = _ctx("p-db-fail")
    theme_dir = tmp_path / "theme"
    config = _make_config(tmp_path, theme_root=str(theme_dir))
    output_root = Path(config.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "original.html").write_text("<html>original</html>")

    prev_css = "body { color: green; }"
    SiteThemeService(theme_dir).set_active_css(prev_css)

    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        content_request_ids=["req-db"],
        staged_theme_css="body { color: blue; }",
        affected_item_ids=["item-db"],
    )

    svc = _make_real_svc(config)
    svc.build_preview = MagicMock(side_effect=_fake_build_ok())

    fake_validation = MagicMock(ok=True, request_version=1)
    fake_content_svc = MagicMock()
    fake_content_svc.validate_change_request.return_value = fake_validation
    fake_content_svc.apply_change_request.side_effect = Exception("DB constraint failed")

    with patch("cauldron_site_astro.site_tools._get_content_operation_service", return_value=fake_content_svc):
        with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
            result = handle_publish(ctx, change_set_id=str(cs.id), confirm=True)

    assert result.success is False
    fake_content_svc.apply_change_request.assert_called_once()

    assert (output_root / "original.html").exists(), "previous output must be restored"
    assert not (output_root / "new.html").exists(), "new output must not remain after DB rollback"
    assert SiteThemeService(theme_dir).get_active_css() == prev_css, \
        "active CSS must be restored after DB rollback"

    cs.refresh_from_db()
    assert cs.status == SiteChangeSet.PUBLISH_FAILED


def test_retry_publish_failed_succeeds_without_double_apply(tmp_path):
    """A PUBLISH_FAILED change set can be retried and completes cleanly.

    Because all applies run inside a single transaction.atomic() that
    either fully commits or fully rolls back, a retry always starts from
    a clean content-store state; req-retry is applied exactly once.
    """
    from unittest.mock import MagicMock, patch
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_site_astro.site_tools import _handle_publish as handle_publish

    ctx = _ctx("p-retry")
    config = _make_config(tmp_path)

    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.PUBLISH_FAILED,
        content_request_ids=["req-retry"],
        staged_theme_css="",
        affected_item_ids=["item-retry"],
    )

    svc = _make_real_svc(config)
    svc.build_preview = MagicMock(side_effect=_fake_build_ok())

    fake_validation = MagicMock(ok=True, request_version=1)
    fake_apply_ok = MagicMock(ok=True)
    fake_content_svc = MagicMock()
    fake_content_svc.validate_change_request.return_value = fake_validation
    fake_content_svc.apply_change_request.return_value = fake_apply_ok

    with patch("cauldron_site_astro.site_tools._get_content_operation_service", return_value=fake_content_svc):
        with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
            result = handle_publish(ctx, change_set_id=str(cs.id), confirm=True)

    assert result.success is True, result.message
    fake_content_svc.apply_change_request.assert_called_once_with(
        "req-retry",
        user=ctx.actor,
        expected_version=1,
    )

    cs.refresh_from_db()
    assert cs.status == SiteChangeSet.PUBLISHED


def test_promote_output_no_partial_directory_exposed(tmp_path):
    """promote_output leaves no .staging-* or .previous-* directories.

    The partial copy lives at output_root.staging-<uuid> (never live).
    Only after the copy completes does the atomic os.rename() swap it into
    output_root. Callers therefore never observe a partially-copied tree.
    """
    from cauldron_site_astro.service import SiteBuildService

    config = _make_config(tmp_path)
    svc = SiteBuildService(config, MagicMock())

    output_root = Path(config.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "old.html").write_text("<html>old</html>")

    new_build = tmp_path / "new_build"
    new_build.mkdir()
    (new_build / "new.html").write_text("<html>new</html>")

    svc.promote_output(new_build)

    assert (output_root / "new.html").exists()
    assert not (output_root / "old.html").exists()

    parent = output_root.parent
    staging_dirs = list(parent.glob(output_root.name + ".staging-*"))
    previous_dirs = list(parent.glob(output_root.name + ".previous-*"))
    assert staging_dirs == [], f"staging artefacts must be cleaned up: {staging_dirs}"
    assert previous_dirs == [], f"previous artefacts must be cleaned up: {previous_dirs}"


def test_promote_output_with_backup__atomic_swap_invariants(tmp_path):
    """promote_output_with_backup upholds three reader-visible invariants.

    (1) No partial tree at output_root: after the swap output_root contains
        exactly the new content — no mix with the old content.
    (2) No missing live root: output_root exists before and after the swap.
    (3) Backup is not the live root: the returned snapshot lives at
        output_root.previous-<uuid>, a distinct path that is never served.

    Same-filesystem guarantee (documented here, enforced by construction):
    The staging path is output_root.staging-<uuid> — it shares output_root's
    parent directory and is therefore always on the same filesystem as
    output_root.  os.rename(staging → output_root) is consequently a single
    atomic POSIX rename(2) syscall; no reader can observe an intermediate
    state between the old and new complete trees.

    Also verifies that restore_output reinstates the previous content
    completely (the snapshot is a full copy, not a partial one).
    """
    from cauldron_site_astro.service import SiteBuildService

    config = _make_config(tmp_path)
    svc = SiteBuildService(config, MagicMock())
    output_root = Path(config.output_root)

    # Establish a complete initial output: files a, b, c
    output_root.mkdir(parents=True, exist_ok=True)
    for name in ("a.html", "b.html", "c.html"):
        (output_root / name).write_text(f"<html>{name}</html>")

    # New complete content: files x, y, z
    new_build = tmp_path / "new_build"
    new_build.mkdir()
    for name in ("x.html", "y.html", "z.html"):
        (new_build / name).write_text(f"<html>{name}</html>")

    snapshot = svc.promote_output_with_backup(new_build)

    # Invariant 1: output_root exists after the swap
    assert output_root.exists(), "live root must exist after swap (no missing root)"

    # Invariant 2: output_root has exactly the new files — never a mix
    live_files = {p.name for p in output_root.iterdir()}
    assert live_files == {"x.html", "y.html", "z.html"}, (
        f"output_root must contain exactly the new content, got: {live_files!r}"
    )

    # Invariant 3: the snapshot is at a different path from output_root
    assert snapshot is not None
    snapshot_path = Path(snapshot)
    assert snapshot_path != output_root, "snapshot must not be output_root"
    assert not snapshot_path.samefile(output_root), "snapshot must not resolve to output_root"

    # Snapshot is complete (full copy of previous content)
    backup_files = {p.name for p in snapshot_path.iterdir()}
    assert backup_files == {"a.html", "b.html", "c.html"}, (
        f"snapshot must be the complete previous content, got: {backup_files!r}"
    )

    # Same-filesystem constraint: snapshot lives under output_root.parent (inside
    # output_root.releases/), guaranteeing all rename() calls are on the same filesystem.
    assert output_root.parent in snapshot_path.parents, (
        "snapshot lives under output_root.parent, guaranteeing same-filesystem "
        "for os.rename() — cross-device rename would raise EXDEV"
    )

    # restore_output reinstates the previous content completely
    svc.restore_output(snapshot)
    assert output_root.exists(), "output_root must exist after restore"
    restored_files = {p.name for p in output_root.iterdir()}
    assert restored_files == {"a.html", "b.html", "c.html"}, (
        f"restore must produce exactly the original content, got: {restored_files!r}"
    )


def test_promote_output_concurrent_reader_never_sees_missing_root(tmp_path):
    """output_root is continuously accessible during an atomic symlink swap.

    A reader thread polls output_root.exists() throughout a second promote_output
    call.  Because the activation is a single os.rename(next_link, output_root)
    syscall — which replaces one symlink with another atomically at the kernel
    level — output_root is never absent for any reader that reaches the path
    after the first promote (i.e. after output_root is already a symlink).

    The migration case (real-dir → symlink on first call) has a documented brief
    window; only the second and subsequent promotions are fully atomic.  This
    test exercises the second call to isolate the symlink-swap invariant.
    """
    import threading
    from cauldron_site_astro.service import SiteBuildService

    config = _make_config(tmp_path)
    svc = SiteBuildService(config, MagicMock())
    output_root = Path(config.output_root)

    # First promote: establishes output_root as a symlink → initial release.
    initial = tmp_path / "initial"
    initial.mkdir()
    (initial / "init.html").write_text("<html>init</html>")
    svc.promote_output(initial)

    assert output_root.exists()
    assert output_root.is_symlink(), "output_root must be a symlink after first promote"

    # Prepare a second build to swap in.
    new_build = tmp_path / "new_build"
    new_build.mkdir()
    (new_build / "new.html").write_text("<html>new</html>")

    gaps_observed: list[bool] = []
    stop_event = threading.Event()

    def reader():
        while not stop_event.is_set():
            if not output_root.exists():
                gaps_observed.append(True)

    t = threading.Thread(target=reader, daemon=True)
    t.start()

    # Second promote: atomic symlink swap — reader must never see a gap.
    svc.promote_output(new_build)

    stop_event.set()
    t.join(timeout=5)

    assert not gaps_observed, (
        f"output_root was absent {len(gaps_observed)} time(s) during the symlink swap — "
        "the activation is not atomic"
    )
    assert output_root.exists()
    assert output_root.is_symlink()
    assert (output_root / "new.html").exists()


def test_full_workflow_prepare_then_inspect_then_publish(tmp_path):
    """End-to-end: theme-only change set goes through the full lifecycle.

    This exercises the multi-tool sequence the Admin AI is expected to
    follow: prepare_change_set -> inspect_preview -> publish, ensuring
    each hop persists the correct status transition and returns only
    safe URL paths.
    """
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_site_astro.site_tools import (
        _handle_prepare_change_set as handle_prepare_change_set,
        _handle_inspect_preview as handle_inspect_preview,
        _handle_publish as handle_publish,
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
        prep = handle_prepare_change_set(
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
        insp = handle_inspect_preview(ctx, change_set_id=cs_id)
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
            pub = handle_publish(ctx, change_set_id=cs_id, confirm=True)

    assert pub.success is True, pub.message
    assert pub.data["published"] is True
    assert pub.data["live_url"] == "/"

    cs = SiteChangeSet.objects.get(id=cs_id)
    assert cs.status == SiteChangeSet.PUBLISHED
    # Staged CSS was promoted on successful publish.
    assert SiteThemeService(theme_dir).get_active_css() == "body { background: purple; }"


# ---------------------------------------------------------------------------
# _extract_draft_items: new-page injection into preview builds
# ---------------------------------------------------------------------------


def test_extract_draft_items_empty_input():
    """Empty request list returns empty ids and items."""
    from cauldron_site_astro.site_tools import _extract_draft_items
    ids, items = _extract_draft_items([])
    assert ids == []
    assert items == []


def test_extract_draft_items_no_matching_requests():
    """Non-existent request IDs produce empty results without raising."""
    from cauldron_site_astro.site_tools import _extract_draft_items
    ids, items = _extract_draft_items(["does-not-exist-123"])
    assert ids == []
    assert items == []


def test_extract_draft_items_from_workspace_changeset(tmp_path):
    """Operations from a workspace changeset are returned as extra_items."""
    from types import SimpleNamespace
    from cauldron_site_astro.site_tools import _extract_draft_items

    # Build a minimal fake workspace and changeset
    fake_op = SimpleNamespace(
        kind="create",
        item_id="timmy-home",
        slug="homepage",
        collection="pages",
        schema="page",
        data={"title": "Timmy's Home"},
        body="Hello from Timmy!",
    )
    fake_changeset = SimpleNamespace(operations=[fake_op])
    fake_workspace = MagicMock()
    fake_workspace.load_changeset.return_value = fake_changeset

    fake_cr = SimpleNamespace(
        workspace_changeset_id="ws-abc",
    )

    fake_service = MagicMock()
    fake_service._workspace = fake_workspace

    with patch("cauldron_site_astro.site_tools._get_content_operation_service", return_value=fake_service):
        # Patch ContentChangeRequest.objects.get to return our fake cr
        with patch(
            "cauldron_site_astro.site_tools._extract_draft_items.__code__",
        ) if False else patch(
            "cauldron_content_operations.models.ContentChangeRequest.objects.get",
            return_value=fake_cr,
        ):
            ids, items = _extract_draft_items(["req-timmy-1"])

    assert "timmy-home" in ids
    assert len(items) == 1
    item = items[0]
    assert item.id == "timmy-home"
    assert item.slug == "homepage"
    assert item.data == {"title": "Timmy's Home"}
    assert item.body == "Hello from Timmy!"


def test_extract_draft_items_delete_operations_skipped(tmp_path):
    """Delete operations are excluded from extra_items (cannot inject a removal)."""
    from types import SimpleNamespace
    from cauldron_site_astro.site_tools import _extract_draft_items

    fake_op = SimpleNamespace(
        kind="delete",
        item_id="old-page",
        slug="old-page",
        collection="pages",
        schema="page",
        data={},
        body="",
    )
    fake_changeset = SimpleNamespace(operations=[fake_op])
    fake_workspace = MagicMock()
    fake_workspace.load_changeset.return_value = fake_changeset

    fake_cr = SimpleNamespace(workspace_changeset_id="ws-del")
    fake_service = MagicMock()
    fake_service._workspace = fake_workspace

    with patch("cauldron_site_astro.site_tools._get_content_operation_service", return_value=fake_service):
        with patch(
            "cauldron_content_operations.models.ContentChangeRequest.objects.get",
            return_value=fake_cr,
        ):
            ids, items = _extract_draft_items(["req-del-1"])

    # The item_id IS recorded (for scoping) but no extra_item is injected
    assert "old-page" in ids
    assert items == []


def test_extract_draft_items_create_without_item_id(tmp_path):
    """Create operations with no item_id are injected as extra_items via slug.

    This is the real-world path: content.create_proposal returns item_id=''
    for brand-new pages that have never been published.  The old code did
    ``if not op_item_id: continue`` and silently dropped every create op,
    producing pages_built=0 for any all-new site.
    """
    from types import SimpleNamespace
    from cauldron_site_astro.site_tools import _extract_draft_items

    fake_op = SimpleNamespace(
        kind="create",
        item_id="",        # empty — not yet assigned
        slug="index",
        collection="pages",
        schema="page",
        data={"title": "Home"},
        body="<h1>Welcome</h1>",
    )
    fake_changeset = SimpleNamespace(operations=[fake_op])
    fake_workspace = MagicMock()
    fake_workspace.load_changeset.return_value = fake_changeset

    fake_cr = SimpleNamespace(workspace_changeset_id="ws-new")
    fake_service = MagicMock()
    fake_service._workspace = fake_workspace

    with patch("cauldron_site_astro.site_tools._get_content_operation_service", return_value=fake_service):
        with patch(
            "cauldron_content_operations.models.ContentChangeRequest.objects.get",
            return_value=fake_cr,
        ):
            ids, items = _extract_draft_items(["req-new-1"])

    # No real item_id → nothing in item_ids (no existing item to scope)
    assert ids == []
    # BUT the page IS injected as an extra_item via its slug
    assert len(items) == 1
    item = items[0]
    assert item.slug == "index"
    assert item.id == "index"      # slug used as synthetic id
    assert item.body == "<h1>Welcome</h1>"
    assert item.data == {"title": "Home"}


def test_prepare_change_set_passes_extra_items_to_build_preview(tmp_path):
    """_handle_prepare_change_set passes extra_items from workspace ops to build_preview."""
    from types import SimpleNamespace
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_site_astro.site_tools import _handle_prepare_change_set as handle_prepare_change_set

    ctx = _ctx("timmy-extra-items")
    previews_root = tmp_path / "previews"
    config = _make_config(tmp_path, previews_root=str(previews_root))
    build_result = _make_build_result(ok=True, pages_built=3)
    svc = MagicMock()
    svc._config = config
    svc.build_preview.return_value = build_result

    # Fake draft item that _extract_draft_items will return
    fake_item = SimpleNamespace(
        id="new-timmy-page",
        slug="about-timmy",
        collection="pages",
        schema="page",
        data={"title": "About Timmy"},
        body="Timmy is a dragon.",
        status=object(),  # ContentStatus.DRAFT
        hash="",
        provider="",
        source_ref="",
    )

    with patch(
        "cauldron_site_astro.site_tools._extract_draft_items",
        return_value=(["new-timmy-page"], [fake_item]),
    ):
        with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
            result = handle_prepare_change_set(
                ctx,
                content_request_ids=["req-timmy-new"],
            )

    assert result.success is True
    call_kwargs = svc.build_preview.call_args.kwargs
    assert call_kwargs.get("item_ids_to_include") == ["new-timmy-page"]
    assert call_kwargs.get("extra_items") == [fake_item]


# ---------------------------------------------------------------------------
# Staged theme CSS auto-handoff (item 1)
# ---------------------------------------------------------------------------


def test_prepare_change_set_auto_loads_staged_css(tmp_path):
    """prepare_change_set automatically picks up CSS staged by stage_theme.

    The model does NOT need to pass theme_css= — the handler reads the
    staged file when the argument is omitted, so stage_theme → prepare → publish
    forms a dependable automatic handoff.
    """
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_site_astro.site_tools import (
        _handle_prepare_change_set as handle_prepare_change_set,
        _handle_stage_theme as handle_stage_theme,
    )
    from cauldron_site_astro.theme import SiteThemeService

    ctx = _ctx("timmy-staged-css")
    theme_dir = tmp_path / "theme"
    previews_root = tmp_path / "previews"
    config = _make_config(tmp_path, theme_root=str(theme_dir), previews_root=str(previews_root))

    svc = MagicMock()
    svc._config = config

    def fake_build(**kwargs):
        out = Path(kwargs["output_dir"])
        out.mkdir(parents=True, exist_ok=True)
        (out / "index.html").write_text("<html></html>")
        return _make_build_result(ok=True, pages_built=1, output_dir=str(out))

    svc.build_preview.side_effect = fake_build

    # 1. Stage CSS via site.stage_theme
    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        stage_result = handle_stage_theme(ctx, css_content="body { color: coral; }")
    assert stage_result.success is True
    assert SiteThemeService(theme_dir).get_staged_css() == "body { color: coral; }"

    # 2. prepare_change_set WITHOUT passing theme_css — it must auto-load
    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        prep = handle_prepare_change_set(
            ctx,
            content_request_ids=["req-css-handoff"],
            # theme_css intentionally omitted
        )

    assert prep.success is True, prep.message
    cs = SiteChangeSet.objects.get(id=prep.data["change_set_id"])
    # Staged CSS must be captured in the change set record
    assert cs.staged_theme_css == "body { color: coral; }", (
        f"Expected staged CSS in change set, got: {cs.staged_theme_css!r}"
    )
    # build_preview must have received the CSS
    call_kwargs = svc.build_preview.call_args.kwargs
    assert call_kwargs.get("theme_css") == "body { color: coral; }", (
        f"build_preview did not receive staged CSS: {call_kwargs!r}"
    )


def test_prepare_change_set_explicit_theme_css_overrides_staged(tmp_path):
    """Explicit theme_css= takes priority over any previously staged CSS."""
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_site_astro.site_tools import _handle_prepare_change_set as handle_prepare_change_set
    from cauldron_site_astro.theme import SiteThemeService

    ctx = _ctx("timmy-explicit-css")
    theme_dir = tmp_path / "theme"
    previews_root = tmp_path / "previews"
    config = _make_config(tmp_path, theme_root=str(theme_dir), previews_root=str(previews_root))

    # Pre-stage some CSS
    SiteThemeService(theme_dir).stage_css("body { color: purple; }")

    svc = MagicMock()
    svc._config = config

    def fake_build(**kwargs):
        out = Path(kwargs["output_dir"])
        out.mkdir(parents=True, exist_ok=True)
        (out / "index.html").write_text("<html></html>")
        return _make_build_result(ok=True, pages_built=1, output_dir=str(out))

    svc.build_preview.side_effect = fake_build

    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        prep = handle_prepare_change_set(
            ctx,
            content_request_ids=["req-override"],
            theme_css="body { color: gold; }",  # explicit override
        )

    assert prep.success is True, prep.message
    cs = SiteChangeSet.objects.get(id=prep.data["change_set_id"])
    assert cs.staged_theme_css == "body { color: gold; }", (
        "Explicit theme_css must override staged CSS"
    )
    call_kwargs = svc.build_preview.call_args.kwargs
    assert call_kwargs.get("theme_css") == "body { color: gold; }"


def test_full_stage_then_prepare_then_publish_carries_css(tmp_path):
    """End-to-end: stage_theme → prepare_change_set → publish promotes the CSS.

    Proves the full automatic handoff chain without any manual CSS passing.
    """
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_site_astro.site_tools import (
        _handle_stage_theme as handle_stage_theme,
        _handle_prepare_change_set as handle_prepare_change_set,
        _handle_publish as handle_publish,
    )
    from cauldron_site_astro.theme import SiteThemeService

    ctx = _ctx("timmy-e2e-css")
    theme_dir = tmp_path / "theme"
    previews_root = tmp_path / "previews"
    config = _make_config(tmp_path, theme_root=str(theme_dir), previews_root=str(previews_root))

    svc = _make_real_svc(config)

    def fake_build(**kwargs):
        out = Path(kwargs["output_dir"])
        out.mkdir(parents=True, exist_ok=True)
        (out / "index.html").write_text("<html></html>")
        from cauldron_site_astro.service import BuildResult
        return BuildResult(ok=True, pages_built=1, output_dir=str(out))

    svc.build_preview = MagicMock(side_effect=fake_build)

    # 1. Stage CSS
    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        stage_r = handle_stage_theme(ctx, css_content="body { background: teal; }")
    assert stage_r.success is True

    # 2. Prepare (no theme_css arg — auto-load from staged)
    with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
        prep = handle_prepare_change_set(
            ctx,
            content_request_ids=["req-e2e"],
        )
    assert prep.success is True, prep.message
    cs_id = prep.data["change_set_id"]
    cs = SiteChangeSet.objects.get(id=cs_id)
    assert cs.staged_theme_css == "body { background: teal; }"
    assert prep.data["preview_url"].startswith("/")

    # 3. Publish
    fake_content_svc = MagicMock()
    fake_ok = MagicMock(ok=True, request_version=1)
    fake_content_svc.validate_change_request.return_value = fake_ok
    fake_content_svc.apply_change_request.return_value = fake_ok

    with patch("cauldron_site_astro.site_tools._get_content_operation_service", return_value=fake_content_svc):
        with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
            pub = handle_publish(ctx, change_set_id=cs_id, confirm=True)

    assert pub.success is True, pub.message
    # published boolean must be True on success
    assert pub.data["published"] is True
    assert pub.data["live_url"] == "/"

    # CSS must have been promoted to active
    assert SiteThemeService(theme_dir).get_active_css() == "body { background: teal; }"

    cs.refresh_from_db()
    assert cs.status == SiteChangeSet.PUBLISHED


# ---------------------------------------------------------------------------
# published boolean in publish result (item 2)
# ---------------------------------------------------------------------------


def test_publish_result_has_published_true_on_success(tmp_path):
    """Successful publish returns published=True in data."""
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_site_astro.site_tools import _handle_publish as handle_publish

    ctx = _ctx("timmy-pub-bool")
    config = _make_config(tmp_path)

    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        content_request_ids=["req-bool"],
        affected_item_ids=["item-bool"],
    )

    svc = _make_real_svc(config)
    svc.build_preview = MagicMock(side_effect=_fake_build_ok())

    fake_ok = MagicMock(ok=True, request_version=1)
    fake_content_svc = MagicMock()
    fake_content_svc.validate_change_request.return_value = fake_ok
    fake_content_svc.apply_change_request.return_value = fake_ok

    with patch("cauldron_site_astro.site_tools._get_content_operation_service", return_value=fake_content_svc):
        with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
            result = handle_publish(ctx, change_set_id=str(cs.id), confirm=True)

    assert result.success is True
    assert result.data["published"] is True
    assert result.data["live_url"] == "/"
    assert "pages_built" in result.data
    assert "change_set_id" in result.data


def test_publish_result_has_published_false_on_build_failure(tmp_path):
    """Failed publish (build error) returns success=False; no published key expected on failure."""
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_site_astro.site_tools import _handle_publish as handle_publish

    ctx = _ctx("timmy-pub-fail-bool")
    config = _make_config(tmp_path)

    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        content_request_ids=["req-fail"],
        affected_item_ids=["item-fail"],
    )

    svc = _make_real_svc(config)
    from cauldron_site_astro.service import BuildResult
    svc.build_preview = MagicMock(return_value=BuildResult(
        ok=False, pages_built=0, output_dir="", error="Astro crashed",
    ))

    fake_ok = MagicMock(ok=True, request_version=1)
    fake_content_svc = MagicMock()
    fake_content_svc.validate_change_request.return_value = fake_ok

    with patch("cauldron_site_astro.site_tools._get_content_operation_service", return_value=fake_content_svc):
        with patch("cauldron_site_astro.site_tools.get_build_service", return_value=svc):
            result = handle_publish(ctx, change_set_id=str(cs.id), confirm=True)

    assert result.success is False
    assert "change_set_id" in result.data
    # live_url and published=True must not appear on failure
    assert result.data.get("published") is not True
    assert "live_url" not in result.data
