"""Tests for :class:`SiteChangeSetService` — the shared preview/publish service.

These tests verify the domain service directly (not through the AI-tool
adapters) so both callers exercise the same code path.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.django_db


def _make_config(tmp_path: Path, theme_root: str = "", previews_root: str = ""):
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


def _make_build_result(ok=True, pages_built=1, output_dir="/tmp/out", error="", build_log=""):
    from cauldron_site_astro.service import BuildResult
    return BuildResult(
        ok=ok,
        pages_built=pages_built,
        output_dir=output_dir,
        error=error,
        build_log=build_log,
    )


def _make_mock_svc(config, pages_result=None):
    svc = MagicMock()
    svc._config = config
    if pages_result is not None:
        svc.build_preview.return_value = pages_result
    return svc


def _actor(username="pub-svc-user", *, superuser=True):
    """Return a User with is_superuser=True by default so has_perm returns True."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user, _ = User.objects.get_or_create(
        username=username,
        defaults={"is_superuser": superuser, "is_staff": True},
    )
    if user.is_superuser != superuser:
        user.is_superuser = superuser
        user.save(update_fields=["is_superuser"])
    return user


def _actor_without_perms(username="no-perm-user"):
    """Actor whose has_perm always returns False."""
    return SimpleNamespace(has_perm=lambda _p: False, pk=None)


# ---------------------------------------------------------------------------
# prepare()
# ---------------------------------------------------------------------------


def test_prepare_creates_draft_ready_on_happy_path(tmp_path, bypass_db_integrity):
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet

    previews_root = tmp_path / "previews"
    config = _make_config(tmp_path, previews_root=str(previews_root))
    svc = _make_mock_svc(config, pages_result=_make_build_result(ok=True, pages_built=2))

    with patch("cauldron_site_astro.publication_service.get_build_service", return_value=svc):
        result = SiteChangeSetService().prepare(
            actor=_actor(),
            content_request_ids=["req-1"],
            staged_theme_css="body { color: teal; }",
        )

    assert result.ok is True
    assert result.status == SiteChangeSet.DRAFT_READY
    assert result.pages_built == 2
    # preview URL must be Django URL path.
    assert result.preview_url.startswith("/")

    cs = SiteChangeSet.objects.get(id=result.change_set_id)
    assert cs.status == SiteChangeSet.DRAFT_READY
    assert cs.draft_ready_at is not None
    assert cs.staged_theme_css == "body { color: teal; }"


def test_prepare_rejects_empty_content_request_ids(tmp_path):
    from cauldron_site_astro.publication_service import SiteChangeSetService

    result = SiteChangeSetService().prepare(
        actor=_actor(),
        content_request_ids=[],
    )

    assert result.ok is False
    assert "content_request_ids" in result.message


def test_prepare_marks_preview_failed_on_build_failure(tmp_path, bypass_db_integrity):
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet

    previews_root = tmp_path / "previews"
    config = _make_config(tmp_path, previews_root=str(previews_root))
    svc = _make_mock_svc(
        config,
        pages_result=_make_build_result(ok=False, error="Astro exploded", build_log="stderr..."),
    )

    with patch("cauldron_site_astro.publication_service.get_build_service", return_value=svc):
        result = SiteChangeSetService().prepare(
            actor=_actor(),
            content_request_ids=["req-x"],
        )

    assert result.ok is False
    cs = SiteChangeSet.objects.get(id=result.change_set_id)
    assert cs.status == SiteChangeSet.PREVIEW_FAILED


def test_prepare_no_previews_root_returns_error(tmp_path):
    from cauldron_site_astro.publication_service import SiteChangeSetService

    config = _make_config(tmp_path, previews_root="")
    svc = _make_mock_svc(config)

    with patch("cauldron_site_astro.publication_service.get_build_service", return_value=svc):
        result = SiteChangeSetService().prepare(
            actor=_actor(),
            content_request_ids=["r-1"],
        )

    assert result.ok is False
    assert "previews_root" in result.message


# ---------------------------------------------------------------------------
# inspect()
# ---------------------------------------------------------------------------


def test_inspect_returns_status_of_change_set(tmp_path):
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet

    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        content_request_ids=["req-inspect"],
        preview_dir="p1",
    )
    previews_root = tmp_path / "previews"
    (previews_root / "p1").mkdir(parents=True)
    (previews_root / "p1" / "index.html").write_text("<html></html>")

    config = _make_config(tmp_path, previews_root=str(previews_root))
    svc = _make_mock_svc(config)

    with patch("cauldron_site_astro.publication_service.get_build_service", return_value=svc):
        result = SiteChangeSetService().inspect(str(cs.id))

    assert result.ok is True
    assert result.status == SiteChangeSet.DRAFT_READY
    assert result.pages_built == 1
    assert result.preview_url.startswith("/")


def test_inspect_returns_error_for_missing_change_set():
    import uuid as _uuid
    from cauldron_site_astro.publication_service import SiteChangeSetService

    result = SiteChangeSetService().inspect(str(_uuid.uuid4()))
    assert result.ok is False
    assert "not found" in result.message


# ---------------------------------------------------------------------------
# publish()
# ---------------------------------------------------------------------------


def test_publish_produces_published_state(tmp_path):
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet

    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        content_request_ids=[],
    )
    config = _make_config(tmp_path)
    svc = _make_mock_svc(config, pages_result=_make_build_result(ok=True, pages_built=3))

    with patch("cauldron_site_astro.publication_service.get_build_service", return_value=svc):
        result = SiteChangeSetService().publish(
            actor=_actor(),
            change_set_id=str(cs.id),
        )

    assert result.ok is True, result.message
    assert result.status == SiteChangeSet.PUBLISHED
    assert result.live_url == "/"
    assert result.pages_built == 3

    cs.refresh_from_db()
    assert cs.status == SiteChangeSet.PUBLISHED
    assert cs.published_at is not None


