"""Custom permission enforcement on the Django admin registrations."""
from __future__ import annotations

import pytest
from django.contrib import admin as _admin

pytestmark = pytest.mark.django_db

from cauldron_ai_admin.admin import AdminAIRunAdmin, AdminAIToolInvocationAdmin
from cauldron_ai_admin.models import AdminAIRun, AdminAIToolInvocation


def _make_user(*, perms=()):
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    User = get_user_model()
    user, _ = User.objects.get_or_create(username=f"perms-{','.join(perms) or 'none'}")
    user.is_staff = True
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


def _req(user):
    class _Request:
        pass
    r = _Request()
    r.user = user
    return r


def test_use_admin_ai_only_cannot_view_runs():
    user = _make_user(perms=("cauldron_ai_admin.use_admin_ai",))
    site = AdminAIRunAdmin(AdminAIRun, _admin.site)
    request = _req(user)
    assert not site.has_module_permission(request)
    assert not site.has_view_permission(request)


def test_use_admin_ai_only_cannot_view_invocations():
    user = _make_user(perms=("cauldron_ai_admin.use_admin_ai",))
    site = AdminAIToolInvocationAdmin(AdminAIToolInvocation, _admin.site)
    request = _req(user)
    assert not site.has_module_permission(request)
    assert not site.has_view_permission(request)


def test_view_admin_ai_runs_grants_view_of_runs():
    user = _make_user(perms=("cauldron_ai_admin.view_admin_ai_runs",))
    site = AdminAIRunAdmin(AdminAIRun, _admin.site)
    request = _req(user)
    assert site.has_module_permission(request)
    assert site.has_view_permission(request)


def test_view_admin_ai_audit_grants_view_of_invocations():
    user = _make_user(perms=("cauldron_ai_admin.view_admin_ai_audit",))
    site = AdminAIToolInvocationAdmin(AdminAIToolInvocation, _admin.site)
    request = _req(user)
    assert site.has_module_permission(request)
    assert site.has_view_permission(request)


def test_admin_registrations_never_allow_mutations():
    user = _make_user(perms=("cauldron_ai_admin.view_admin_ai_runs",))
    request = _req(user)
    for site_cls, model in (
        (AdminAIRunAdmin, AdminAIRun),
        (AdminAIToolInvocationAdmin, AdminAIToolInvocation),
    ):
        site = site_cls(model, _admin.site)
        assert not site.has_add_permission(request)
        assert not site.has_change_permission(request)
        assert not site.has_delete_permission(request)
