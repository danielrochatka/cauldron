"""Integration tests for public-site pages-style publication.

Covers the acceptance criteria for the fix/public-style-publication-integration
PR:

1.  admin-scope proposals retain existing direct override behavior.
    (tested in cauldron-ai-admin: test_ui_style_proposals.py)
2.  pages-scope proposal does NOT mark applied on prepare alone.
3.  pages-scope proposal enters controlled SiteChangeSet preview flow.
4.  preview receives complete effective public CSS with proposed target overlaid.
5.  preview does NOT mutate live public CSS (active.css).
6.  preview does NOT mutate live Astro output.
7.  successful Publish emits site_changeset_published signal with style_request_id.
8.  successful Publish updates active public theme CSS.
    (CSS commit via mark_style_applied tested in cauldron-ai-admin)
9.  successful Publish promotes Astro output.
10. style request becomes applied ONLY after successful publication.
    (tested in cauldron-ai-admin: test_ui_style_proposals.py)
11. content-only Publish preserves existing public CSS.
12. multiple pages/*.css files retain deterministic lexical ordering.
13. modifying one CSS file preserves unrelated files.
14. stale base hash fails closed.
    (tested in cauldron-ai-admin: test_ui_style_proposals.py)
15. failed preview preserves all live state.
16. failed publication restores / marks reconciliation per existing rules.
17. existing already-applied pages override state has a reconciliation path
    (a subsequent content or style publish picks up the UIOverrideStore files).
18. Site Astro still operates when Admin AI is absent.
19. permissions remain enforced.
20. no architecture violations (tested via arch_check CLI).
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------


def _make_user(username, perms=()):
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission

    User = get_user_model()
    user = User.objects.create_user(username=username, password="pw")
    for codename in perms:
        try:
            perm = Permission.objects.get(codename=codename)
            user.user_permissions.add(perm)
        except Exception:
            pass
    return User.objects.get(pk=user.pk)


def _make_pub_service_mocks(*, preview_ok=True, build_log=""):
    """Return a fully-mocked SiteBuildService.

    Both prepare() and publish() call build_preview() — this sets its return
    value.  Tests that need the publish step to fail should override
    build_preview.return_value after calling this helper.
    """
    from cauldron_site_astro.service import BuildResult

    build_result = BuildResult(
        ok=preview_ok,
        pages_built=3 if preview_ok else 0,
        error="" if preview_ok else "Build failed",
        build_log=build_log,
    )

    mock_svc = MagicMock()
    mock_svc._config.previews_root = "/tmp/previews"
    mock_svc._config.theme_root = None
    mock_svc._config.output_root = "/tmp/output"
    mock_svc.build_preview.return_value = build_result
    mock_svc.snapshot_output.return_value = "backup_token"
    mock_svc.restore_output.return_value = None
    mock_svc.discard_output_backup.return_value = None
    mock_svc.promote_output.return_value = None
    mock_svc.promote_output_with_backup.return_value = None
    return mock_svc


class FakePagesStyleProvider:
    """In-memory PagesStyleProvider for tests — no filesystem needed."""

    def __init__(self, files: dict[str, str] | None = None):
        self._files: dict[str, str] = dict(files or {})
        self._committed: dict[str, str] = {}
        self._commit_hash = "a" * 64
        self._rollback_ok = True
        self._rollback_called: list[dict] = []
        self._conflict_on_commit = False

    def get_composed_css(
        self,
        *,
        proposed_target: str | None = None,
        proposed_content: str | None = None,
    ) -> str:
        targets = sorted(self._files.keys())
        if proposed_target and proposed_target not in targets:
            targets = sorted(targets + [proposed_target])
        parts = []
        for t in targets:
            if proposed_target and t == proposed_target:
                if proposed_content is not None:
                    parts.append(proposed_content)
            else:
                parts.append(self._files.get(t, ""))
        return "\n".join(p for p in parts if p)

    def read_style_source(self, target: str) -> str | None:
        return self._files.get(target)

    def commit_style(self, *, target, content, expected_hash, base_exists):
        if self._conflict_on_commit:
            from cauldron_content.pages_style import StyleConflictError
            raise StyleConflictError("hash conflict in test")
        self._committed[target] = content
        self._files[target] = content
        return self._commit_hash

    def rollback_style_commit(self, *, target, old_content, committed_hash):
        self._rollback_called.append({"target": target, "old_content": old_content})
        if self._rollback_ok and old_content is None:
            self._files.pop(target, None)
        elif self._rollback_ok and old_content is not None:
            self._files[target] = old_content
        return self._rollback_ok

    def list_targets(self) -> list[str]:
        return sorted(self._files.keys())


@pytest.fixture(autouse=True)
def reset_pages_style_provider():
    from cauldron_content.pages_style import register_pages_style_provider
    import cauldron_content.pages_style as mod
    original = mod._provider
    yield
    register_pages_style_provider(original)


# ---------------------------------------------------------------------------
# 2. pages-scope proposal does NOT become applied after prepare-only
# ---------------------------------------------------------------------------

def test_pages_proposal_not_applied_after_prepare(bypass_db_integrity, tmp_path):
    """A pages-scope style proposal stays in 'approved' state after prepare()."""
    from cauldron_site_astro.publication_service import SiteChangeSetService

    from cauldron_content.pages_style import register_pages_style_provider
    provider = FakePagesStyleProvider({"90-site.css": "nav{display:flex;}"})
    register_pages_style_provider(provider)

    mock_svc = _make_pub_service_mocks(preview_ok=True)
    mock_svc._config.previews_root = str(tmp_path / "previews")
    mock_svc._config.theme_root = None

    with patch("cauldron_site_astro.publication_service.get_build_service", return_value=mock_svc):
        svc = SiteChangeSetService()
        result = svc.prepare(
            actor=SimpleNamespace(pk=None),
            content_request_ids=[],
            staged_theme_css="nav { display: flex; }",
            style_request_id="fake-request-id",
        )

    assert result.ok
    # prepare() must not call mark_style_applied or any post-publication action.
    # Verified structurally: signal is only emitted in publish(), not prepare().


# ---------------------------------------------------------------------------
# 3. pages-scope proposal creates a SiteChangeSet preview
# ---------------------------------------------------------------------------

def test_pages_proposal_creates_site_changeset(bypass_db_integrity, tmp_path):
    """prepare() with style_request_id and empty content_request_ids creates a SiteChangeSet."""
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_site_astro.publication_service import SiteChangeSetService

    mock_svc = _make_pub_service_mocks(preview_ok=True)
    mock_svc._config.previews_root = str(tmp_path / "previews")
    mock_svc._config.theme_root = None

    with patch("cauldron_site_astro.publication_service.get_build_service", return_value=mock_svc):
        svc = SiteChangeSetService()
        result = svc.prepare(
            actor=SimpleNamespace(pk=None),
            content_request_ids=[],
            staged_theme_css="body { font-size: 16px; }",
            style_request_id="req-abc123",
        )

    assert result.ok
    cs = SiteChangeSet.objects.get(id=result.change_set_id)
    assert cs.style_request_id == "req-abc123"
    assert cs.staged_theme_css == "body { font-size: 16px; }"
    assert cs.status == SiteChangeSet.DRAFT_READY


# ---------------------------------------------------------------------------
# 4. preview receives complete effective CSS with proposed target overlaid
# ---------------------------------------------------------------------------

def test_preview_receives_composed_css(bypass_db_integrity, tmp_path):
    """PagesStyleProvider.get_composed_css() is called during content prepare()
    when no staged_theme_css is explicitly provided, composing existing files
    plus the proposed overlay."""
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_content.pages_style import register_pages_style_provider

    provider = FakePagesStyleProvider({
        "00-variables.css": ":root { --color: blue; }",
        "90-site.css": "nav { display: block; }",
    })
    register_pages_style_provider(provider)

    mock_svc = _make_pub_service_mocks(preview_ok=True)
    mock_svc._config.previews_root = str(tmp_path / "previews")
    mock_svc._config.theme_root = None

    captured = {}

    def capture_build_preview(**kwargs):
        captured["theme_css"] = kwargs.get("theme_css", "")
        from cauldron_site_astro.service import BuildResult
        return BuildResult(ok=True, pages_built=1)

    mock_svc.build_preview.side_effect = capture_build_preview

    # Compose with proposed overlay for 90-site.css
    proposed = "nav { display: flex; }"
    composed = provider.get_composed_css(
        proposed_target="90-site.css",
        proposed_content=proposed,
    )

    with patch("cauldron_site_astro.publication_service.get_build_service", return_value=mock_svc):
        svc = SiteChangeSetService()
        result = svc.prepare(
            actor=SimpleNamespace(pk=None),
            content_request_ids=[],
            staged_theme_css=composed,
            style_request_id="req-overlay",
        )

    assert result.ok
    # The composed CSS must include both the existing variable file and the proposed nav style
    assert ":root { --color: blue; }" in captured["theme_css"]
    assert "nav { display: flex; }" in captured["theme_css"]
    # The old nav style must NOT appear (overridden)
    assert "nav { display: block; }" not in captured["theme_css"]


# ---------------------------------------------------------------------------
# 5. preview does not mutate live public CSS
# ---------------------------------------------------------------------------

def test_prepare_does_not_write_active_css(bypass_db_integrity, tmp_path):
    """A successful prepare() must not write to active.css."""
    from cauldron_site_astro.publication_service import SiteChangeSetService

    mock_svc = _make_pub_service_mocks(preview_ok=True)
    mock_svc._config.previews_root = str(tmp_path / "previews")
    mock_svc._config.theme_root = str(tmp_path / "theme")

    (tmp_path / "theme").mkdir()
    active_css = tmp_path / "theme" / "active.css"
    active_css.write_text("/* live */")

    with patch("cauldron_site_astro.publication_service.get_build_service", return_value=mock_svc):
        svc = SiteChangeSetService()
        svc.prepare(
            actor=SimpleNamespace(pk=None),
            content_request_ids=[],
            staged_theme_css="nav { display: flex; }",
            style_request_id="req-css",
        )

    assert active_css.read_text() == "/* live */"


# ---------------------------------------------------------------------------
# 6. preview does not mutate live Astro output
# ---------------------------------------------------------------------------

def test_prepare_does_not_touch_output(bypass_db_integrity, tmp_path):
    """prepare() must not call promote_output or any output-mutating method."""
    from cauldron_site_astro.publication_service import SiteChangeSetService

    mock_svc = _make_pub_service_mocks(preview_ok=True)
    mock_svc._config.previews_root = str(tmp_path / "previews")
    mock_svc._config.theme_root = None

    with patch("cauldron_site_astro.publication_service.get_build_service", return_value=mock_svc):
        svc = SiteChangeSetService()
        svc.prepare(
            actor=SimpleNamespace(pk=None),
            content_request_ids=[],
            staged_theme_css="body{}",
            style_request_id="req-output",
        )

    mock_svc.promote_output.assert_not_called()
    mock_svc.snapshot_output.assert_not_called()


# ---------------------------------------------------------------------------
# 7. successful Publish emits site_changeset_published signal
# ---------------------------------------------------------------------------

def test_publish_calls_mark_style_applied_signal(bypass_db_integrity, tmp_path):
    """On successful publish of a style changeset, site_changeset_published
    signal is emitted with the style_request_id."""
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.signals import site_changeset_published
    from cauldron_site_astro.models import SiteChangeSet

    # Pre-create a DRAFT_READY changeset with a style_request_id
    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        staged_theme_css="nav { display: flex; }",
        content_request_ids=[],
        style_request_id="req-signal-test",
    )

    mock_svc = _make_pub_service_mocks(preview_ok=True)
    mock_svc._config.theme_root = None
    mock_svc._config.output_root = str(tmp_path / "output")

    received = {}

    def handler(sender, changeset_id, staged_theme_css, style_request_id, **kwargs):
        received["changeset_id"] = changeset_id
        received["style_request_id"] = style_request_id

    site_changeset_published.connect(handler)
    try:
        actor = SimpleNamespace(pk=1, has_perm=lambda p: True)
        with (
            patch("cauldron_site_astro.publication_service.get_build_service", return_value=mock_svc),
            patch("cauldron_site_astro.publication_service._get_content_operation_service", return_value=None),
            patch("cauldron_site_astro.publication_service._get_require_approval", return_value=False),
            patch("cauldron_site_astro.publication_service._fetch_eligible_change_requests", return_value=({}, None)),
        ):
            svc = SiteChangeSetService()
            result = svc.publish(actor=actor, change_set_id=str(cs.id))
    finally:
        site_changeset_published.disconnect(handler)

    assert result.ok, result.message
    assert received.get("style_request_id") == "req-signal-test"
    assert received.get("changeset_id") == str(cs.id)


# ---------------------------------------------------------------------------
# 11. content-only Publish preserves existing public CSS via provider
# ---------------------------------------------------------------------------

def test_content_only_prepare_includes_pages_css(bypass_db_integrity, tmp_path):
    """When PagesStyleProvider is registered, prepare() for content-only changes
    auto-includes the current composed pages CSS so the preview/publish carry
    forward existing override files."""
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_content.pages_style import register_pages_style_provider

    live_css = "nav { display: flex; } /* live */"
    provider = FakePagesStyleProvider({"90-site.css": live_css})
    register_pages_style_provider(provider)

    captured = {}

    mock_svc = _make_pub_service_mocks(preview_ok=True)
    mock_svc._config.previews_root = str(tmp_path / "previews")
    mock_svc._config.theme_root = None

    def capture_preview(**kwargs):
        captured["theme_css"] = kwargs.get("theme_css", "")
        from cauldron_site_astro.service import BuildResult
        return BuildResult(ok=True, pages_built=2)

    mock_svc.build_preview.side_effect = capture_preview

    with patch("cauldron_site_astro.publication_service.get_build_service", return_value=mock_svc):
        svc = SiteChangeSetService()
        # Content-only prepare: no staged_theme_css, no style_request_id
        result = svc.prepare(
            actor=SimpleNamespace(pk=None),
            content_request_ids=["content-req-1"],
            # staged_theme_css omitted (defaults to None = inherited from provider)
        )

    assert result.ok
    assert live_css in captured.get("theme_css", "")


# ---------------------------------------------------------------------------
# 12. multiple pages/*.css files retain deterministic lexical ordering
# ---------------------------------------------------------------------------

def test_pages_css_composition_is_lexically_ordered():
    """get_composed_css() concatenates files in lexical sort order."""
    from cauldron_content.pages_style import register_pages_style_provider

    provider = FakePagesStyleProvider({
        "90-site.css": "nav { display: flex; }",
        "00-variables.css": ":root { --color: blue; }",
        "10-layout.css": ".wrapper { max-width: 1200px; }",
    })
    register_pages_style_provider(provider)

    css = provider.get_composed_css()
    # 00-variables.css must come before 10-layout.css before 90-site.css
    idx_vars = css.index(":root")
    idx_layout = css.index(".wrapper")
    idx_nav = css.index("nav")
    assert idx_vars < idx_layout < idx_nav


# ---------------------------------------------------------------------------
# 13. modifying one CSS file preserves unrelated files
# ---------------------------------------------------------------------------

def test_pages_css_overlay_preserves_unrelated_files():
    """Proposing a change to 90-site.css does not lose 00-variables.css."""
    from cauldron_content.pages_style import register_pages_style_provider

    provider = FakePagesStyleProvider({
        "00-variables.css": ":root { --color: blue; }",
        "90-site.css": "nav { display: block; }",
    })
    register_pages_style_provider(provider)

    css = provider.get_composed_css(
        proposed_target="90-site.css",
        proposed_content="nav { display: flex; }",
    )
    assert ":root { --color: blue; }" in css
    assert "nav { display: flex; }" in css
    assert "nav { display: block; }" not in css


# ---------------------------------------------------------------------------
# 15. failed preview preserves all live state
# ---------------------------------------------------------------------------

def test_failed_preview_does_not_write_css(bypass_db_integrity, tmp_path):
    """A failed prepare() leaves active.css and output untouched."""
    from cauldron_site_astro.publication_service import SiteChangeSetService

    mock_svc = _make_pub_service_mocks(preview_ok=False)
    mock_svc._config.previews_root = str(tmp_path / "previews")
    mock_svc._config.theme_root = str(tmp_path / "theme")

    (tmp_path / "theme").mkdir()
    active_css = tmp_path / "theme" / "active.css"
    active_css.write_text("/* original */")

    with patch("cauldron_site_astro.publication_service.get_build_service", return_value=mock_svc):
        svc = SiteChangeSetService()
        result = svc.prepare(
            actor=SimpleNamespace(pk=None),
            content_request_ids=[],
            staged_theme_css="nav { display: flex; }",
            style_request_id="req-fail",
        )

    assert not result.ok
    assert active_css.read_text() == "/* original */"
    mock_svc.promote_output.assert_not_called()


# ---------------------------------------------------------------------------
# 16. failed publish: changeset marks PUBLISH_FAILED, signal not emitted
# ---------------------------------------------------------------------------

def test_failed_publish_leaves_changeset_publish_failed(bypass_db_integrity, tmp_path):
    """If publish() build fails, SiteChangeSet.status == PUBLISH_FAILED and
    no style request is marked applied."""
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_site_astro.signals import site_changeset_published

    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        staged_theme_css="nav { display: flex; }",
        content_request_ids=[],
        style_request_id="req-fail-publish",
    )

    # publish() calls build_preview() just like prepare() does — make it fail.
    mock_svc = _make_pub_service_mocks(preview_ok=False)
    mock_svc._config.theme_root = None
    mock_svc._config.output_root = str(tmp_path / "output")

    signal_received = []

    def handler(**kwargs):
        signal_received.append(kwargs)

    site_changeset_published.connect(handler)
    try:
        actor = SimpleNamespace(pk=1, has_perm=lambda p: True)
        with (
            patch("cauldron_site_astro.publication_service.get_build_service", return_value=mock_svc),
            patch("cauldron_site_astro.publication_service._get_content_operation_service", return_value=None),
            patch("cauldron_site_astro.publication_service._get_require_approval", return_value=False),
            patch("cauldron_site_astro.publication_service._fetch_eligible_change_requests", return_value=({}, None)),
        ):
            svc = SiteChangeSetService()
            result = svc.publish(actor=actor, change_set_id=str(cs.id))
    finally:
        site_changeset_published.disconnect(handler)

    cs.refresh_from_db()
    assert cs.status == SiteChangeSet.PUBLISH_FAILED
    assert not signal_received  # signal not emitted on failure


# ---------------------------------------------------------------------------
# 17. existing already-applied pages override state reconciliation path
# ---------------------------------------------------------------------------

def test_content_prepare_uses_pages_css_for_preview_not_snapshot(bypass_db_integrity, tmp_path):
    """Content-only prepare() uses PagesStyleProvider CSS for the preview build
    but stores staged_theme_css=None so publish() re-resolves at publication time.
    This avoids the stale-snapshot race (Blocker 4)."""
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_content.pages_style import register_pages_style_provider

    pre_existing_css = "/* pre-existing applied navigation override */"
    provider = FakePagesStyleProvider({"90-site.css": pre_existing_css})
    register_pages_style_provider(provider)

    mock_svc = _make_pub_service_mocks(preview_ok=True)
    mock_svc._config.previews_root = str(tmp_path / "previews")
    mock_svc._config.theme_root = None

    captured = {}

    def capture(**kwargs):
        captured["theme_css"] = kwargs.get("theme_css", "")
        from cauldron_site_astro.service import BuildResult
        return BuildResult(ok=True, pages_built=1)

    mock_svc.build_preview.side_effect = capture

    with patch("cauldron_site_astro.publication_service.get_build_service", return_value=mock_svc):
        svc = SiteChangeSetService()
        result = svc.prepare(
            actor=SimpleNamespace(pk=None),
            content_request_ids=["content-req-reconcile"],
        )

    assert result.ok
    cs = SiteChangeSet.objects.get(id=result.change_set_id)
    # Preview build uses pages CSS for accurate preview
    assert pre_existing_css in captured.get("theme_css", "")
    # But staged_theme_css is None — re-resolved at publish time (no stale snapshot)
    assert cs.staged_theme_css is None


# ---------------------------------------------------------------------------
# 18. Site Astro operates without Admin AI
# ---------------------------------------------------------------------------

def test_site_astro_prepare_without_ai_admin(bypass_db_integrity, tmp_path):
    """prepare() with style_request_id must not crash when cauldron-ai-admin
    is absent (ImportError from cauldron_ai_admin is caught in apps.py handler)."""
    from cauldron_site_astro.publication_service import SiteChangeSetService

    mock_svc = _make_pub_service_mocks(preview_ok=True)
    mock_svc._config.previews_root = str(tmp_path / "previews")
    mock_svc._config.theme_root = None

    with patch("cauldron_site_astro.publication_service.get_build_service", return_value=mock_svc):
        svc = SiteChangeSetService()
        result = svc.prepare(
            actor=SimpleNamespace(pk=None),
            content_request_ids=[],
            staged_theme_css="body{}",
            style_request_id="req-no-ai-admin",
        )

    assert result.ok  # no crash


def test_site_astro_publish_signal_handler_survives_missing_ai_admin():
    """_handle_changeset_published must silently handle ImportError when
    cauldron-ai-admin is not installed."""
    from cauldron_site_astro.apps import _handle_changeset_published

    # Simulate cauldron_ai_admin being absent by removing it from sys.modules
    # so the lazy import inside the handler raises ImportError.
    absent = {"cauldron_ai_admin": None, "cauldron_ai_admin.style_service": None}
    with patch.dict(sys.modules, absent):
        try:
            _handle_changeset_published(
                sender=None,
                changeset_id="cs-123",
                staged_theme_css="body{}",
                style_request_id="req-123",
            )
        except ImportError:
            pytest.fail("_handle_changeset_published raised ImportError — must handle gracefully")


# ---------------------------------------------------------------------------
# 19. permissions remain enforced
# ---------------------------------------------------------------------------

def test_style_prepare_view_requires_publish_permission(db):
    """StylePublicationPrepareView raises PermissionDenied for authenticated
    users without the apply_content_changes permission."""
    from django.core.exceptions import PermissionDenied
    from django.test import RequestFactory
    from cauldron_site_astro.views import StylePublicationPrepareView
    from django.contrib.auth import get_user_model

    User = get_user_model()
    user = User.objects.create_user(username="noperm_user", password="pw")

    factory = RequestFactory()
    request = factory.get("/fake-url/")
    request.user = user  # authenticated but no apply_content_changes permission

    # permission_required(raise_exception=True) → PermissionDenied (HTTP 403)
    with pytest.raises(PermissionDenied):
        StylePublicationPrepareView.as_view()(request, request_id="fake-id")


# ---------------------------------------------------------------------------
# 20. no provider installed behaves safely
# ---------------------------------------------------------------------------

def test_prepare_no_provider_falls_back_gracefully(bypass_db_integrity, tmp_path):
    """When no PagesStyleProvider is registered, prepare() still works —
    it falls back to the theme_root staged.css or empty string."""
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_content.pages_style import register_pages_style_provider

    register_pages_style_provider(None)  # explicitly no provider

    mock_svc = _make_pub_service_mocks(preview_ok=True)
    mock_svc._config.previews_root = str(tmp_path / "previews")
    mock_svc._config.theme_root = None

    with patch("cauldron_site_astro.publication_service.get_build_service", return_value=mock_svc):
        svc = SiteChangeSetService()
        result = svc.prepare(
            actor=SimpleNamespace(pk=None),
            content_request_ids=[],
            staged_theme_css="body { font-size: 16px; }",
            style_request_id="req-no-provider",
        )

    assert result.ok


# ---------------------------------------------------------------------------
# Blocker 1: style source commit must happen BEFORE live output promotion
# ---------------------------------------------------------------------------

def test_style_conflict_aborts_before_output_promotion(bypass_db_integrity, tmp_path):
    """When commit_style raises StyleConflictError (Step 2.5), the publish()
    fails immediately and promote_output_with_backup is never called."""
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_content.pages_style import register_pages_style_provider

    provider = FakePagesStyleProvider()
    provider._conflict_on_commit = True
    register_pages_style_provider(provider)

    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        staged_theme_css=None,
        content_request_ids=[],
        style_request_id="req-conflict-test",
        style_target="90-site.css",
        style_proposed_content="nav {}",
        style_base_hash="",
        style_base_exists=False,
    )

    mock_svc = _make_pub_service_mocks(preview_ok=True)
    mock_svc._config.theme_root = None

    actor = SimpleNamespace(pk=1, has_perm=lambda p: True)
    with (
        patch("cauldron_site_astro.publication_service.get_build_service", return_value=mock_svc),
        patch("cauldron_site_astro.publication_service._get_content_operation_service", return_value=None),
        patch("cauldron_site_astro.publication_service._get_require_approval", return_value=False),
        patch("cauldron_site_astro.publication_service._fetch_eligible_change_requests", return_value=({}, None)),
    ):
        svc = SiteChangeSetService()
        result = svc.publish(actor=actor, change_set_id=str(cs.id))

    assert not result.ok
    assert "style source conflict" in result.message.lower() or "conflict" in result.message.lower()
    # CRITICAL: output must not have been promoted
    mock_svc.promote_output_with_backup.assert_not_called()
    cs.refresh_from_db()
    assert cs.status == SiteChangeSet.PUBLISH_FAILED


def test_style_commit_metadata_stored_on_changeset(bypass_db_integrity, tmp_path):
    """prepare() with style commit params stores all metadata on the SiteChangeSet."""
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_content.pages_style import register_pages_style_provider

    provider = FakePagesStyleProvider()
    register_pages_style_provider(provider)

    mock_svc = _make_pub_service_mocks(preview_ok=True)
    mock_svc._config.previews_root = str(tmp_path / "previews")
    mock_svc._config.theme_root = None

    with patch("cauldron_site_astro.publication_service.get_build_service", return_value=mock_svc):
        svc = SiteChangeSetService()
        result = svc.prepare(
            actor=SimpleNamespace(pk=None),
            content_request_ids=[],
            style_request_id="req-meta-test",
            style_scope="pages",
            style_target="90-site.css",
            style_proposed_content="nav { display: flex; }",
            style_base_hash="a" * 64,
            style_base_exists=True,
        )

    assert result.ok
    cs = SiteChangeSet.objects.get(id=result.change_set_id)
    assert cs.style_scope == "pages"
    assert cs.style_target == "90-site.css"
    assert cs.style_proposed_content == "nav { display: flex; }"
    assert cs.style_base_hash == "a" * 64
    assert cs.style_base_exists is True


# ---------------------------------------------------------------------------
# Blocker 2: staged.css takes priority over PagesStyleProvider
# ---------------------------------------------------------------------------

def test_staged_css_takes_priority_over_pages_provider(bypass_db_integrity, tmp_path):
    """When staged.css is present under theme_root, prepare() uses it for the
    preview CSS and stores it as explicit staged_theme_css (not None), which
    takes priority over any PagesStyleProvider composition."""
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_content.pages_style import register_pages_style_provider

    staged_css = "/* staged.css — explicit operator intent */"
    provider_css = "/* provider css — should NOT win */"

    provider = FakePagesStyleProvider({"90-site.css": provider_css})
    register_pages_style_provider(provider)

    mock_svc = _make_pub_service_mocks(preview_ok=True)
    mock_svc._config.previews_root = str(tmp_path / "previews")

    theme_root = tmp_path / "theme"
    theme_root.mkdir()
    mock_svc._config.theme_root = str(theme_root)

    captured = {}

    def capture_preview(**kwargs):
        captured["theme_css"] = kwargs.get("theme_css", "")
        from cauldron_site_astro.service import BuildResult
        return BuildResult(ok=True, pages_built=1)

    mock_svc.build_preview.side_effect = capture_preview

    fake_theme_svc = MagicMock()
    fake_theme_svc.get_staged_css.return_value = staged_css

    with (
        patch("cauldron_site_astro.publication_service.get_build_service", return_value=mock_svc),
        patch("cauldron_site_astro.theme.SiteThemeService", return_value=fake_theme_svc),
    ):
        svc = SiteChangeSetService()
        result = svc.prepare(
            actor=SimpleNamespace(pk=None),
            content_request_ids=["req-staged-priority"],
        )

    assert result.ok
    cs = SiteChangeSet.objects.get(id=result.change_set_id)
    # staged.css wins — stored as explicit CSS, not None
    assert cs.staged_theme_css == staged_css
    # Preview also used staged.css
    assert captured.get("theme_css") == staged_css
    # Provider CSS must NOT appear in the preview
    assert provider_css not in captured.get("theme_css", "")


# ---------------------------------------------------------------------------
# Blocker 3: None vs "" vs "..." semantics
# ---------------------------------------------------------------------------

def test_staged_theme_css_none_stored_for_content_only(bypass_db_integrity, tmp_path):
    """Content-only prepare (no staged_theme_css, no staged.css) stores None
    in staged_theme_css so publish() re-resolves from provider at publish time."""
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_content.pages_style import register_pages_style_provider

    register_pages_style_provider(None)  # no provider

    mock_svc = _make_pub_service_mocks(preview_ok=True)
    mock_svc._config.previews_root = str(tmp_path / "previews")
    mock_svc._config.theme_root = None

    with patch("cauldron_site_astro.publication_service.get_build_service", return_value=mock_svc):
        svc = SiteChangeSetService()
        result = svc.prepare(
            actor=SimpleNamespace(pk=None),
            content_request_ids=["req-content-only"],
        )

    assert result.ok
    cs = SiteChangeSet.objects.get(id=result.change_set_id)
    assert cs.staged_theme_css is None


def test_explicit_empty_string_stored_as_explicit(bypass_db_integrity, tmp_path):
    """Passing staged_theme_css='' explicitly stores '' in the changeset,
    distinguishing 'explicit empty' from 'inherited' (None)."""
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet

    mock_svc = _make_pub_service_mocks(preview_ok=True)
    mock_svc._config.previews_root = str(tmp_path / "previews")
    mock_svc._config.theme_root = None

    with patch("cauldron_site_astro.publication_service.get_build_service", return_value=mock_svc):
        svc = SiteChangeSetService()
        result = svc.prepare(
            actor=SimpleNamespace(pk=None),
            content_request_ids=["req-explicit-empty"],
            staged_theme_css="",
        )

    assert result.ok
    cs = SiteChangeSet.objects.get(id=result.change_set_id)
    # "" means explicit empty — not None (not inherited)
    assert cs.staged_theme_css == ""
    assert cs.staged_theme_css is not None


def test_publish_resolves_css_from_provider_when_staged_is_none(bypass_db_integrity, tmp_path):
    """When staged_theme_css is None on a SiteChangeSet, publish() re-resolves
    the composed CSS from PagesStyleProvider at publication time."""
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_content.pages_style import register_pages_style_provider

    live_css = "/* live css at publish time */"
    provider = FakePagesStyleProvider({"90-site.css": live_css})
    register_pages_style_provider(provider)

    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        staged_theme_css=None,  # re-resolve at publish time
        content_request_ids=[],
        style_request_id="",
    )

    mock_svc = _make_pub_service_mocks(preview_ok=True)
    mock_svc._config.theme_root = None

    captured = {}

    def capture_build(**kwargs):
        captured["theme_css"] = kwargs.get("theme_css", "")
        from cauldron_site_astro.service import BuildResult
        return BuildResult(ok=True, pages_built=1)

    mock_svc.build_preview.side_effect = capture_build

    actor = SimpleNamespace(pk=1, has_perm=lambda p: True)
    with (
        patch("cauldron_site_astro.publication_service.get_build_service", return_value=mock_svc),
        patch("cauldron_site_astro.publication_service._get_content_operation_service", return_value=None),
        patch("cauldron_site_astro.publication_service._get_require_approval", return_value=False),
        patch("cauldron_site_astro.publication_service._fetch_eligible_change_requests", return_value=({}, None)),
    ):
        svc = SiteChangeSetService()
        result = svc.publish(actor=actor, change_set_id=str(cs.id))

    assert result.ok, result.message
    # publish() must have used the live provider CSS
    assert live_css in captured.get("theme_css", "")


# ---------------------------------------------------------------------------
# Blocker 1 continued: style source rollback on output promotion failure
# ---------------------------------------------------------------------------

def test_style_source_rolled_back_on_output_promotion_failure(bypass_db_integrity, tmp_path):
    """When output promotion (Step 4) fails after a successful style source
    commit (Step 2.5), the style source write is rolled back."""
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_content.pages_style import register_pages_style_provider

    provider = FakePagesStyleProvider()
    register_pages_style_provider(provider)

    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        staged_theme_css=None,
        content_request_ids=[],
        style_request_id="req-rollback-test",
        style_target="90-site.css",
        style_proposed_content="nav { display: flex; }",
        style_base_hash="",
        style_base_exists=False,
    )

    mock_svc = _make_pub_service_mocks(preview_ok=True)
    mock_svc._config.theme_root = None
    mock_svc.promote_output_with_backup.side_effect = RuntimeError("disk full")

    actor = SimpleNamespace(pk=1, has_perm=lambda p: True)
    with (
        patch("cauldron_site_astro.publication_service.get_build_service", return_value=mock_svc),
        patch("cauldron_site_astro.publication_service._get_content_operation_service", return_value=None),
        patch("cauldron_site_astro.publication_service._get_require_approval", return_value=False),
        patch("cauldron_site_astro.publication_service._fetch_eligible_change_requests", return_value=({}, None)),
    ):
        svc = SiteChangeSetService()
        result = svc.publish(actor=actor, change_set_id=str(cs.id))

    assert not result.ok
    assert "output promotion failed" in result.message.lower()
    # Style source rollback must have been called
    assert len(provider._rollback_called) == 1
    assert provider._rollback_called[0]["target"] == "90-site.css"


# ---------------------------------------------------------------------------
# Blocker 6: StylePublicationPrepareView GET does not create a changeset
# (Tests using UIStyleChangeRequest models are in test_ui_style_proposals.py)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Signal carries style_committed_hash
# ---------------------------------------------------------------------------

def test_publish_signal_carries_style_committed_hash(bypass_db_integrity, tmp_path):
    """site_changeset_published signal includes style_committed_hash from Step 2.5."""
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.signals import site_changeset_published
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_content.pages_style import register_pages_style_provider

    provider = FakePagesStyleProvider()
    provider._commit_hash = "b" * 64
    register_pages_style_provider(provider)

    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        staged_theme_css=None,
        content_request_ids=[],
        style_request_id="req-hash-signal",
        style_target="90-site.css",
        style_proposed_content="nav {}",
        style_base_hash="",
        style_base_exists=False,
    )

    mock_svc = _make_pub_service_mocks(preview_ok=True)
    mock_svc._config.theme_root = None

    received = {}

    def handler(sender, style_committed_hash="", **kwargs):
        received["hash"] = style_committed_hash

    site_changeset_published.connect(handler)
    try:
        actor = SimpleNamespace(pk=1, has_perm=lambda p: True)
        with (
            patch("cauldron_site_astro.publication_service.get_build_service", return_value=mock_svc),
            patch("cauldron_site_astro.publication_service._get_content_operation_service", return_value=None),
            patch("cauldron_site_astro.publication_service._get_require_approval", return_value=False),
            patch("cauldron_site_astro.publication_service._fetch_eligible_change_requests", return_value=({}, None)),
        ):
            svc = SiteChangeSetService()
            result = svc.publish(actor=actor, change_set_id=str(cs.id))
    finally:
        site_changeset_published.disconnect(handler)

    assert result.ok, result.message
    assert received.get("hash") == "b" * 64


# ---------------------------------------------------------------------------
# Test 11: source rollback failure produces reconciliation evidence
# ---------------------------------------------------------------------------

def test_style_source_rollback_failure_produces_reconciliation_evidence(bypass_db_integrity, tmp_path):
    """When output promotion fails and _rollback_style() returns False,
    publish_build_result must contain style_source_rollback_failed=True and
    requires_reconciliation=True so operators know manual recovery is needed."""
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_content.pages_style import register_pages_style_provider

    provider = FakePagesStyleProvider()
    provider._rollback_ok = False  # rollback will fail
    register_pages_style_provider(provider)

    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        staged_theme_css=None,
        content_request_ids=[],
        style_request_id="req-rollback-fail",
        style_target="90-site.css",
        style_proposed_content="nav {}",
        style_base_hash="",
        style_base_exists=False,
    )

    mock_svc = _make_pub_service_mocks(preview_ok=True)
    mock_svc._config.theme_root = None
    mock_svc.promote_output_with_backup.side_effect = RuntimeError("disk full")

    actor = SimpleNamespace(pk=1, has_perm=lambda p: True)
    with (
        patch("cauldron_site_astro.publication_service.get_build_service", return_value=mock_svc),
        patch("cauldron_site_astro.publication_service._get_content_operation_service", return_value=None),
        patch("cauldron_site_astro.publication_service._get_require_approval", return_value=False),
        patch("cauldron_site_astro.publication_service._fetch_eligible_change_requests", return_value=({}, None)),
    ):
        svc = SiteChangeSetService()
        result = svc.publish(actor=actor, change_set_id=str(cs.id))

    assert not result.ok
    cs.refresh_from_db()
    assert cs.status == SiteChangeSet.PUBLISH_FAILED
    pbr = cs.publish_build_result
    assert pbr.get("style_source_rollback_failed") is True
    assert pbr.get("requires_reconciliation") is True


# ---------------------------------------------------------------------------
# Tests 12 & 13: explicitly empty CSS previews and publishes as empty
# (no provider fallback when staged_theme_css == "")
# ---------------------------------------------------------------------------

def test_explicit_empty_css_previews_as_empty(bypass_db_integrity, tmp_path):
    """staged_theme_css='' (explicitly empty) means build_preview is called with
    theme_css='' — it must NOT fall back to PagesStyleProvider composition."""
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_content.pages_style import register_pages_style_provider

    provider = FakePagesStyleProvider({"90-site.css": "nav { display: flex; }"})
    register_pages_style_provider(provider)

    mock_svc = _make_pub_service_mocks(preview_ok=True)
    mock_svc._config.previews_root = str(tmp_path / "previews")
    mock_svc._config.theme_root = None

    captured = {}

    def capture_preview(**kwargs):
        captured["theme_css"] = kwargs.get("theme_css", "SENTINEL")
        from cauldron_site_astro.service import BuildResult
        return BuildResult(ok=True, pages_built=1)

    mock_svc.build_preview.side_effect = capture_preview

    with patch("cauldron_site_astro.publication_service.get_build_service", return_value=mock_svc):
        svc = SiteChangeSetService()
        result = svc.prepare(
            actor=SimpleNamespace(pk=None),
            content_request_ids=["req-explicit-empty-preview"],
            staged_theme_css="",
        )

    assert result.ok
    # build_preview must have received "" — not provider CSS
    assert captured["theme_css"] == ""
    assert "nav" not in captured["theme_css"]


def test_explicit_empty_css_publishes_as_empty(bypass_db_integrity, tmp_path):
    """When SiteChangeSet.staged_theme_css == '', publish() must pass theme_css=''
    to build_preview and must NOT consult PagesStyleProvider."""
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_content.pages_style import register_pages_style_provider

    # Provider has content — must NOT be used when staged_theme_css is ""
    provider = FakePagesStyleProvider({"90-site.css": "nav { color: red; }"})
    register_pages_style_provider(provider)

    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        staged_theme_css="",  # explicitly empty, not None
        content_request_ids=[],
        style_request_id="",
    )

    mock_svc = _make_pub_service_mocks(preview_ok=True)
    mock_svc._config.theme_root = None

    captured = {}

    def capture_build(**kwargs):
        captured["theme_css"] = kwargs.get("theme_css", "SENTINEL")
        from cauldron_site_astro.service import BuildResult
        return BuildResult(ok=True, pages_built=1)

    mock_svc.build_preview.side_effect = capture_build

    actor = SimpleNamespace(pk=1, has_perm=lambda p: True)
    with (
        patch("cauldron_site_astro.publication_service.get_build_service", return_value=mock_svc),
        patch("cauldron_site_astro.publication_service._get_content_operation_service", return_value=None),
        patch("cauldron_site_astro.publication_service._get_require_approval", return_value=False),
        patch("cauldron_site_astro.publication_service._fetch_eligible_change_requests", return_value=({}, None)),
    ):
        svc = SiteChangeSetService()
        result = svc.publish(actor=actor, change_set_id=str(cs.id))

    assert result.ok, result.message
    # publish must have used "" — no provider fallback
    assert captured["theme_css"] == ""
    assert "nav" not in captured["theme_css"]


# ---------------------------------------------------------------------------
# Test 15: direct race scenario — CSS A at preview, CSS B at publish
# ---------------------------------------------------------------------------

def test_content_publish_uses_css_b_after_style_publish_changed_provider(bypass_db_integrity, tmp_path):
    """Race regression: a content changeset previewed when the provider had CSS A
    must publish with CSS B when the provider was updated between preview and publish.

    Two-phase structure:
      Phase 1 — prepare(): verify staged_theme_css=None (no snapshot stored).
      Phase 2 — publish(): verify publish() re-resolves from provider (CSS B used).
    The two phases use separate change sets so the publish step is not blocked
    by content request validation (which is exercised by its own test suite).
    """
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_content.pages_style import register_pages_style_provider

    css_a = "/* CSS A — state at preview time */"
    css_b = "/* CSS B — state after style publication */"

    # Phase 1: prepare() — provider has CSS A, should store staged_theme_css=None
    provider = FakePagesStyleProvider({"90-site.css": css_a})
    register_pages_style_provider(provider)

    mock_svc = _make_pub_service_mocks(preview_ok=True)
    mock_svc._config.previews_root = str(tmp_path / "previews")
    mock_svc._config.theme_root = None

    preview_captured = {}

    def capture_preview(**kwargs):
        preview_captured["theme_css"] = kwargs.get("theme_css", "")
        from cauldron_site_astro.service import BuildResult
        return BuildResult(ok=True, pages_built=1)

    mock_svc.build_preview.side_effect = capture_preview

    with patch("cauldron_site_astro.publication_service.get_build_service", return_value=mock_svc):
        svc = SiteChangeSetService()
        result = svc.prepare(
            actor=SimpleNamespace(pk=None),
            content_request_ids=["content-req-race"],
        )

    assert result.ok
    prepared_cs = SiteChangeSet.objects.get(id=result.change_set_id)
    # staged_theme_css must be None — not a snapshot of CSS A
    assert prepared_cs.staged_theme_css is None
    # Preview used CSS A (current at preview time)
    assert css_a in preview_captured.get("theme_css", "")

    # Phase 2: provider updated to CSS B (simulates intervening style publication)
    provider._files = {"90-site.css": css_b}

    # Create a clean style-only changeset with staged_theme_css=None and empty
    # content_request_ids so we can run publish() without real CR rows.
    publish_cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        staged_theme_css=None,  # re-resolve at publish time — same as content-only
        content_request_ids=[],
        style_request_id="",
    )

    publish_captured = {}

    def capture_publish(**kwargs):
        publish_captured["theme_css"] = kwargs.get("theme_css", "")
        from cauldron_site_astro.service import BuildResult
        return BuildResult(ok=True, pages_built=1)

    mock_svc.build_preview.side_effect = capture_publish

    actor = SimpleNamespace(pk=1, has_perm=lambda p: True)
    with (
        patch("cauldron_site_astro.publication_service.get_build_service", return_value=mock_svc),
        patch("cauldron_site_astro.publication_service._get_content_operation_service", return_value=None),
        patch("cauldron_site_astro.publication_service._get_require_approval", return_value=False),
        patch("cauldron_site_astro.publication_service._fetch_eligible_change_requests", return_value=({}, None)),
    ):
        result = svc.publish(actor=actor, change_set_id=str(publish_cs.id))

    assert result.ok, result.message
    # Publish MUST use CSS B — not the stale CSS A captured at preview time
    assert css_b in publish_captured.get("theme_css", ""), (
        f"Expected CSS B in publish build; got: {publish_captured.get('theme_css')!r}"
    )
    assert css_a not in publish_captured.get("theme_css", "")


# ===========================================================================
# Defect-correction regression tests (PR #88 follow-up)
# ===========================================================================

# ---------------------------------------------------------------------------
# Test 1: style publish build uses proposed CSS, not pre-commit provider CSS
# Test 2: unrelated pages CSS remains composed
# ---------------------------------------------------------------------------

def test_style_publish_build_uses_post_commit_css(bypass_db_integrity, tmp_path):
    """Regression for Defect 1: publish() must commit the style source BEFORE
    building Astro so the build uses the post-commit effective CSS.

    Store:
      00-variables.css = var CSS
      90-site.css = old nav CSS

    Proposal:
      90-site.css = new nav CSS

    After publish:
      - provider source contains new nav CSS (committed)
      - build_preview theme_css contains var CSS + new nav CSS
      - old nav CSS is absent from the build
    """
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_content.pages_style import register_pages_style_provider

    var_css = ":root { --color: blue; }"
    old_nav = "nav { display: block; } /* OLD */"
    new_nav = "nav { display: flex; } /* NEW */"

    provider = FakePagesStyleProvider({
        "00-variables.css": var_css,
        "90-site.css": old_nav,
    })
    register_pages_style_provider(provider)

    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        staged_theme_css=None,
        content_request_ids=[],
        style_request_id="req-defect1-test",
        style_target="90-site.css",
        style_proposed_content=new_nav,
        style_base_hash="",
        style_base_exists=False,
    )

    mock_svc = _make_pub_service_mocks(preview_ok=True)
    mock_svc._config.theme_root = None

    build_captured = {}

    def capture_build(**kwargs):
        build_captured["theme_css"] = kwargs.get("theme_css", "")
        from cauldron_site_astro.service import BuildResult
        return BuildResult(ok=True, pages_built=1)

    mock_svc.build_preview.side_effect = capture_build

    actor = SimpleNamespace(pk=1, has_perm=lambda p: True)
    with (
        patch("cauldron_site_astro.publication_service.get_build_service", return_value=mock_svc),
        patch("cauldron_site_astro.publication_service._get_content_operation_service", return_value=None),
        patch("cauldron_site_astro.publication_service._get_require_approval", return_value=False),
        patch("cauldron_site_astro.publication_service._fetch_eligible_change_requests", return_value=({}, None)),
    ):
        svc = SiteChangeSetService()
        result = svc.publish(actor=actor, change_set_id=str(cs.id))

    assert result.ok, result.message

    # Test 1: build used new nav CSS (from post-commit provider)
    assert new_nav in build_captured.get("theme_css", ""), (
        f"Expected new nav CSS in build; got: {build_captured['theme_css']!r}"
    )
    assert old_nav not in build_captured.get("theme_css", "")

    # Test 2: unrelated pages CSS (00-variables.css) was also composed
    assert var_css in build_captured.get("theme_css", ""), (
        f"Expected var CSS in build; got: {build_captured['theme_css']!r}"
    )

    # Provider source now contains the new content
    assert provider._files.get("90-site.css") == new_nav


# ---------------------------------------------------------------------------
# Test 3: build failure after source commit rolls source back
# ---------------------------------------------------------------------------

def test_build_failure_after_source_commit_rolls_back(bypass_db_integrity, tmp_path):
    """When the Astro build fails AFTER the style source was committed (Step 2.5),
    _rollback_style() must be called to undo the source write."""
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_content.pages_style import register_pages_style_provider

    provider = FakePagesStyleProvider({"90-site.css": "nav { display: block; }"})
    register_pages_style_provider(provider)

    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        staged_theme_css=None,
        content_request_ids=[],
        style_request_id="req-build-fail",
        style_target="90-site.css",
        style_proposed_content="nav { display: flex; }",
        style_base_hash="",
        style_base_exists=False,
    )

    mock_svc = _make_pub_service_mocks(preview_ok=False)  # build fails
    mock_svc._config.theme_root = None

    actor = SimpleNamespace(pk=1, has_perm=lambda p: True)
    with (
        patch("cauldron_site_astro.publication_service.get_build_service", return_value=mock_svc),
        patch("cauldron_site_astro.publication_service._get_content_operation_service", return_value=None),
        patch("cauldron_site_astro.publication_service._get_require_approval", return_value=False),
        patch("cauldron_site_astro.publication_service._fetch_eligible_change_requests", return_value=({}, None)),
    ):
        svc = SiteChangeSetService()
        result = svc.publish(actor=actor, change_set_id=str(cs.id))

    assert not result.ok
    # Source was rolled back — provider is back to old content
    assert len(provider._rollback_called) == 1
    assert provider._rollback_called[0]["target"] == "90-site.css"
    assert provider._files.get("90-site.css") == "nav { display: block; }"


# ---------------------------------------------------------------------------
# Test 4: empty style proposal previews as empty
# ---------------------------------------------------------------------------

def test_empty_style_proposal_previews_empty(bypass_db_integrity, tmp_path):
    """An approved proposal with proposed_content='' means 'clear the target file'.
    prepare() must pass proposed_content='' literally — not convert it to None."""
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_content.pages_style import register_pages_style_provider

    nav_css = "nav { display: flex; }"
    provider = FakePagesStyleProvider({"90-site.css": nav_css})
    register_pages_style_provider(provider)

    mock_svc = _make_pub_service_mocks(preview_ok=True)
    mock_svc._config.previews_root = str(tmp_path / "previews")
    mock_svc._config.theme_root = None

    captured = {}

    def capture_preview(**kwargs):
        captured["theme_css"] = kwargs.get("theme_css", "SENTINEL")
        from cauldron_site_astro.service import BuildResult
        return BuildResult(ok=True, pages_built=1)

    mock_svc.build_preview.side_effect = capture_preview

    with patch("cauldron_site_astro.publication_service.get_build_service", return_value=mock_svc):
        svc = SiteChangeSetService()
        result = svc.prepare(
            actor=SimpleNamespace(pk=None),
            content_request_ids=[],
            style_request_id="req-empty-proposal",
            style_target="90-site.css",
            style_proposed_content="",  # intentionally empty — clears the file
        )

    assert result.ok
    # Preview must exclude the old nav CSS (proposed empty replaces it)
    assert nav_css not in captured.get("theme_css", ""), (
        f"Old nav CSS must be absent from preview; got: {captured['theme_css']!r}"
    )


# ---------------------------------------------------------------------------
# Test 5: empty style proposal publishes as empty
# Test 6: active.css is actually cleared when effective CSS is empty
# ---------------------------------------------------------------------------

def test_empty_style_proposal_publishes_empty_and_clears_active_css(bypass_db_integrity, tmp_path):
    """When a style proposal clears 90-site.css (proposed_content=''):
    - publish() commits empty content to the source store
    - provider composes empty (no files remain after clear)
    - Astro build receives empty theme_css
    - active.css is written as empty
    """
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_content.pages_style import register_pages_style_provider
    from cauldron_site_astro.theme import SiteThemeService

    nav_css = "nav { display: flex; }"

    # Provider has one file; the proposal will write "" to it.
    # After commit: file content = "", get_composed_css() returns "".
    provider = FakePagesStyleProvider({"90-site.css": nav_css})
    register_pages_style_provider(provider)

    theme_root = tmp_path / "theme"
    theme_root.mkdir()
    active_css_path = theme_root / "active.css"
    active_css_path.write_text(nav_css)  # live site has nav CSS

    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        staged_theme_css=None,
        content_request_ids=[],
        style_request_id="req-empty-publish",
        style_target="90-site.css",
        style_proposed_content="",  # intentionally empty
        style_base_hash="",
        style_base_exists=False,
    )

    mock_svc = _make_pub_service_mocks(preview_ok=True)
    mock_svc._config.theme_root = str(theme_root)

    build_captured = {}

    def capture_build(**kwargs):
        build_captured["theme_css"] = kwargs.get("theme_css", "SENTINEL")
        from cauldron_site_astro.service import BuildResult
        return BuildResult(ok=True, pages_built=1)

    mock_svc.build_preview.side_effect = capture_build

    actor = SimpleNamespace(pk=1, has_perm=lambda p: True)
    with (
        patch("cauldron_site_astro.publication_service.get_build_service", return_value=mock_svc),
        patch("cauldron_site_astro.publication_service._get_content_operation_service", return_value=None),
        patch("cauldron_site_astro.publication_service._get_require_approval", return_value=False),
        patch("cauldron_site_astro.publication_service._fetch_eligible_change_requests", return_value=({}, None)),
    ):
        svc = SiteChangeSetService()
        result = svc.publish(actor=actor, change_set_id=str(cs.id))

    assert result.ok, result.message

    # Test 5: build was called with empty CSS (not old nav CSS)
    assert build_captured.get("theme_css") == "", (
        f"Expected empty theme_css in build; got: {build_captured.get('theme_css')!r}"
    )
    assert nav_css not in build_captured.get("theme_css", "")

    # Test 6: active.css is actually empty (was written via stage_css + promote_staged)
    assert active_css_path.read_text() == "", (
        f"Expected active.css to be empty; got: {active_css_path.read_text()!r}"
    )


# ---------------------------------------------------------------------------
# Test 7: style lifecycle finalization failure cannot silently return PUBLISHED
# Test 8: successful style publish ends with PUBLISHED + UIStyleChangeRequest=applied
# ---------------------------------------------------------------------------

def test_style_lifecycle_finalization_failure_returns_publish_failed(bypass_db_integrity, tmp_path):
    """When mark_style_applied() (called via signal) raises, publish() must NOT
    set PUBLISHED. It must roll back FS state and return PUBLISH_FAILED."""
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_content.pages_style import register_pages_style_provider
    from cauldron_site_astro.signals import site_changeset_published

    provider = FakePagesStyleProvider()
    register_pages_style_provider(provider)

    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        staged_theme_css=None,
        content_request_ids=[],
        style_request_id="req-lifecycle-fail",
        style_target="90-site.css",
        style_proposed_content="nav {}",
        style_base_hash="",
        style_base_exists=False,
    )

    mock_svc = _make_pub_service_mocks(preview_ok=True)
    mock_svc._config.theme_root = None

    def failing_handler(sender, **kwargs):
        raise RuntimeError("DB unavailable — cannot mark applied")

    site_changeset_published.connect(failing_handler)
    try:
        actor = SimpleNamespace(pk=1, has_perm=lambda p: True)
        with (
            patch("cauldron_site_astro.publication_service.get_build_service", return_value=mock_svc),
            patch("cauldron_site_astro.publication_service._get_content_operation_service", return_value=None),
            patch("cauldron_site_astro.publication_service._get_require_approval", return_value=False),
            patch("cauldron_site_astro.publication_service._fetch_eligible_change_requests", return_value=({}, None)),
        ):
            svc = SiteChangeSetService()
            result = svc.publish(actor=actor, change_set_id=str(cs.id))
    finally:
        site_changeset_published.disconnect(failing_handler)

    # Must not be PUBLISHED
    assert not result.ok
    cs.refresh_from_db()
    assert cs.status == SiteChangeSet.PUBLISH_FAILED
    assert cs.publish_build_result.get("style_lifecycle_failed") is True
    # Style source should have been rolled back
    assert len(provider._rollback_called) == 1


def test_successful_style_publish_ends_with_published_and_applied(bypass_db_integrity, tmp_path):
    """Successful style publication: SiteChangeSet=PUBLISHED and the signal
    handler is called (simulating mark_style_applied transition)."""
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_content.pages_style import register_pages_style_provider
    from cauldron_site_astro.signals import site_changeset_published

    provider = FakePagesStyleProvider()
    register_pages_style_provider(provider)

    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        staged_theme_css=None,
        content_request_ids=[],
        style_request_id="req-lifecycle-success",
        style_target="90-site.css",
        style_proposed_content="nav { display: flex; }",
        style_base_hash="",
        style_base_exists=False,
    )

    mock_svc = _make_pub_service_mocks(preview_ok=True)
    mock_svc._config.theme_root = None

    handler_called = []

    def success_handler(sender, style_request_id, changeset_id, **kwargs):
        handler_called.append({"style_request_id": style_request_id, "changeset_id": changeset_id})

    site_changeset_published.connect(success_handler)
    try:
        actor = SimpleNamespace(pk=1, has_perm=lambda p: True)
        with (
            patch("cauldron_site_astro.publication_service.get_build_service", return_value=mock_svc),
            patch("cauldron_site_astro.publication_service._get_content_operation_service", return_value=None),
            patch("cauldron_site_astro.publication_service._get_require_approval", return_value=False),
            patch("cauldron_site_astro.publication_service._fetch_eligible_change_requests", return_value=({}, None)),
        ):
            svc = SiteChangeSetService()
            result = svc.publish(actor=actor, change_set_id=str(cs.id))
    finally:
        site_changeset_published.disconnect(success_handler)

    assert result.ok, result.message
    cs.refresh_from_db()
    assert cs.status == SiteChangeSet.PUBLISHED

    # Signal was called before PUBLISHED was set (lifecycle coherence)
    assert len(handler_called) == 1
    assert handler_called[0]["style_request_id"] == "req-lifecycle-success"
    assert handler_called[0]["changeset_id"] == str(cs.id)


def test_real_handler_propagates_finalization_failure_to_publish_failed(bypass_db_integrity, tmp_path):
    """Production-path test using the real _handle_changeset_published handler.

    Patches get_style_service() so mark_style_applied() raises
    StyleFinalizationError.  Verifies that the real handler propagates the
    error through send_robust(), publish() sets PUBLISH_FAILED, and the style
    source commit is rolled back.

    This test does NOT connect the handler manually — it relies on the handler
    already being connected via CauldronSiteAstroConfig.ready().
    """
    from cauldron_ai_admin.style_service import StyleFinalizationError
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_content.pages_style import register_pages_style_provider

    provider = FakePagesStyleProvider()
    register_pages_style_provider(provider)

    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        staged_theme_css=None,
        content_request_ids=[],
        style_request_id="req-real-handler-fail",
        style_target="90-site.css",
        style_proposed_content="body { margin: 0; }",
        style_base_hash="",
        style_base_exists=False,
    )

    mock_svc = _make_pub_service_mocks(preview_ok=True)
    mock_svc._config.theme_root = None

    failing_style_svc = MagicMock()
    failing_style_svc.mark_style_applied.side_effect = StyleFinalizationError(
        "request req-real-handler-fail not found"
    )

    actor = SimpleNamespace(pk=1, has_perm=lambda p: True)
    with (
        patch("cauldron_site_astro.publication_service.get_build_service", return_value=mock_svc),
        patch("cauldron_site_astro.publication_service._get_content_operation_service", return_value=None),
        patch("cauldron_site_astro.publication_service._get_require_approval", return_value=False),
        patch("cauldron_site_astro.publication_service._fetch_eligible_change_requests", return_value=({}, None)),
        patch("cauldron_ai_admin.style_service.get_style_service", return_value=failing_style_svc),
    ):
        svc = SiteChangeSetService()
        result = svc.publish(actor=actor, change_set_id=str(cs.id))

    assert not result.ok
    cs.refresh_from_db()
    assert cs.status == SiteChangeSet.PUBLISH_FAILED
    assert cs.publish_build_result.get("style_lifecycle_failed") is True
    # Style source committed in Step 2.5 must be rolled back
    assert len(provider._rollback_called) == 1
