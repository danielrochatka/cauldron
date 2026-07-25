"""Tests for AdminAIRun.model_name field added in Phase 2."""
import pytest
from django.contrib.auth import get_user_model

pytestmark = pytest.mark.django_db


def _get_user():
    User = get_user_model()
    user, _ = User.objects.get_or_create(username="model_name_test")
    return user


def test_adminairun_has_model_name_field():
    from cauldron_ai_admin.models import AdminAIRun
    assert hasattr(AdminAIRun, "model_name")


def test_adminairun_model_name_defaults_to_empty():
    from cauldron_ai_admin.models import AdminAIRun
    run = AdminAIRun(
        actor=_get_user(),
        provider_name="fake",
        user_request="test",
    )
    assert run.model_name == ""


def test_adminairun_model_name_can_be_set_and_saved():
    from cauldron_ai_admin.models import AdminAIRun
    run = AdminAIRun.objects.create(
        actor=_get_user(),
        provider_name="fake",
        model_name="gpt-4o",
        user_request="test",
    )
    run.refresh_from_db()
    assert run.model_name == "gpt-4o"


def test_adminairun_model_name_blank_allowed():
    from cauldron_ai_admin.models import AdminAIRun
    run = AdminAIRun.objects.create(
        actor=_get_user(),
        provider_name="fake",
        model_name="",
        user_request="test",
    )
    run.refresh_from_db()
    assert run.model_name == ""