def test_publish_rolls_back_on_content_apply_failure(tmp_path):
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet

    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        content_request_ids=["req-fail"],
    )
    config = _make_config(tmp_path)
    svc = _make_mock_svc(config, pages_result=_make_build_result(ok=True, pages_built=1))

    fake_content_svc = MagicMock()
    fake_content_svc.validate_change_request.return_value = MagicMock(ok=True, request_version=1)
    fake_content_svc.apply_change_request.return_value = MagicMock(
        ok=False, error=MagicMock(message="apply blew up"),
    )

    with patch("cauldron_site_astro.publication_service.get_build_service", return_value=svc):
        with patch(
            "cauldron_site_astro.publication_service._get_content_operation_service",
            return_value=fake_content_svc,
        ):
            result = SiteChangeSetService().publish(
                actor=_actor(),
                change_set_id=str(cs.id),
            )

    assert result.ok is False
    cs.refresh_from_db()
    assert cs.status == SiteChangeSet.PUBLISH_FAILED
    assert cs.publish_build_result.get("applied") == []


def test_publish_already_published_change_set_is_idempotent(tmp_path):
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet

    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.PUBLISHED,
        publish_build_result={"applied": ["r1"], "pages_built": 4},
    )

    # No build service patch needed — idempotent path never touches it.
    result = SiteChangeSetService().publish(
        actor=_actor(),
        change_set_id=str(cs.id),
    )

    assert result.ok is True
    assert result.status == SiteChangeSet.PUBLISHED
    assert result.pages_built == 4
    assert result.applied_request_ids == ["r1"]


def test_publish_failed_can_be_retried_to_success(tmp_path):
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet

    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.PUBLISH_FAILED,
        content_request_ids=[],
    )
    config = _make_config(tmp_path)
    svc = _make_mock_svc(config, pages_result=_make_build_result(ok=True, pages_built=1))

    with patch("cauldron_site_astro.publication_service.get_build_service", return_value=svc):
        result = SiteChangeSetService().publish(
            actor=_actor(),
            change_set_id=str(cs.id),
        )

    assert result.ok is True
    cs.refresh_from_db()
    assert cs.status == SiteChangeSet.PUBLISHED


def test_publish_rejects_unauthorized_actor(tmp_path):
    """Actor lacking apply_content_changes permission cannot publish."""
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet

    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        content_request_ids=[],
    )

    result = SiteChangeSetService().publish(
        actor=_actor_without_perms(),
        change_set_id=str(cs.id),
    )

    assert result.ok is False
    assert "apply_content_changes" in result.message
    # Status is unchanged — not marked PUBLISHING, no PUBLISH_FAILED.
    cs.refresh_from_db()
    assert cs.status == SiteChangeSet.DRAFT_READY


def test_publish_rejects_non_draft_ready_or_failed_status():
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet

    cs = SiteChangeSet.objects.create(status=SiteChangeSet.PREPARING)
    result = SiteChangeSetService().publish(
        actor=_actor(),
        change_set_id=str(cs.id),
    )
    assert result.ok is False
    assert "draft_ready" in result.message


def test_publish_missing_change_set_returns_error():
    import uuid as _uuid
    from cauldron_site_astro.publication_service import SiteChangeSetService

    result = SiteChangeSetService().publish(
        actor=_actor(),
        change_set_id=str(_uuid.uuid4()),
    )
    assert result.ok is False
    assert "not found" in result.message


# ---------------------------------------------------------------------------
# Real production-path tests — no bypass_db_integrity fixture.
# These exercise _check_request_integrity and _fetch_eligible_change_requests
# against real ContentChangeRequest rows.
# ---------------------------------------------------------------------------


def _make_ccr(*, request_id, lifecycle_state="proposed", workspace_changeset_id="ws-test-x",
              request_version=1, operations=None):
    """Create a real ContentChangeRequest row for integrity-path tests."""
    from cauldron_content_operations.models import ContentChangeRequest
    cr = ContentChangeRequest.objects.create(
        request_id=request_id,
        workspace_changeset_id=workspace_changeset_id,
        provider_name="flatfile",
        lifecycle_state=lifecycle_state,
        request_version=request_version,
        payload_hash="",
    )
    return cr


# --- prepare() production-path -------------------------------------------


def test_prepare_fails_missing_content_request(tmp_path):
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet

    previews_root = tmp_path / "previews"
    config = _make_config(tmp_path, previews_root=str(previews_root))
    svc = _make_mock_svc(config, pages_result=_make_build_result(ok=True))

    with patch("cauldron_site_astro.publication_service.get_build_service", return_value=svc):
        result = SiteChangeSetService().prepare(
            actor=_actor(),
            content_request_ids=["missing-id"],
        )

    assert result.ok is False
    assert "not found" in result.message
    assert SiteChangeSet.objects.count() == 0


def test_prepare_fails_terminal_lifecycle_state(tmp_path):
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet

    _make_ccr(request_id="terminal-1", lifecycle_state="applied")

    previews_root = tmp_path / "previews"
    config = _make_config(tmp_path, previews_root=str(previews_root))
    svc = _make_mock_svc(config, pages_result=_make_build_result(ok=True))

    with patch("cauldron_site_astro.publication_service.get_build_service", return_value=svc):
        result = SiteChangeSetService().prepare(
            actor=_actor(),
            content_request_ids=["terminal-1"],
        )

    assert result.ok is False
    assert SiteChangeSet.objects.count() == 0


def test_prepare_fails_operation_free_request(tmp_path):
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet

    # No workspace_changeset_id and no inline operations set.
    _make_ccr(request_id="op-free-1", workspace_changeset_id="")

    previews_root = tmp_path / "previews"
    config = _make_config(tmp_path, previews_root=str(previews_root))
    svc = _make_mock_svc(config, pages_result=_make_build_result(ok=True))

    with patch("cauldron_site_astro.publication_service.get_build_service", return_value=svc):
        result = SiteChangeSetService().prepare(
            actor=_actor(),
            content_request_ids=["op-free-1"],
        )

    assert result.ok is False
    assert "no operations" in result.message
    assert SiteChangeSet.objects.count() == 0


def test_prepare_accepts_proposed_request(tmp_path):
    """Proposed CR with a workspace_changeset_id whose ops load successfully."""
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet

    _make_ccr(request_id="proposed-1", workspace_changeset_id="ws-1")

    previews_root = tmp_path / "previews"
    config = _make_config(tmp_path, previews_root=str(previews_root))
    svc = _make_mock_svc(config, pages_result=_make_build_result(ok=True, pages_built=1))

    # Mock content op service so workspace.load_changeset returns operations.
    fake_workspace = MagicMock()
    fake_workspace.load_changeset.return_value = SimpleNamespace(
        operations=[SimpleNamespace(item_id="i1", kind="create", slug="s1", schema="page",
                                    collection="pages", data={}, body="")]
    )
    fake_content_svc = MagicMock()
    fake_content_svc._workspace = fake_workspace

    with patch("cauldron_site_astro.publication_service.get_build_service", return_value=svc), \
         patch("cauldron_site_astro.publication_service._get_content_operation_service",
               return_value=fake_content_svc):
        result = SiteChangeSetService().prepare(
            actor=_actor(),
            content_request_ids=["proposed-1"],
        )

    assert result.ok is True, result.message
    assert result.status == SiteChangeSet.DRAFT_READY


