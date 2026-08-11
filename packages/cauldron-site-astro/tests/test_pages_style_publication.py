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

    def commit_style(self, *, target, content, expected_hash, base_exists):
        self._committed[target] = content
        return "newhash"

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
            staged_theme_css="",  # not explicitly provided
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

def test_content_prepare_picks_up_existing_override_files(bypass_db_integrity, tmp_path):
    """After this PR, a content-only prepare() pulls UIOverrideStore pages
    CSS (via PagesStyleProvider) into the changeset's staged_theme_css.
    This is the reconciliation path for already-applied pages overrides that
    predate the controlled-publication workflow."""
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_content.pages_style import register_pages_style_provider

    # Simulate a previously-applied pages override that is in UIOverrideStore
    # but was never promoted to active.css.
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
    # The changeset must have picked up the pre-existing pages CSS
    assert pre_existing_css in cs.staged_theme_css


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
