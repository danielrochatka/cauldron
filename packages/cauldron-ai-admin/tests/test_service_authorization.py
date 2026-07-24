"""Direct-service authorization checks.

The service must refuse ``run()`` for an actor missing
``use_admin_ai`` — no ``AdminAIRun`` should be created.
"""
from __future__ import annotations

import pytest
from django.core.exceptions import PermissionDenied

pytestmark = pytest.mark.django_db

from cauldron_ai.testing import FakeAIModelProvider
from cauldron_ai_admin.models import AdminAIRun
from cauldron_ai_admin.service import AdminAIService
from cauldron_ai_admin.tools import AdminAIToolRegistry


def _make_user(*, with_ai_perm: bool):
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    User = get_user_model()
    user, _ = User.objects.get_or_create(username=f"authtest-{with_ai_perm}")
    if with_ai_perm:
        perm = Permission.objects.get(
            codename="use_admin_ai",
            content_type__app_label="cauldron_ai_admin",
        )
        user.user_permissions.add(perm)
    return User.objects.get(pk=user.pk)


def test_direct_run_without_use_admin_ai_raises_permission_denied():
    fake = FakeAIModelProvider()
    svc = AdminAIService(
        provider=fake, tool_registry=AdminAIToolRegistry(),
    )
    user = _make_user(with_ai_perm=False)

    with pytest.raises(PermissionDenied):
        svc.run(user, "Hello.")

    assert AdminAIRun.objects.filter(actor=user).count() == 0


def test_direct_run_with_use_admin_ai_succeeds():
    from cauldron_ai.contracts import AIModelResponse
    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="r1", content="hi", stop_reason="end_turn",
    ))
    svc = AdminAIService(
        provider=fake, tool_registry=AdminAIToolRegistry(),
    )
    user = _make_user(with_ai_perm=True)
    run = svc.run(user, "Hi.")
    assert run.status == "completed"
