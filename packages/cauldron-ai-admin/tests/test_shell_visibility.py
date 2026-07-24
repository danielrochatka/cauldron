"""Tests for admin-visibility permissions on the shell views."""
import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from cauldron_ai_admin.models import AdminAIRun


pytestmark = pytest.mark.django_db


def _make_user(*, username, perms=()):
    User = get_user_model()
    user, _ = User.objects.get_or_create(username=username)
    user.set_password("pw")
    user.save()
    for spec in perms:
        app_label, codename = spec.split(".", 1)
        try:
            perm = Permission.objects.get(
                codename=codename, content_type__app_label=app_label,
            )
        except Permission.DoesNotExist:
            continue
        user.user_permissions.add(perm)
    return User.objects.get(pk=user.pk)


def test_run_list_admin_visibility():
    """The run list surfaces every run for anyone with view_admin_ai_runs."""
    owner = _make_user(username="run-owner-shell")
    viewer = _make_user(
        username="run-viewer-shell",
        perms=("cauldron_ai_admin.view_admin_ai_runs",),
    )
    other_run = AdminAIRun.objects.create(
        actor=owner,
        status="completed",
        provider_name="test",
        user_request="alien",
        final_response="ok",
        completed_at=timezone.now(),
    )

    client = Client()
    client.force_login(viewer)
    response = client.get(reverse("cauldron_ai_admin:run-list"))
    assert response.status_code == 200
    # The viewer never created this run, but with view_admin_ai_runs must see it.
    assert str(other_run.run_id).encode() in response.content


def test_invocations_hidden_without_audit_perm():
    """Run detail without view_admin_ai_audit shows no invocations."""
    from cauldron_ai_admin.models import AdminAIToolInvocation

    viewer = _make_user(
        username="run-noaudit",
        perms=("cauldron_ai_admin.view_admin_ai_runs",),
    )
    run = AdminAIRun.objects.create(
        actor=viewer,
        status="completed",
        provider_name="test",
        user_request="hi",
        final_response="ok",
        completed_at=timezone.now(),
    )
    AdminAIToolInvocation.objects.create(
        run=run,
        tool_name="content.list_collections",
        risk_level="READ_ONLY",
        status="completed",
        started_at=timezone.now(),
        completed_at=timezone.now(),
    )

    client = Client()
    client.force_login(viewer)
    response = client.get(
        reverse("cauldron_ai_admin:run-detail", kwargs={"run_id": run.run_id})
    )
    assert response.status_code == 200
    # Without audit perm the invocation table (and tool name) must not be rendered.
    assert b"content.list_collections" not in response.content
    assert b"Tool Invocations" not in response.content


def test_invocations_shown_with_audit_perm():
    """Run detail with view_admin_ai_audit shows tool invocations."""
    from cauldron_ai_admin.models import AdminAIToolInvocation

    viewer = _make_user(
        username="run-audit",
        perms=(
            "cauldron_ai_admin.view_admin_ai_runs",
            "cauldron_ai_admin.view_admin_ai_audit",
        ),
    )
    run = AdminAIRun.objects.create(
        actor=viewer,
        status="completed",
        provider_name="test",
        user_request="hi",
        final_response="ok",
        completed_at=timezone.now(),
    )
    AdminAIToolInvocation.objects.create(
        run=run,
        tool_name="content.list_collections",
        risk_level="READ_ONLY",
        status="completed",
        started_at=timezone.now(),
        completed_at=timezone.now(),
    )

    client = Client()
    client.force_login(viewer)
    response = client.get(
        reverse("cauldron_ai_admin:run-detail", kwargs={"run_id": run.run_id})
    )
    assert response.status_code == 200
    assert b"content.list_collections" in response.content
