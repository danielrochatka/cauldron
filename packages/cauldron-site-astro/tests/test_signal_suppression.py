"""Tests for the canonical_content_changed signal-suppression flag.

During a controlled SiteChangeSet publish the publication service performs
its own scoped build and promotion, so the ``canonical_content_changed``
signal fired by each ``apply_change_request`` call must NOT dispatch a
second build. Outside of that publish, the same signal must continue to
dispatch a build as before.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


pytestmark = pytest.mark.django_db


def test_signal_dispatches_build_when_not_suppressed():
    """Without the suppression flag active, a build IS dispatched."""
    from cauldron_site_astro.apps import _handle_content_changed, _suppress_rebuild

    # Ensure flag is clear.
    _suppress_rebuild.active = False

    fake_dispatcher = MagicMock()
    with patch(
        "cauldron_site_astro.dispatcher.get_dispatcher",
        return_value=fake_dispatcher,
    ):
        _handle_content_changed(
            sender=None,
            change_type="apply",
            change_id="cr-x",
            provider_name="flatfile",
            changed_by=None,
        )

    fake_dispatcher.dispatch.assert_called_once()


def test_signal_suppressed_when_flag_active():
    """With the suppression flag active on this thread, dispatch is skipped."""
    from cauldron_site_astro.apps import _handle_content_changed, _suppress_rebuild

    _suppress_rebuild.active = True
    try:
        fake_dispatcher = MagicMock()
        with patch(
            "cauldron_site_astro.dispatcher.get_dispatcher",
            return_value=fake_dispatcher,
        ):
            _handle_content_changed(
                sender=None,
                change_type="apply",
                change_id="cr-y",
                provider_name="flatfile",
                changed_by=None,
            )
        fake_dispatcher.dispatch.assert_not_called()
    finally:
        _suppress_rebuild.active = False


def test_publish_sets_suppression_flag_during_content_apply(tmp_path):
    """SiteChangeSetService.publish() sets _suppress_rebuild.active during step 6.

    The flag must be True when apply_change_request runs and False after the
    publish returns (both success and failure paths clear it).
    """
    from cauldron_site_astro.apps import _suppress_rebuild
    from cauldron_site_astro.config import SiteAstroConfig
    from cauldron_site_astro.models import SiteChangeSet
    from cauldron_site_astro.publication_service import SiteChangeSetService
    from cauldron_site_astro.service import BuildResult

    _suppress_rebuild.active = False

    frontend = tmp_path / "frontend"
    frontend.mkdir()
    output = tmp_path / "output"
    config = SiteAstroConfig(
        frontend_root=str(frontend),
        output_root=str(output),
        npm_command="npm",
        build_timeout=30,
        theme_root="",
        previews_root="",
    )
    svc = MagicMock()
    svc._config = config
    svc.build_preview.return_value = BuildResult(ok=True, pages_built=1, output_dir="/tmp")

    cs = SiteChangeSet.objects.create(
        status=SiteChangeSet.DRAFT_READY,
        content_request_ids=["req-signal"],
    )

    observed_state = {}

    def capture_apply(*args, **kwargs):
        observed_state["flag_during_apply"] = getattr(_suppress_rebuild, "active", False)
        return MagicMock(ok=True)

    fake_content_svc = MagicMock()
    fake_content_svc.validate_change_request.return_value = MagicMock(ok=True, request_version=1)
    fake_content_svc.apply_change_request.side_effect = capture_apply

    from django.contrib.auth import get_user_model
    User = get_user_model()
    actor, _ = User.objects.get_or_create(
        username="signal-test-actor",
        defaults={"is_superuser": True, "is_staff": True},
    )
    if not actor.is_superuser:
        actor.is_superuser = True
        actor.save(update_fields=["is_superuser"])

    with patch("cauldron_site_astro.publication_service.get_build_service", return_value=svc):
        with patch(
            "cauldron_site_astro.publication_service._get_content_operation_service",
            return_value=fake_content_svc,
        ):
            result = SiteChangeSetService().publish(actor=actor, change_set_id=str(cs.id))

    assert result.ok is True, result.message
    assert observed_state.get("flag_during_apply") is True, (
        "Suppression flag must be active while apply_change_request runs."
    )
    # Flag must be cleared after publish returns.
    assert getattr(_suppress_rebuild, "active", False) is False
