"""Optimistic concurrency for AdminAIRun finalization."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db

from cauldron_ai.contracts import AIModelResponse
from cauldron_ai.testing import FakeAIModelProvider
from cauldron_ai_admin.models import (
    AdminAIRun,
    ConcurrentModificationError,
)
from cauldron_ai_admin.service import AdminAIService
from cauldron_ai_admin.tools import AdminAIToolRegistry


def _make_user():
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    User = get_user_model()
    user, _ = User.objects.get_or_create(username="occtest")
    perm = Permission.objects.get(
        codename="use_admin_ai",
        content_type__app_label="cauldron_ai_admin",
    )
    user.user_permissions.add(perm)
    return User.objects.get(pk=user.pk)


def test_concurrent_finalize_raises_concurrent_modification():
    """When a run's version has advanced between load and update the
    finalize must raise ``ConcurrentModificationError``."""
    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="r1", content="ok", stop_reason="end_turn",
    ))
    svc = AdminAIService(
        provider=fake, tool_registry=AdminAIToolRegistry(),
    )
    user = _make_user()
    run = svc.run(user, "Hi.")
    assert run.status == "completed"

    # Advance the row under our feet, then try another finalize.
    AdminAIRun.objects.filter(pk=run.pk).update(version=run.version + 5)
    with pytest.raises(ConcurrentModificationError):
        svc._compare_and_finalize(
            run,
            new_status="failed",
            final_response=None,
            error_code="test",
            error_summary="raced",
        )


def test_successful_finalize_advances_version():
    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="r1", content="ok", stop_reason="end_turn",
    ))
    svc = AdminAIService(
        provider=fake, tool_registry=AdminAIToolRegistry(),
    )
    user = _make_user()
    run = svc.run(user, "Hi.")
    assert run.version >= 2  # created(1) → running → completed advances