def test_prepare_fails_config_error(tmp_path):
    """A broken operations config MUST fail closed — no SiteChangeSet created."""
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet

    previews_root = tmp_path / "previews"
    config = _make_config(tmp_path, previews_root=str(previews_root))
    svc = _make_mock_svc(config, pages_result=_make_build_result(ok=True))

    with patch("cauldron_site_astro.publication_service.get_build_service", return_value=svc), \
         patch("cauldron_site_astro.publication_service._get_require_approval",
               side_effect=Exception("config broken")):
        result = SiteChangeSetService().prepare(
            actor=_actor(),
            content_request_ids=["r1"],
        )

    assert result.ok is False
    assert (
        "configuration" in result.message.lower()
        or "config" in result.message.lower()
    )
    assert SiteChangeSet.objects.count() == 0


# --- publish() production-path -------------------------------------------


def test_publish_fails_config_error(tmp_path):
    """A broken operations config during publish MUST fail closed."""
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet

    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        content_request_ids=[],
    )
    config = _make_config(tmp_path)
    svc = _make_mock_svc(config, pages_result=_make_build_result(ok=True))

    with patch("cauldron_site_astro.publication_service.get_build_service", return_value=svc), \
         patch("cauldron_site_astro.publication_service._get_require_approval",
               side_effect=Exception("config broken")):
        result = SiteChangeSetService().publish(
            actor=_actor(),
            change_set_id=str(cs.id),
        )

    assert result.ok is False
    cs.refresh_from_db()
    assert cs.status == SiteChangeSet.PUBLISH_FAILED


# --- _fetch_eligible_change_requests direct tests -------------------------


def test_fetch_eligible_uses_real_records(tmp_path):
    from cauldron_site_astro.publication_service import (
        _fetch_eligible_change_requests, _publishable_states,
    )

    _make_ccr(request_id="prop-fetch", lifecycle_state="proposed")

    loaded, err = _fetch_eligible_change_requests(
        ["prop-fetch"], _publishable_states(False), require_approval=False,
    )
    assert err is None
    assert "prop-fetch" in loaded
    assert loaded["prop-fetch"].lifecycle_state == "proposed"


def test_fetch_eligible_rejects_terminal_state(tmp_path):
    from cauldron_site_astro.publication_service import (
        _fetch_eligible_change_requests, _publishable_states,
    )

    _make_ccr(request_id="terminal-fetch", lifecycle_state="applied")

    loaded, err = _fetch_eligible_change_requests(
        ["terminal-fetch"], _publishable_states(False), require_approval=False,
    )
    assert err is not None
    assert loaded == {}


# --- Zero-applied compensation semantics (Item 2) -------------------------


def test_publish_zero_applied_not_compensated(tmp_path):
    """First apply returns ok=False APPLY_FAILED — nothing was applied.

    compensated must be False (no CRs to compensate), and requires_reconciliation
    must be False (APPLY_FAILED is a retryable state).
    """
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet

    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        content_request_ids=["req-z"],
    )
    config = _make_config(tmp_path)
    svc = _make_mock_svc(config, pages_result=_make_build_result(ok=True, pages_built=1))

    fake_content_svc = MagicMock()
    fake_content_svc.validate_change_request.return_value = MagicMock(ok=True, request_version=1)
    fake_content_svc.apply_change_request.return_value = MagicMock(
        ok=False,
        lifecycle_state="apply_failed",
        error=MagicMock(message="apply blew up"),
    )

    _mock_fetch = lambda ids, allowed_states, require_approval: (
        {i: SimpleNamespace(lifecycle_state="proposed", request_version=1) for i in ids},
        None,
    )

    with patch("cauldron_site_astro.publication_service.get_build_service", return_value=svc), \
         patch("cauldron_site_astro.publication_service._get_content_operation_service",
               return_value=fake_content_svc), \
         patch("cauldron_site_astro.publication_service._fetch_eligible_change_requests",
               side_effect=_mock_fetch):
        result = SiteChangeSetService().publish(
            actor=_actor(),
            change_set_id=str(cs.id),
        )

    assert result.ok is False
    cs.refresh_from_db()
    pbr = cs.publish_build_result or {}
    # Nothing was applied — compensated MUST be False (not vacuously True).
    assert pbr.get("compensated") is False
    # APPLY_FAILED is retryable — no reconciliation required.
    assert pbr.get("requires_reconciliation") is False

    inspect_result = SiteChangeSetService().inspect(str(cs.id))
    assert inspect_result.retryable is True


def test_publish_first_apply_failed_is_retryable(tmp_path):
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet

    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        content_request_ids=["only-req"],
    )
    config = _make_config(tmp_path)
    svc = _make_mock_svc(config, pages_result=_make_build_result(ok=True))

    fake_content_svc = MagicMock()
    fake_content_svc.validate_change_request.return_value = MagicMock(ok=True, request_version=1)
    fake_content_svc.apply_change_request.return_value = MagicMock(
        ok=False,
        lifecycle_state="apply_failed",
        error=MagicMock(message="failed"),
    )

    _mock_fetch = lambda ids, allowed_states, require_approval: (
        {i: SimpleNamespace(lifecycle_state="proposed", request_version=1) for i in ids},
        None,
    )

    with patch("cauldron_site_astro.publication_service.get_build_service", return_value=svc), \
         patch("cauldron_site_astro.publication_service._get_content_operation_service",
               return_value=fake_content_svc), \
         patch("cauldron_site_astro.publication_service._fetch_eligible_change_requests",
               side_effect=_mock_fetch):
        SiteChangeSetService().publish(actor=_actor(), change_set_id=str(cs.id))

    inspect_result = SiteChangeSetService().inspect(str(cs.id))
    assert inspect_result.retryable is True
    assert inspect_result.publish_build_result.get("compensated") is False


