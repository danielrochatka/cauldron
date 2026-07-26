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


# ---------------------------------------------------------------------------
# admin_ai.E011: provider factory contract validation
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_e011_no_error_when_no_factories(isolated_store):
    from cauldron_ai_admin.checks import check_provider_factory_contracts
    with override_settings(CAULDRON_MODULES=ACTIVE_MODULES):
        errors = check_provider_factory_contracts(None)
    assert not any(e.id == "admin_ai.E011" for e in errors)


@pytest.mark.django_db
def test_e011_error_when_factory_missing_test_connection(isolated_store):
    """A misregistered factory is caught before request-time."""
    from cauldron_ai.providers import _registry
    from cauldron_ai.provider_configuration import AIProviderConfigurationSpec
    from cauldron_ai_admin.checks import check_provider_factory_contracts

    class _Broken:
        name = "broken"

        @property
        def configuration_spec(self):
            return AIProviderConfigurationSpec(
                provider_name="broken", display_name="Broken",
            )

        def build(self, c, s):
            return object()

        # Missing test_connection intentionally.

    # Bypass the registry validation to plant a corrupt factory.
    with _registry._lock:
        _registry._factories["broken"] = _Broken()
    try:
        with override_settings(CAULDRON_MODULES=ACTIVE_MODULES):
            errors = check_provider_factory_contracts(None)
        assert any(e.id == "admin_ai.E011" for e in errors)
    finally:
        with _registry._lock:
            _registry._factories.pop("broken", None)


# ---------------------------------------------------------------------------
# admin_ai.E012: configuration spec validation
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_e012_no_error_when_all_specs_valid(isolated_store):
    from cauldron_ai.provider_configuration import (
        AIProviderConfigurationSpec, AIProviderConnectionResult,
    )
    from cauldron_ai.providers import register_provider_factory
    from cauldron_ai_admin.checks import check_configuration_spec_validity

    class _F:
        name = "good"

        @property
        def configuration_spec(self):
            return AIProviderConfigurationSpec(
                provider_name="good", display_name="Good",
            )

        def build(self, c, s):
            return object()

        def test_connection(self, c, s):
            return AIProviderConnectionResult(success=True, status="ok")

    register_provider_factory(_F())
    with override_settings(CAULDRON_MODULES=ACTIVE_MODULES):
        errors = check_configuration_spec_validity(None)
    assert not any(e.id == "admin_ai.E012" for e in errors)


@pytest.mark.django_db
def test_e012_error_when_spec_provider_name_mismatch(isolated_store):
    from cauldron_ai.provider_configuration import (
        AIProviderConfigurationSpec, AIProviderConnectionResult,
    )
    from cauldron_ai.providers import register_provider_factory
    from cauldron_ai_admin.checks import check_configuration_spec_validity

    class _Skew:
        name = "skew"

        @property
        def configuration_spec(self):
            # Deliberate mismatch: spec.provider_name != factory.name
            return AIProviderConfigurationSpec(
                provider_name="wrong", display_name="Skew",
            )

        def build(self, c, s):
            return object()

        def test_connection(self, c, s):
            return AIProviderConnectionResult(success=True, status="ok")

    register_provider_factory(_Skew())
    with override_settings(CAULDRON_MODULES=ACTIVE_MODULES):
        errors = check_configuration_spec_validity(None)
    assert any(e.id == "admin_ai.E012" for e in errors)


# ---------------------------------------------------------------------------
# admin_ai.W003: missing required non-credential config
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_w003_warning_when_required_model_missing(isolated_store):
    from cauldron_ai.provider_configuration import (
        AIProviderConfigurationField,
        AIProviderConfigurationSpec,
        AIProviderConnectionResult,
    )
    from cauldron_ai.providers import register_provider_factory
    from cauldron_ai_admin.checks import (
        check_selected_provider_has_required_config,
    )
    from cauldron_ai_admin.provider_config import AIProviderSettingsStore

    class _F:
        name = "modelreq"

        @property
        def configuration_spec(self):
            return AIProviderConfigurationSpec(
                provider_name="modelreq", display_name="MR",
                fields=(AIProviderConfigurationField(
                    name="model", label="Model", required=True,
                ),),
            )

        def build(self, c, s):
            return object()

        def test_connection(self, c, s):
            return AIProviderConnectionResult(success=True, status="ok")

    register_provider_factory(_F())
    store = AIProviderSettingsStore(isolated_store)
    store.set_selected_provider("modelreq")
    with override_settings(CAULDRON_MODULES=ACTIVE_MODULES):
        warnings = check_selected_provider_has_required_config(None)
    assert any(w.id == "admin_ai.W003" for w in warnings)


