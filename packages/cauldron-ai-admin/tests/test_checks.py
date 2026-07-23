"""Tests for cauldron_ai_admin.checks."""
import pytest
from django.test import override_settings

from cauldron_ai_admin.checks import (
    check_ai_provider_registered,
    check_limits_are_positive,
    check_no_duplicate_tool_names,
    check_required_dependencies,
)
from cauldron_ai.providers import _reset_registry_for_tests, register_provider


class _P:
    name = "test-provider"

    def complete(self, req):  # pragma: no cover - not invoked
        return None


@pytest.fixture(autouse=True)
def reset_ai_registry():
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


def test_e001_fires_when_no_provider():
    errors = check_ai_provider_registered(None)
    ids = [e.id for e in errors]
    assert "admin_ai.E001" in ids


def test_e001_silent_when_provider_registered():
    register_provider(_P())
    errors = check_ai_provider_registered(None)
    assert all(e.id != "admin_ai.E001" for e in errors)


def test_e002_fires_when_preferred_missing():
    register_provider(_P())
    with override_settings(CAULDRON_MODULES={
        "cauldron.ai.admin": {"preferred_provider": "not-registered"},
    }):
        errors = check_ai_provider_registered(None)
    ids = [e.id for e in errors]
    assert "admin_ai.E002" in ids


def test_e002_silent_when_preferred_present():
    register_provider(_P())
    with override_settings(CAULDRON_MODULES={
        "cauldron.ai.admin": {"preferred_provider": "test-provider"},
    }):
        errors = check_ai_provider_registered(None)
    assert all(e.id != "admin_ai.E002" for e in errors)


def test_e003_bad_max_model_turns():
    with override_settings(CAULDRON_MODULES={
        "cauldron.ai.admin": {"max_model_turns": 0},
    }):
        errors = check_limits_are_positive(None)
    assert any(e.id == "admin_ai.E003" for e in errors)


def test_e003_bad_timeout():
    with override_settings(CAULDRON_MODULES={
        "cauldron.ai.admin": {"tool_timeout_seconds": -1},
    }):
        errors = check_limits_are_positive(None)
    assert any(e.id == "admin_ai.E003" for e in errors)


def test_e003_silent_when_ok():
    with override_settings(CAULDRON_MODULES={
        "cauldron.ai.admin": {
            "max_model_turns": 5, "tool_timeout_seconds": 10.0,
        },
    }):
        errors = check_limits_are_positive(None)
    assert errors == []


def test_e004_silent_when_no_duplicates():
    errors = check_no_duplicate_tool_names(None)
    assert errors == []


def test_e005_silent_when_apps_present():
    errors = check_required_dependencies(None)
    assert errors == []


def test_e005_fires_when_ops_app_missing():
    with override_settings(
        INSTALLED_APPS=["django.contrib.contenttypes", "cauldron_ai_admin"],
    ):
        errors = check_required_dependencies(None)
    assert any(e.id == "admin_ai.E005" for e in errors)


def test_checks_skip_when_admin_ai_inactive():
    with override_settings(CAULDRON_MODULES={"cauldron.content": {}}):
        assert check_ai_provider_registered(None) == []
        assert check_limits_are_positive(None) == []
        assert check_no_duplicate_tool_names(None) == []
        assert check_required_dependencies(None) == []