def test_publish_first_reconciliation_required_not_retryable(tmp_path):
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet

    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        content_request_ids=["rec-req"],
    )
    config = _make_config(tmp_path)
    svc = _make_mock_svc(config, pages_result=_make_build_result(ok=True))
    svc.promote_output_with_backup.return_value = "snap-x"

    fake_content_svc = MagicMock()
    fake_content_svc.validate_change_request.return_value = MagicMock(ok=True, request_version=1)
    fake_content_svc.apply_change_request.return_value = MagicMock(
        ok=False,
        lifecycle_state="reconciliation_required",
        error=MagicMock(message="workspace persist failed"),
    )

    _mock_fetch = lambda ids, allowed_states, require_approval: (
        {i: SimpleNamespace(lifecycle_state="proposed", request_version=1) for i in ids},
        None,
    )

    with patch("cauldron_site_astro.publication_service.get_build_service", return_value=svc), \
         patch("cauldron_site_astro.publication_service._get_content_operation_service",
               return_value=fake_content_svc), \
         patch("cauldron_site_astro.publication_service._fetch_eligible_change_requests",
               side_effect=_mock_fetch):
        SiteChangeSetService().publish(actor=_actor(), change_set_id=str(cs.id))

    cs.refresh_from_db()
    pbr = cs.publish_build_result or {}
    assert pbr.get("requires_reconciliation") is True

    inspect_result = SiteChangeSetService().inspect(str(cs.id))
    assert inspect_result.retryable is False


# --- Item 3: RECONCILIATION_REQUIRED preserves FS state -------------------


def test_publish_reconciliation_required_response_marks_requires_reconciliation(tmp_path):
    """RECONCILIATION_REQUIRED lifecycle from apply → do NOT restore FS."""
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet

    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        content_request_ids=["rec-req-2"],
    )
    config = _make_config(tmp_path)
    svc = _make_mock_svc(config, pages_result=_make_build_result(ok=True))
    svc.promote_output_with_backup.return_value = "snap-y"

    fake_content_svc = MagicMock()
    fake_content_svc.validate_change_request.return_value = MagicMock(ok=True, request_version=1)
    fake_content_svc.apply_change_request.return_value = MagicMock(
        ok=False,
        lifecycle_state="reconciliation_required",
        error=MagicMock(message="persist failed"),
    )

    _mock_fetch = lambda ids, allowed_states, require_approval: (
        {i: SimpleNamespace(lifecycle_state="proposed", request_version=1) for i in ids},
        None,
    )

    with patch("cauldron_site_astro.publication_service.get_build_service", return_value=svc), \
         patch("cauldron_site_astro.publication_service._get_content_operation_service",
               return_value=fake_content_svc), \
         patch("cauldron_site_astro.publication_service._fetch_eligible_change_requests",
               side_effect=_mock_fetch):
        SiteChangeSetService().publish(actor=_actor(), change_set_id=str(cs.id))

    cs.refresh_from_db()
    pbr = cs.publish_build_result or {}
    assert pbr.get("requires_reconciliation") is True
    # FS state preserved — restore_output was NOT called because canonical is uncertain.
    svc.restore_output.assert_not_called()


# --- Item 4: independent FS restoration tracking --------------------------


