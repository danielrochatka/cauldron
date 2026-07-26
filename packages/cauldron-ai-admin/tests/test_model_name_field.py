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


# ---------------------------------------------------------------------------
# Service records model_name from provider on new runs
# ---------------------------------------------------------------------------

def _permitted_user():
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    User = get_user_model()
    user, _ = User.objects.get_or_create(username="model_name_service")
    for spec in ("cauldron_ai_admin.use_admin_ai",):
        app_label, codename = spec.split(".", 1)
        try:
            perm = Permission.objects.get(
                codename=codename, content_type__app_label=app_label,
            )
        except Permission.DoesNotExist:
            continue
        user.user_permissions.add(perm)
    return User.objects.get(pk=user.pk)


def test_service_records_provider_model_on_run():
    from cauldron_ai.contracts import AIModelResponse
    from cauldron_ai.testing import FakeAIModelProvider
    from cauldron_ai_admin.service import AdminAIService
    from cauldron_ai_admin.tools import AdminAIToolRegistry

    fake = FakeAIModelProvider(name="fake")
    fake.queue_response(AIModelResponse(
        provider_request_id="r1",
        content="hello",
        stop_reason="end_turn",
    ))
    service = AdminAIService(
        provider=fake,
        tool_registry=AdminAIToolRegistry(),
        max_model_turns=1,
        max_tool_calls=1,
        tool_timeout_seconds=5.0,
        run_timeout_seconds=10.0,
    )
    run = service.run(_permitted_user(), "hi", correlation_id="c-1")
    run.refresh_from_db()
    assert run.model_name == "fake"


def test_service_records_provider_model_from_property():
    """A provider exposing ``model`` as an @property is recorded verbatim."""
    from cauldron_ai.contracts import AIModelRequest, AIModelResponse
    from cauldron_ai_admin.service import AdminAIService
    from cauldron_ai_admin.tools import AdminAIToolRegistry

    class _PropProvider:
        name = "propp"
        _internal_model = "gpt-4o-custom"

        @property
        def model(self):
            return self._internal_model

        def __init__(self):
            self._responses = [
                AIModelResponse(
                    provider_request_id="r1",
                    content="ok",
                    stop_reason="end_turn",
                ),
            ]

        def complete(self, request):
            return self._responses.pop(0)

    service = AdminAIService(
        provider=_PropProvider(),
        tool_registry=AdminAIToolRegistry(),
        max_model_turns=1,
        max_tool_calls=1,
        tool_timeout_seconds=5.0,
        run_timeout_seconds=10.0,
    )
    run = service.run(_permitted_user(), "hi", correlation_id="c-2")
    run.refresh_from_db()
    assert run.model_name == "gpt-4o-custom"