# ---------------------------------------------------------------------------
# admin_ai.W004: missing required credential
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_w004_warning_when_required_api_key_missing(isolated_store, monkeypatch):
    from cauldron_ai.provider_configuration import (
        AIProviderConfigurationField,
        AIProviderConfigurationSpec,
        AIProviderConnectionResult,
        FIELD_TYPE_PASSWORD,
    )
    from cauldron_ai.providers import register_provider_factory
    from cauldron_ai_admin.checks import (
        check_selected_provider_has_credentials,
    )
    from cauldron_ai_admin.provider_config import AIProviderSettingsStore

    class _F:
        name = "credreq"

        @property
        def configuration_spec(self):
            return AIProviderConfigurationSpec(
                provider_name="credreq", display_name="Cred",
                fields=(AIProviderConfigurationField(
                    name="api_key", label="Key",
                    field_type=FIELD_TYPE_PASSWORD, required=True,
                    environment_variable="_TEST_MISSING_KEY",
                ),),
            )

        def build(self, c, s):
            return object()

        def test_connection(self, c, s):
            return AIProviderConnectionResult(success=True, status="ok")

    register_provider_factory(_F())
    store = AIProviderSettingsStore(isolated_store)
    store.set_selected_provider("credreq")
    monkeypatch.delenv("_TEST_MISSING_KEY", raising=False)
    with override_settings(CAULDRON_MODULES=ACTIVE_MODULES):
        warnings = check_selected_provider_has_credentials(None)
    assert any(w.id == "admin_ai.W004" for w in warnings)


@pytest.mark.django_db
def test_w004_no_warning_when_env_var_provides_key(isolated_store, monkeypatch):
    from cauldron_ai.provider_configuration import (
        AIProviderConfigurationField,
        AIProviderConfigurationSpec,
        AIProviderConnectionResult,
        FIELD_TYPE_PASSWORD,
    )
    from cauldron_ai.providers import register_provider_factory
    from cauldron_ai_admin.checks import (
        check_selected_provider_has_credentials,
    )
    from cauldron_ai_admin.provider_config import AIProviderSettingsStore

    class _F:
        name = "envcred"

        @property
        def configuration_spec(self):
            return AIProviderConfigurationSpec(
                provider_name="envcred", display_name="Env",
                fields=(AIProviderConfigurationField(
                    name="api_key", label="Key",
                    field_type=FIELD_TYPE_PASSWORD, required=True,
                    environment_variable="_TEST_ENV_KEY_OK",
                ),),
            )

        def build(self, c, s):
            return object()

        def test_connection(self, c, s):
            return AIProviderConnectionResult(success=True, status="ok")

    register_provider_factory(_F())
    store = AIProviderSettingsStore(isolated_store)
    store.set_selected_provider("envcred")
    monkeypatch.setenv("_TEST_ENV_KEY_OK", "sk-from-env")
    with override_settings(CAULDRON_MODULES=ACTIVE_MODULES):
        warnings = check_selected_provider_has_credentials(None)
    assert not any(w.id == "admin_ai.W004" for w in warnings)


# ---------------------------------------------------------------------------
# admin_ai.E013/E014/E015/E016/W005/W006: config file health
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_e013_error_on_malformed_json(isolated_store):
    from cauldron_ai_admin.checks import check_config_file_readable
    isolated_store.write_text("{malformed", encoding="utf-8")
    import os as _os
    _os.chmod(str(isolated_store), 0o600)
    with override_settings(CAULDRON_MODULES=ACTIVE_MODULES):
        errors = check_config_file_readable(None)
    assert any(e.id == "admin_ai.E013" for e in errors)


@pytest.mark.django_db
def test_e014_error_on_unsupported_version(isolated_store):
    from cauldron_ai_admin.checks import check_config_file_readable
    isolated_store.write_text('{"version": 99}', encoding="utf-8")
    import os as _os
    _os.chmod(str(isolated_store), 0o600)
    with override_settings(CAULDRON_MODULES=ACTIVE_MODULES):
        errors = check_config_file_readable(None)
    assert any(e.id == "admin_ai.E014" for e in errors)


@pytest.mark.django_db
def test_e015_error_on_symlink(isolated_store, tmp_path):
    from cauldron_ai_admin.checks import check_config_file_readable
    real = tmp_path / "real.json"
    real.write_text('{"version": 1}', encoding="utf-8")
    isolated_store.symlink_to(real)
    with override_settings(CAULDRON_MODULES=ACTIVE_MODULES):
        errors = check_config_file_readable(None)
    assert any(e.id == "admin_ai.E015" for e in errors)


@pytest.mark.django_db
def test_w005_warning_on_oversized_file(isolated_store):
    from cauldron_ai_admin.checks import check_config_file_readable
    # Write >64KB of arbitrary bytes.
    isolated_store.write_text("x" * (65 * 1024), encoding="utf-8")
    import os as _os
    _os.chmod(str(isolated_store), 0o600)
    with override_settings(CAULDRON_MODULES=ACTIVE_MODULES):
        results = check_config_file_readable(None)
    assert any(w.id == "admin_ai.W005" for w in results)


@pytest.mark.django_db
def test_w006_warning_on_wide_parent_permissions(isolated_store):
    from cauldron_ai_admin.checks import check_config_file_readable
    isolated_store.write_text('{"version": 1}', encoding="utf-8")
    import os as _os
    _os.chmod(str(isolated_store), 0o600)
    _os.chmod(str(isolated_store.parent), 0o755)
    with override_settings(CAULDRON_MODULES=ACTIVE_MODULES):
        results = check_config_file_readable(None)
    assert any(w.id == "admin_ai.W006" for w in results)


@pytest.mark.django_db
def test_config_file_checks_inactive_when_module_not_active(isolated_store):
    from cauldron_ai_admin.checks import check_config_file_readable
    with override_settings(CAULDRON_MODULES={}):
        assert check_config_file_readable(None) == []