def test_publish_output_restore_failure_marks_requires_reconciliation(tmp_path):
    """Compensation succeeded but restore_output raised → requires_reconciliation."""
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet

    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        content_request_ids=["r-A", "r-B"],
    )
    config = _make_config(tmp_path)
    svc = _make_mock_svc(config, pages_result=_make_build_result(ok=True))
    svc.promote_output_with_backup.return_value = "snap-fail"
    svc.restore_output.side_effect = Exception("restore blew up")

    fake_content_svc = MagicMock()
    fake_content_svc.validate_change_request.return_value = MagicMock(ok=True, request_version=1)

    call_count = {"n": 0}

    def apply_side_effect(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return MagicMock(ok=True, request_version=2, lifecycle_state="applied")
        return MagicMock(
            ok=False, lifecycle_state="apply_failed",
            error=MagicMock(message="B failed"),
        )

    fake_content_svc.apply_change_request.side_effect = apply_side_effect
    fake_content_svc.compensate_for_publication_failure.return_value = MagicMock(
        ok=True, verified=True, lifecycle_state="rolled_back",
        error_code="", error_message="",
    )

    _mock_fetch = lambda ids, allowed_states, require_approval: (
        {i: SimpleNamespace(lifecycle_state="proposed", request_version=1) for i in ids},
        None,
    )

    with patch("cauldron_site_astro.publication_service.get_build_service", return_value=svc), \
         patch("cauldron_site_astro.publication_service._get_content_operation_service",
               return_value=fake_content_svc), \
         patch("cauldron_site_astro.publication_service._fetch_eligible_change_requests",
               side_effect=_mock_fetch):
        SiteChangeSetService().publish(actor=_actor(), change_set_id=str(cs.id))

    cs.refresh_from_db()
    pbr = cs.publish_build_result or {}
    assert pbr.get("requires_reconciliation") is True
    assert pbr.get("compensated") is False
    # Backup MUST be retained — discard_output_backup should NOT be called.
    svc.discard_output_backup.assert_not_called()


def test_publish_theme_restore_failure_marks_requires_reconciliation(tmp_path):
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_site_astro.theme import SiteThemeService

    theme_dir = tmp_path / "theme"
    theme_dir.mkdir()
    SiteThemeService(theme_dir).set_active_css("body { color: original; }")

    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        content_request_ids=["r-A", "r-B"],
        staged_theme_css="body { color: staged; }",
    )
    config = _make_config(tmp_path, theme_root=str(theme_dir))
    svc = _make_mock_svc(config, pages_result=_make_build_result(ok=True))
    svc.promote_output_with_backup.return_value = "snap-css"

    fake_content_svc = MagicMock()
    fake_content_svc.validate_change_request.return_value = MagicMock(ok=True, request_version=1)

    call_count = {"n": 0}

    def apply_side_effect(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return MagicMock(ok=True, request_version=2, lifecycle_state="applied")
        return MagicMock(
            ok=False, lifecycle_state="apply_failed",
            error=MagicMock(message="B failed"),
        )

    fake_content_svc.apply_change_request.side_effect = apply_side_effect
    fake_content_svc.compensate_for_publication_failure.return_value = MagicMock(
        ok=True, verified=True, lifecycle_state="rolled_back",
        error_code="", error_message="",
    )

    _mock_fetch = lambda ids, allowed_states, require_approval: (
        {i: SimpleNamespace(lifecycle_state="proposed", request_version=1) for i in ids},
        None,
    )

    # Patch theme service's set_active_css to raise on RESTORE.
    def bad_set_active_css(self, css):
        raise Exception("css restore blew up")

    with patch.object(SiteThemeService, "set_active_css", bad_set_active_css), \
         patch("cauldron_site_astro.publication_service.get_build_service", return_value=svc), \
         patch("cauldron_site_astro.publication_service._get_content_operation_service",
               return_value=fake_content_svc), \
         patch("cauldron_site_astro.publication_service._fetch_eligible_change_requests",
               side_effect=_mock_fetch):
        SiteChangeSetService().publish(actor=_actor(), change_set_id=str(cs.id))

    cs.refresh_from_db()
    pbr = cs.publish_build_result or {}
    assert pbr.get("requires_reconciliation") is True
    assert pbr.get("compensated") is False


def test_publish_backup_retained_when_output_restore_fails(tmp_path):
    """Item 4: discard_output_backup NOT called when restore_output raises."""
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet

    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        content_request_ids=["r-A", "r-B"],
    )
    config = _make_config(tmp_path)
    svc = _make_mock_svc(config, pages_result=_make_build_result(ok=True))
    svc.promote_output_with_backup.return_value = "snap-XX"
    svc.restore_output.side_effect = Exception("boom")

    fake_content_svc = MagicMock()
    fake_content_svc.validate_change_request.return_value = MagicMock(ok=True, request_version=1)

    call_count = {"n": 0}

    def apply_side_effect(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return MagicMock(ok=True, request_version=2, lifecycle_state="applied")
        return MagicMock(
            ok=False, lifecycle_state="apply_failed",
            error=MagicMock(message="B failed"),
        )

    fake_content_svc.apply_change_request.side_effect = apply_side_effect
    fake_content_svc.compensate_for_publication_failure.return_value = MagicMock(
        ok=True, verified=True, lifecycle_state="rolled_back",
        error_code="", error_message="",
    )

    _mock_fetch = lambda ids, allowed_states, require_approval: (
        {i: SimpleNamespace(lifecycle_state="proposed", request_version=1) for i in ids},
        None,
    )

    with patch("cauldron_site_astro.publication_service.get_build_service", return_value=svc), \
         patch("cauldron_site_astro.publication_service._get_content_operation_service",
               return_value=fake_content_svc), \
         patch("cauldron_site_astro.publication_service._fetch_eligible_change_requests",
               side_effect=_mock_fetch):
        SiteChangeSetService().publish(actor=_actor(), change_set_id=str(cs.id))

    # discard_output_backup MUST NOT be called — the backup must be retained.
    svc.discard_output_backup.assert_not_called()


# ---------------------------------------------------------------------------
# Item 1: Step 5 (theme promotion failure) restoration discipline
# ---------------------------------------------------------------------------


def _make_draft_ready_cs(tmp_path, staged_theme_css="body { color: staged; }"):
    """Build a DRAFT_READY SiteChangeSet with staged CSS and a theme root."""
    from cauldron_site_astro.models import SiteChangeSet
    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        content_request_ids=[],
        staged_theme_css=staged_theme_css,
    )
    return cs


def _make_step5_svc(tmp_path, theme_root):
    """Build a mock build service configured for Step 5 tests."""
    config = _make_config(tmp_path, theme_root=str(theme_root))
    svc = _make_mock_svc(config, pages_result=_make_build_result(ok=True, pages_built=1))
    svc.promote_output_with_backup.return_value = "snap-step5"
    return svc


def test_publish_theme_promo_fails_output_restore_succeeds(tmp_path, bypass_db_integrity):
    """Theme promotion raises; restore_output succeeds → requires_reconciliation False."""
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_site_astro.theme import SiteThemeService

    theme_dir = tmp_path / "theme"
    theme_dir.mkdir()
    SiteThemeService(theme_dir).set_active_css("body { color: original; }")

    cs = _make_draft_ready_cs(tmp_path)
    svc = _make_step5_svc(tmp_path, theme_dir)
    svc.restore_output.return_value = None  # succeeds

    # stage_css raises → triggers the except path.
    def bad_stage_css(self, css):
        raise Exception("stage broke")

    with patch.object(SiteThemeService, "stage_css", bad_stage_css), \
         patch("cauldron_site_astro.publication_service.get_build_service", return_value=svc):
        result = SiteChangeSetService().publish(
            actor=_actor(),
            change_set_id=str(cs.id),
        )

    assert result.ok is False
    assert result.status == SiteChangeSet.PUBLISH_FAILED
    cs.refresh_from_db()
    pbr = cs.publish_build_result or {}
    assert "theme promotion failed" in pbr.get("error", "")
    assert pbr.get("requires_reconciliation") is False
    svc.restore_output.assert_called_once()


def test_publish_theme_promo_fails_output_restore_also_fails(tmp_path, bypass_db_integrity):
    """Theme promotion raises AND restore_output raises → requires_reconciliation True."""
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_site_astro.theme import SiteThemeService

    theme_dir = tmp_path / "theme"
    theme_dir.mkdir()
    SiteThemeService(theme_dir).set_active_css("body { color: original; }")

    cs = _make_draft_ready_cs(tmp_path)
    svc = _make_step5_svc(tmp_path, theme_dir)
    svc.restore_output.side_effect = Exception("restore broke")

    def bad_promote_staged(self):
        raise Exception("promote broke")

    with patch.object(SiteThemeService, "promote_staged", bad_promote_staged), \
         patch("cauldron_site_astro.publication_service.get_build_service", return_value=svc):
        result = SiteChangeSetService().publish(
            actor=_actor(),
            change_set_id=str(cs.id),
        )

    assert result.ok is False
    cs.refresh_from_db()
    pbr = cs.publish_build_result or {}
    assert pbr.get("requires_reconciliation") is True
    assert pbr.get("output_restored") is False
    assert "restore broke" in pbr.get("output_restore_error", "")
    # Backup MUST be retained.
    svc.discard_output_backup.assert_not_called()


