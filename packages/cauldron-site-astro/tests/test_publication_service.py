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


def test_prepare_creates_draft_ready_on_happy_path(tmp_path):
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


def test_prepare_marks_preview_failed_on_build_failure(tmp_path):
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
