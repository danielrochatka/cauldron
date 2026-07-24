"""Tests verifying the shell migration: template extension, navigation, view access."""
import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client

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


# ---------------------------------------------------------------------------
# test_ai_page_extends_shell
# ---------------------------------------------------------------------------

def test_ai_page_extends_shell():
    """ai_page.html extends cauldron_admin/base.html."""
    from django.template.loader import get_template
    template = get_template("cauldron_ai_admin/ai_page.html")
    # The template source should reference the shell base
    source = template.template.source
    assert "cauldron_admin/base.html" in source


# ---------------------------------------------------------------------------
# test_ai_page_navigation_registered
# ---------------------------------------------------------------------------

def test_ai_page_navigation_registered():
    """Navigation registry has the ai-page item after app ready."""
    from cauldron_django_admin.navigation import get_navigation_registry
    registry = get_navigation_registry()
    items = {item.key: item for item in registry._items.values()}
    assert "cauldron.ai.admin.page" in items
    item = items["cauldron.ai.admin.page"]
    assert item.url_name == "cauldron_ai_admin:ai-page"
    assert item.section == "ai"


# ---------------------------------------------------------------------------
# test_run_list_view_requires_permission
# ---------------------------------------------------------------------------

def test_run_list_view_requires_permission():
    """The run list view returns 403 for a user without view_admin_ai_runs."""
    user = _make_user(username="no-runs-perm", perms=())
    client = Client()
    client.force_login(user)

    from django.urls import reverse
    url = reverse("cauldron_ai_admin:run-list")
    response = client.get(url)
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# test_run_detail_view_authenticated
# ---------------------------------------------------------------------------

def test_run_detail_view_authenticated():
    """Run detail returns 200 for the run's owner with view_admin_ai_runs perm."""
    from cauldron_ai_admin.models import AdminAIRun
    from django.utils import timezone

    user = _make_user(
        username="run-owner",
        perms=("cauldron_ai_admin.view_admin_ai_runs",),
    )
    run = AdminAIRun.objects.create(
        actor=user,
        status="completed",
        provider_name="test",
        user_request="test request",
        final_response="test response",
        completed_at=timezone.now(),
    )

    client = Client()
    client.force_login(user)

    from django.urls import reverse
    url = reverse("cauldron_ai_admin:run-detail", kwargs={"run_id": run.run_id})
    response = client.get(url)
    assert response.status_code == 200