def test_publish_theme_promo_fails_backup_retained_when_restore_fails(tmp_path, bypass_db_integrity):
    """Explicitly verify discard_output_backup is never called when restore fails."""
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.theme import SiteThemeService

    theme_dir = tmp_path / "theme"
    theme_dir.mkdir()
    SiteThemeService(theme_dir).set_active_css("body { color: original; }")

    cs = _make_draft_ready_cs(tmp_path)
    svc = _make_step5_svc(tmp_path, theme_dir)
    svc.restore_output.side_effect = Exception("restore broke")

    def bad_promote_staged(self):
        raise Exception("promote broke")

    with patch.object(SiteThemeService, "promote_staged", bad_promote_staged), \
         patch("cauldron_site_astro.publication_service.get_build_service", return_value=svc):
        SiteChangeSetService().publish(
            actor=_actor(),
            change_set_id=str(cs.id),
        )

    svc.discard_output_backup.assert_not_called()


def test_publish_theme_promo_fails_css_restored_to_prev_state(tmp_path, bypass_db_integrity):
    """After promote_staged raises, set_active_css(prev_active_css) MUST be called."""
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.theme import SiteThemeService

    theme_dir = tmp_path / "theme"
    theme_dir.mkdir()
    SiteThemeService(theme_dir).set_active_css("body { color: original; }")

    cs = _make_draft_ready_cs(tmp_path)
    svc = _make_step5_svc(tmp_path, theme_dir)
    svc.restore_output.return_value = None  # succeeds

    # SiteThemeService is imported locally inside publish() from
    # cauldron_site_astro.theme; patch it at the source module.
    theme_mock = MagicMock()
    theme_mock.get_active_css.return_value = "body { color: original; }"
    theme_mock.promote_staged.side_effect = Exception("promote broke")

    with patch(
        "cauldron_site_astro.theme.SiteThemeService",
        return_value=theme_mock,
    ), patch("cauldron_site_astro.publication_service.get_build_service", return_value=svc):
        result = SiteChangeSetService().publish(
            actor=_actor(),
            change_set_id=str(cs.id),
        )

    assert result.ok is False
    # Verify set_active_css was called with the previous CSS.
    theme_mock.set_active_css.assert_called_once_with("body { color: original; }")
    cs.refresh_from_db()
    pbr = cs.publish_build_result or {}
    # Both output and CSS restore succeeded → requires_reconciliation False.
    assert pbr.get("requires_reconciliation") is False


def test_publish_theme_promo_fails_css_restore_also_fails(tmp_path, bypass_db_integrity):
    """promote_staged AND set_active_css both raise → css_restored=False."""
    from cauldron_site_astro.publication_service import SiteChangeSetService

    theme_dir = tmp_path / "theme"
    theme_dir.mkdir()

    cs = _make_draft_ready_cs(tmp_path)
    svc = _make_step5_svc(tmp_path, theme_dir)
    svc.restore_output.return_value = None  # succeeds

    theme_mock = MagicMock()
    theme_mock.get_active_css.return_value = "body { color: original; }"
    theme_mock.promote_staged.side_effect = Exception("promote broke")
    theme_mock.set_active_css.side_effect = Exception("css restore broke")

    with patch(
        "cauldron_site_astro.theme.SiteThemeService",
        return_value=theme_mock,
    ), patch("cauldron_site_astro.publication_service.get_build_service", return_value=svc):
        SiteChangeSetService().publish(
            actor=_actor(),
            change_set_id=str(cs.id),
        )

    cs.refresh_from_db()
    pbr = cs.publish_build_result or {}
    assert pbr.get("css_restored") is False
    assert "css restore broke" in pbr.get("css_restore_error", "")
    assert pbr.get("requires_reconciliation") is True


# ---------------------------------------------------------------------------
# Item 2: ContentOperationService factory ownership proof
# ---------------------------------------------------------------------------


def test_get_content_operation_service_uses_content_operations_factory(tmp_path):
    """Site Astro must obtain ContentOperationService via cauldron_content_operations,
    not via optional cauldron_admin_content.
    """
    from cauldron_site_astro.publication_service import _get_content_operation_service
    # Ensure the service_factory submodule is loaded so ``patch`` can resolve it.
    import cauldron_content_operations.service_factory  # noqa: F401

    fake_service = MagicMock(name="fake-content-operation-service")

    with patch(
        "cauldron_content_operations.service_factory.get_service",
        return_value=fake_service,
    ):
        result = _get_content_operation_service()

    assert result is fake_service


# ---------------------------------------------------------------------------
# Delete-thread regression test: prepare() passes deleted_item_ids as
# excluded_item_ids to build_preview.
# ---------------------------------------------------------------------------


def test_prepare_passes_deleted_ids_as_excluded_to_build(tmp_path, bypass_db_integrity):
    """A delete operation must flow through prepare() → build_preview.excluded_item_ids."""
    from cauldron_site_astro.publication_service import SiteChangeSetService

    previews_root = tmp_path / "previews"
    config = _make_config(tmp_path, previews_root=str(previews_root))
    svc = _make_mock_svc(config, pages_result=_make_build_result(ok=True, pages_built=1))

    # Patch _extract_draft_items so we can control the (ids, extras, deleted) triple.
    with patch(
        "cauldron_site_astro.publication_service._extract_draft_items",
        return_value=(["item-1"], [], ["item-1"]),
    ), patch(
        "cauldron_site_astro.publication_service.get_build_service", return_value=svc,
    ):
        result = SiteChangeSetService().prepare(
            actor=_actor(),
            content_request_ids=["r-del"],
        )

    assert result.ok is True, result.message
    # build_preview must have been called with excluded_item_ids=["item-1"].
    call_kwargs = svc.build_preview.call_args.kwargs
    assert call_kwargs.get("excluded_item_ids") == ["item-1"]


# ---------------------------------------------------------------------------
# _extract_draft_items projection fix: draft-status ops → excluded_item_ids
# ---------------------------------------------------------------------------


def _fake_cr_and_service(ws_id, ops):
    """Return (fake_cr, fake_service) for workspace-based _extract_draft_items tests."""
    from types import SimpleNamespace
    fake_changeset = SimpleNamespace(operations=ops)
    fake_workspace = MagicMock()
    fake_workspace.load_changeset.return_value = fake_changeset
    fake_cr = SimpleNamespace(workspace_changeset_id=ws_id)
    fake_service = MagicMock()
    fake_service._workspace = fake_workspace
    return fake_cr, fake_service


