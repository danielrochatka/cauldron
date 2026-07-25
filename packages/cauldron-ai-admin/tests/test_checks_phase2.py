"""Tests for Phase 2 system checks: admin_ai.E010 and admin_ai.W002."""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from django.test.utils import override_settings

from cauldron_ai.providers import _reset_registry_for_tests


ACTIVE_MODULES = {
    "cauldron.ai.admin": {
        "provider": "fake",
    }
}


@pytest.fixture(autouse=True)
def clean_registry():
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


@pytest.fixture()
def isolated_store(tmp_path):
    from cauldron_ai_admin.provider_config import _reset_store_for_tests
    p = tmp_path / "ai.json"
    _reset_store_for_tests(path=p)
    yield p
    _reset_store_for_tests(path=None)


# ---------------------------------------------------------------------------
# admin_ai.E010: selected provider must be registered
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_e010_no_error_when_no_explicit_selection(isolated_store):
    from cauldron_ai_admin.checks import check_selected_provider_has_factory_or_instance
    with override_settings(CAULDRON_MODULES=ACTIVE_MODULES):
        errors = check_selected_provider_has_factory_or_instance(None)
    assert not any(e.id == "admin_ai.E010" for e in errors)


@pytest.mark.django_db
def test_e010_no_error_when_provider_registered_as_instance(isolated_store):
    from cauldron_ai.providers import register_provider
    from cauldron_ai_admin.checks import check_selected_provider_has_factory_or_instance
    from cauldron_ai_admin.provider_config import AIProviderSettingsStore

    store = AIProviderSettingsStore(isolated_store)
    store.set_selected_provider("myprovider")

    class _P:
        name = "myprovider"
        def complete(self, r): return None

    register_provider(_P())
    with override_settings(CAULDRON_MODULES=ACTIVE_MODULES):
        errors = check_selected_provider_has_factory_or_instance(None)
    assert not any(e.id == "admin_ai.E010" for e in errors)


@pytest.mark.django_db
def test_e010_no_error_when_provider_registered_as_factory(isolated_store):
    from cauldron_ai.providers import register_provider_factory
    from cauldron_ai.provider_configuration import AIProviderConfigurationSpec, AIProviderConnectionResult
    from cauldron_ai_admin.checks import check_selected_provider_has_factory_or_instance
    from cauldron_ai_admin.provider_config import AIProviderSettingsStore

    store = AIProviderSettingsStore(isolated_store)
    store.set_selected_provider("myfactory")

    class _F:
        name = "myfactory"
        @property
        def configuration_spec(self):
            return AIProviderConfigurationSpec(provider_name="myfactory", display_name="My Factory")
        def build(self, c, s): return object()
        def test_connection(self, c, s): return AIProviderConnectionResult(success=True, status="ok")

    register_provider_factory(_F())
    with override_settings(CAULDRON_MODULES=ACTIVE_MODULES):
        errors = check_selected_provider_has_factory_or_instance(None)
    assert not any(e.id == "admin_ai.E010" for e in errors)


@pytest.mark.django_db
def test_e010_error_when_selected_provider_not_registered(isolated_store):
    from cauldron_ai_admin.checks import check_selected_provider_has_factory_or_instance
    from cauldron_ai_admin.provider_config import AIProviderSettingsStore

    store = AIProviderSettingsStore(isolated_store)
    store.set_selected_provider("missing-provider")

    with override_settings(CAULDRON_MODULES=ACTIVE_MODULES):
        errors = check_selected_provider_has_factory_or_instance(None)
    assert any(e.id == "admin_ai.E010" for e in errors)


# ---------------------------------------------------------------------------
# admin_ai.W002: config file permissions
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_w002_no_warning_when_no_file(isolated_store):
    from cauldron_ai_admin.checks import check_ai_config_file_permissions
    with override_settings(CAULDRON_MODULES=ACTIVE_MODULES):
        warnings = check_ai_config_file_permissions(None)
    assert not any(w.id == "admin_ai.W002" for w in warnings)


@pytest.mark.django_db
def test_w002_no_warning_when_file_is_0600(isolated_store):
    from cauldron_ai_admin.checks import check_ai_config_file_permissions
    from cauldron_ai_admin.provider_config import AIProviderSettingsStore

    store = AIProviderSettingsStore(isolated_store)
    store.save({"provider": "fake"})  # creates with 0600

    with override_settings(CAULDRON_MODULES=ACTIVE_MODULES):
        warnings = check_ai_config_file_permissions(None)
    assert not any(w.id == "admin_ai.W002" for w in warnings)


@pytest.mark.django_db
def test_w002_warning_when_file_not_0600(isolated_store):
    from cauldron_ai_admin.checks import check_ai_config_file_permissions
    from cauldron_ai_admin.provider_config import AIProviderSettingsStore

    store = AIProviderSettingsStore(isolated_store)
    store.save({"provider": "fake"})
    os.chmod(str(isolated_store), 0o644)

    with override_settings(CAULDRON_MODULES=ACTIVE_MODULES):
        warnings = check_ai_config_file_permissions(None)
    assert any(w.id == "admin_ai.W002" for w in warnings)


@pytest.mark.django_db
def test_checks_inactive_when_module_not_active(isolated_store):
    from cauldron_ai_admin.checks import (
        check_ai_config_file_permissions,
        check_selected_provider_has_factory_or_instance,
    )
    with override_settings(CAULDRON_MODULES={}):
        assert check_ai_config_file_permissions(None) == []
        assert check_selected_provider_has_factory_or_instance(None) == []