def _op(kind, item_id, slug, status=None):
    """Build a SimpleNamespace operation shaped like a persisted ContentOperation.

    status is a top-level field (where build_page_operation() stores it), NOT
    inside data. op.data contains page metadata only.
    """
    from types import SimpleNamespace
    return SimpleNamespace(
        kind=kind,
        item_id=item_id,
        slug=slug,
        status=status,
        collection="pages",
        schema="page",
        body="# content",
        data={},
    )


def test_extract_draft_items_published_op_goes_to_extra_items():
    """Create/update ops with status=published must appear in extra_items."""
    from cauldron_site_astro.publication_service import _extract_draft_items
    from cauldron_content.contracts import ContentStatus

    fake_cr, fake_service = _fake_cr_and_service("ws-pub", [
        _op("create", "page-xyz", "my-page", status="published"),
    ])

    with patch("cauldron_site_astro.publication_service._get_content_operation_service", return_value=fake_service), \
         patch("cauldron_content_operations.models.ContentChangeRequest.objects.get", return_value=fake_cr):
        ids, extras, deleted = _extract_draft_items(["req-xdi-pub"])

    assert "page-xyz" in ids
    assert len(extras) == 1
    assert extras[0].id == "page-xyz"
    assert extras[0].status == ContentStatus.PUBLISHED
    assert deleted == []


def test_extract_draft_items_draft_op_goes_to_excluded():
    """Create/update ops with status=draft must be excluded from preview (like deletes)."""
    from cauldron_site_astro.publication_service import _extract_draft_items

    fake_cr, fake_service = _fake_cr_and_service("ws-draft", [
        _op("update", "live-page", "live-page", status="draft"),
    ])

    with patch("cauldron_site_astro.publication_service._get_content_operation_service", return_value=fake_service), \
         patch("cauldron_content_operations.models.ContentChangeRequest.objects.get", return_value=fake_cr):
        ids, extras, deleted = _extract_draft_items(["req-xdi-draft"])

    assert "live-page" in ids
    assert extras == []
    assert "live-page" in deleted


def test_extract_draft_items_delete_op_goes_to_excluded():
    """Delete ops must always appear in deleted_item_ids, not extra_items."""
    from cauldron_site_astro.publication_service import _extract_draft_items

    fake_cr, fake_service = _fake_cr_and_service("ws-del2", [
        _op("delete", "gone-page", "gone"),
    ])

    with patch("cauldron_site_astro.publication_service._get_content_operation_service", return_value=fake_service), \
         patch("cauldron_content_operations.models.ContentChangeRequest.objects.get", return_value=fake_cr):
        ids, extras, deleted = _extract_draft_items(["req-xdi-del"])

    assert "gone-page" in ids
    assert extras == []
    assert "gone-page" in deleted


def test_extract_draft_items_mixed_ops():
    """Mixed ops: published → extra_items, draft → excluded, delete → excluded."""
    from cauldron_site_astro.publication_service import _extract_draft_items
    from cauldron_content.contracts import ContentStatus

    fake_cr, fake_service = _fake_cr_and_service("ws-mix", [
        _op("create", "new-page", "new", status="published"),
        _op("update", "unpub-page", "unpub", status="draft"),
        _op("delete", "del-page", "del"),
    ])

    with patch("cauldron_site_astro.publication_service._get_content_operation_service", return_value=fake_service), \
         patch("cauldron_content_operations.models.ContentChangeRequest.objects.get", return_value=fake_cr):
        ids, extras, deleted = _extract_draft_items(["req-xdi-mix"])

    assert set(ids) == {"new-page", "unpub-page", "del-page"}
    assert len(extras) == 1
    assert extras[0].id == "new-page"
    assert extras[0].status == ContentStatus.PUBLISHED
    assert set(deleted) == {"unpub-page", "del-page"}


# ---------------------------------------------------------------------------
# SiteChangeSetService.find_reusable_change_set
# ---------------------------------------------------------------------------


def test_find_reusable_change_set_returns_draft_ready_id():
    """find_reusable_change_set returns the ID of a DRAFT_READY change set."""
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet

    req_id = "find-cs-1"
    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        content_request_ids=[req_id],
    )

    result = SiteChangeSetService().find_reusable_change_set(req_id)
    assert result == str(cs.id)


def test_find_reusable_change_set_returns_publish_failed_id():
    """find_reusable_change_set returns the ID of a PUBLISH_FAILED change set."""
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet

    req_id = "find-cs-2"
    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.PUBLISH_FAILED,
        content_request_ids=[req_id],
    )

    result = SiteChangeSetService().find_reusable_change_set(req_id)
    assert result == str(cs.id)


def test_find_reusable_change_set_ignores_published_change_set():
    """A PUBLISHED change set is not reusable."""
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet

    req_id = "find-cs-3"
    SiteChangeSet.objects.create(
        status=SiteChangeSet.PUBLISHED,
        content_request_ids=[req_id],
    )

    result = SiteChangeSetService().find_reusable_change_set(req_id)
    assert result is None


def test_find_reusable_change_set_returns_none_when_not_found():
    """Returns None when no matching change set exists."""
    from cauldron_site_astro.publication_service import SiteChangeSetService

    result = SiteChangeSetService().find_reusable_change_set("no-such-request")
    assert result is None


def test_find_reusable_change_set_returns_newest_first():
    """Returns the most recently created reusable change set."""
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.models import SiteChangeSet

    req_id = "find-cs-order"
    older = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        content_request_ids=[req_id],
    )
    newer = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        content_request_ids=[req_id],
    )

    result = SiteChangeSetService().find_reusable_change_set(req_id)
    assert result == str(newer.id)


# ---------------------------------------------------------------------------
# Production-contract tests: op.status is the authoritative status source
# ---------------------------------------------------------------------------
#
# These tests use operations shaped like actual persisted ContentOperation
# objects: status is a top-level field (op.status), op.data contains page
# metadata only. The prior fake _op() stored status in op.data which masked
# the defect in _extract_draft_items().


def _prod_op(kind, item_id, slug, status):
    """Persisted-operation shape: status at top level, data has no status key."""
    from types import SimpleNamespace
    return SimpleNamespace(
        kind=kind,
        item_id=item_id,
        slug=slug,
        status=status,
        collection="pages",
        schema="page",
        body="# body",
        data={"title": "Test Page"},  # metadata only — no status
    )


def _fake_cr_service_for_prod(ws_id, ops):
    from types import SimpleNamespace
    fake_changeset = SimpleNamespace(operations=ops)
    fake_workspace = MagicMock()
    fake_workspace.load_changeset.return_value = fake_changeset
    fake_cr = SimpleNamespace(workspace_changeset_id=ws_id)
    fake_service = MagicMock()
    fake_service._workspace = fake_workspace
    return fake_cr, fake_service


def test_extract_draft_items_persisted_draft_status_is_excluded():
    """ContentStatus.DRAFT at op.status (enum member) excludes the item."""
    from cauldron_content.contracts import ContentStatus
    from cauldron_site_astro.publication_service import _extract_draft_items

    fake_cr, fake_service = _fake_cr_service_for_prod("ws-prod-draft", [
        _prod_op("update", "live-page", "live-page", ContentStatus.DRAFT),
    ])

    with patch("cauldron_site_astro.publication_service._get_content_operation_service",
               return_value=fake_service), \
         patch("cauldron_content_operations.models.ContentChangeRequest.objects.get",
               return_value=fake_cr):
        ids, extras, deleted = _extract_draft_items(["req-prod-draft"])

    assert "live-page" in ids
    assert extras == []
    assert "live-page" in deleted


def test_extract_draft_items_persisted_published_status_is_injected():
    """ContentStatus.PUBLISHED at op.status (enum member) injects into extra_items."""
    from cauldron_content.contracts import ContentStatus
    from cauldron_site_astro.publication_service import _extract_draft_items

    fake_cr, fake_service = _fake_cr_service_for_prod("ws-prod-pub", [
        _prod_op("update", "about-page", "about", ContentStatus.PUBLISHED),
    ])

    with patch("cauldron_site_astro.publication_service._get_content_operation_service",
               return_value=fake_service), \
         patch("cauldron_content_operations.models.ContentChangeRequest.objects.get",
               return_value=fake_cr):
        ids, extras, deleted = _extract_draft_items(["req-prod-pub"])

    assert "about-page" in ids
    assert len(extras) == 1
    assert extras[0].status == ContentStatus.PUBLISHED
    assert deleted == []


def test_extract_draft_items_raw_string_draft_is_excluded():
    """Raw string "draft" at op.status is correctly normalised and excluded."""
    from cauldron_site_astro.publication_service import _extract_draft_items

    fake_cr, fake_service = _fake_cr_service_for_prod("ws-raw-draft", [
        _prod_op("update", "raw-draft-page", "raw-draft", "draft"),
    ])

    with patch("cauldron_site_astro.publication_service._get_content_operation_service",
               return_value=fake_service), \
         patch("cauldron_content_operations.models.ContentChangeRequest.objects.get",
               return_value=fake_cr):
        ids, extras, deleted = _extract_draft_items(["req-raw-draft"])

    assert "raw-draft-page" in deleted
    assert extras == []


def test_extract_draft_items_raw_string_published_is_injected():
    """Raw string "published" at op.status is correctly normalised and injected."""
    from cauldron_content.contracts import ContentStatus
    from cauldron_site_astro.publication_service import _extract_draft_items

    fake_cr, fake_service = _fake_cr_service_for_prod("ws-raw-pub", [
        _prod_op("create", "raw-pub-page", "raw-pub", "published"),
    ])

    with patch("cauldron_site_astro.publication_service._get_content_operation_service",
               return_value=fake_service), \
         patch("cauldron_content_operations.models.ContentChangeRequest.objects.get",
               return_value=fake_cr):
        ids, extras, deleted = _extract_draft_items(["req-raw-pub"])

    assert len(extras) == 1
    assert extras[0].status == ContentStatus.PUBLISHED
    assert deleted == []


def test_extract_draft_items_malformed_status_not_published():
    """An unknown or malformed status does not become published implicitly."""
    from cauldron_site_astro.publication_service import _extract_draft_items

    fake_cr, fake_service = _fake_cr_service_for_prod("ws-bad-status", [
        _prod_op("create", "bad-status-page", "bad-status", "garbage"),
    ])

    with patch("cauldron_site_astro.publication_service._get_content_operation_service",
               return_value=fake_service), \
         patch("cauldron_content_operations.models.ContentChangeRequest.objects.get",
               return_value=fake_cr):
        ids, extras, deleted = _extract_draft_items(["req-bad-status"])

    # Item ID is still tracked (added before status check), but the op must
    # NOT appear in extra_items and must NOT appear in deleted_item_ids.
    assert extras == []
    assert "bad-status-page" not in deleted


def test_prepare_unpublish_excludes_live_page_from_preview(tmp_path, bypass_db_integrity):
    """Unpublish path: prepare() with a draft-status op passes excluded_item_ids to build_preview.

    Verifies the full SiteChangeSetService.prepare() call with a draft operation
    produces a preview build that excludes the live page (excluded_item_ids set).
    """
    from cauldron_content.contracts import ContentStatus
    from cauldron_site_astro.publication_service import SiteChangeSetService

    previews_root = tmp_path / "previews"
    config = _make_config(tmp_path, previews_root=str(previews_root))
    svc = _make_mock_svc(config, pages_result=_make_build_result(ok=True, pages_built=0))

    # bypass_db_integrity patches _check_request_integrity and
    # _fetch_eligible_change_requests, but NOT _extract_draft_items.
    # Patch _extract_draft_items to return the unpublish projection: item in
    # deleted_item_ids, nothing in extra_items.
    with patch("cauldron_site_astro.publication_service.get_build_service", return_value=svc), \
         patch(
             "cauldron_site_astro.publication_service._extract_draft_items",
             return_value=(["live-page-id"], [], ["live-page-id"]),
         ):
        result = SiteChangeSetService().prepare(
            actor=_actor(),
            content_request_ids=["req-unpub"],
        )

    assert result.ok is True, result.message
    call_kwargs = svc.build_preview.call_args.kwargs
    assert "live-page-id" in (call_kwargs.get("excluded_item_ids") or [])
    assert not (call_kwargs.get("extra_items") or [])
