"""Tests for the dynamic ProviderConfigForm."""
from __future__ import annotations

import pytest

from cauldron_ai.provider_configuration import (
    FIELD_TYPE_BOOLEAN,
    FIELD_TYPE_INTEGER,
    FIELD_TYPE_PASSWORD,
    FIELD_TYPE_TEXT,
    AIProviderConfigurationField,
    AIProviderConfigurationSpec,
)
from cauldron_ai_admin.forms import ProviderConfigForm, ProviderSelectForm


def _make_spec(fields=()):
    return AIProviderConfigurationSpec(
        provider_name="testprovider",
        display_name="Test Provider",
        fields=fields,
        supports_connection_test=True,
    )


# ---------------------------------------------------------------------------
# Field generation
# ---------------------------------------------------------------------------

def test_form_has_field_from_spec():
    spec = _make_spec(fields=(
        AIProviderConfigurationField(name="api_key", label="API Key", field_type=FIELD_TYPE_PASSWORD),
    ))
    form = ProviderConfigForm(spec=spec)
    assert "api_key" in form.fields


def test_password_field_uses_password_widget():
    from django.forms import PasswordInput
    spec = _make_spec(fields=(
        AIProviderConfigurationField(name="api_key", label="Key", field_type=FIELD_TYPE_PASSWORD),
    ))
    form = ProviderConfigForm(spec=spec)
    widget = form.fields["api_key"].widget
    assert isinstance(widget, PasswordInput)


def test_password_widget_render_value_false():
    from django.forms import PasswordInput
    spec = _make_spec(fields=(
        AIProviderConfigurationField(name="api_key", label="Key", field_type=FIELD_TYPE_PASSWORD),
    ))
    form = ProviderConfigForm(spec=spec)
    widget = form.fields["api_key"].widget
    assert isinstance(widget, PasswordInput)
    assert not widget.render_value


def test_integer_field_is_integer_form_field():
    from django.forms import IntegerField
    spec = _make_spec(fields=(
        AIProviderConfigurationField(name="timeout", label="Timeout", field_type=FIELD_TYPE_INTEGER),
    ))
    form = ProviderConfigForm(spec=spec)
    assert isinstance(form.fields["timeout"], IntegerField)


def test_boolean_field_is_boolean_form_field():
    from django.forms import BooleanField
    spec = _make_spec(fields=(
        AIProviderConfigurationField(name="store", label="Store", field_type=FIELD_TYPE_BOOLEAN),
    ))
    form = ProviderConfigForm(spec=spec)
    assert isinstance(form.fields["store"], BooleanField)


def test_boolean_field_required_is_false():
    spec = _make_spec(fields=(
        AIProviderConfigurationField(name="flag", label="F", field_type=FIELD_TYPE_BOOLEAN),
    ))
    form = ProviderConfigForm(spec=spec)
    assert form.fields["flag"].required is False


def test_text_field_has_max_length():
    spec = _make_spec(fields=(
        AIProviderConfigurationField(name="model", label="Model", max_length=64),
    ))
    form = ProviderConfigForm(spec=spec)
    assert form.fields["model"].max_length == 64


# ---------------------------------------------------------------------------
# Unbound form — no pre-population of passwords
# ---------------------------------------------------------------------------

def test_password_field_has_no_initial_value():
    spec = _make_spec(fields=(
        AIProviderConfigurationField(name="api_key", label="Key", field_type=FIELD_TYPE_PASSWORD),
    ))
    form = ProviderConfigForm(
        spec=spec,
        current_config={"api_key": "sk-existing"},
    )
    # initial for password must not be set to the stored value
    assert not form.fields["api_key"].initial


def test_non_password_field_gets_current_config_as_initial():
    spec = _make_spec(fields=(
        AIProviderConfigurationField(name="model_name", label="Model"),
    ))
    form = ProviderConfigForm(
        spec=spec,
        current_config={"model_name": "gpt-4o"},
    )
    assert form.initial.get("model_name") == "gpt-4o"


# ---------------------------------------------------------------------------
# Standard and advanced field grouping
# ---------------------------------------------------------------------------

def test_standard_fields_excludes_advanced():
    spec = _make_spec(fields=(
        AIProviderConfigurationField(name="normal", label="N"),
        AIProviderConfigurationField(name="adv", label="A", advanced=True),
    ))
    form = ProviderConfigForm(spec=spec)
    standard_names = [bf.name for bf in form.standard_fields]
    assert "normal" in standard_names
    assert "adv" not in standard_names


def test_advanced_fields_only_advanced():
    spec = _make_spec(fields=(
        AIProviderConfigurationField(name="normal", label="N"),
        AIProviderConfigurationField(name="adv", label="A", advanced=True),
    ))
    form = ProviderConfigForm(spec=spec)
    adv_names = [bf.name for bf in form.advanced_fields]
    assert "adv" in adv_names
    assert "normal" not in adv_names


def test_no_advanced_fields_returns_empty():
    spec = _make_spec(fields=(
        AIProviderConfigurationField(name="x", label="X"),
    ))
    form = ProviderConfigForm(spec=spec)
    assert form.advanced_fields == []


# ---------------------------------------------------------------------------
# split_config_and_secrets
# ---------------------------------------------------------------------------

def test_split_separates_password_from_config():
    spec = _make_spec(fields=(
        AIProviderConfigurationField(name="model_name", label="Model"),
        AIProviderConfigurationField(name="api_key", label="Key", field_type=FIELD_TYPE_PASSWORD),
    ))
    form = ProviderConfigForm(
        {"model_name": "gpt-4o", "api_key": "sk-abc"},
        spec=spec,
    )
    assert form.is_valid(), form.errors
    config, secrets = form.split_config_and_secrets()
    assert config == {"model_name": "gpt-4o"}
    assert secrets == {"api_key": "sk-abc"}


def test_split_empty_password_not_in_secrets():
    spec = _make_spec(fields=(
        AIProviderConfigurationField(name="model_name", label="Model"),
        AIProviderConfigurationField(
            name="api_key", label="Key", field_type=FIELD_TYPE_PASSWORD, required=False
        ),
    ))
    form = ProviderConfigForm(
        {"model_name": "gpt-4o", "api_key": ""},
        spec=spec,
    )
    assert form.is_valid(), form.errors
    config, secrets = form.split_config_and_secrets()
    assert "api_key" not in secrets


def test_split_required_field_in_config():
    spec = _make_spec(fields=(
        AIProviderConfigurationField(name="model_name", label="Model", required=True),
    ))
    form = ProviderConfigForm(
        {"model_name": "gpt-4o"},
        spec=spec,
    )
    assert form.is_valid(), form.errors
    config, _ = form.split_config_and_secrets()
    assert config["model_name"] == "gpt-4o"


# ---------------------------------------------------------------------------
# ProviderSelectForm
# ---------------------------------------------------------------------------

def test_select_form_valid():
    form = ProviderSelectForm(
        {"provider": "openai"},
        available_providers=["openai", "fake"],
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["provider"] == "openai"


def test_select_form_invalid_choice():
    form = ProviderSelectForm(
        {"provider": "unknown"},
        available_providers=["openai", "fake"],
    )
    assert not form.is_valid()
    assert "provider" in form.errors


def test_select_form_choices_are_sorted():
    form = ProviderSelectForm(
        available_providers=["zz", "aa", "mm"],
    )
    choices = [c[0] for c in form.fields["provider"].choices]
    assert choices == sorted(choices)


# ---------------------------------------------------------------------------
# Password fields are always required=False so blank means "retain existing"
# ---------------------------------------------------------------------------

def test_password_field_required_false_even_when_spec_required():
    spec = _make_spec(fields=(
        AIProviderConfigurationField(
            name="api_key", label="Key",
            field_type=FIELD_TYPE_PASSWORD, required=True,
        ),
    ))
    form = ProviderConfigForm(spec=spec)
    # Spec says required=True but the *form* field is required=False so
    # empty submissions keep the existing stored secret.
    assert form.fields["api_key"].required is False


def test_blank_password_does_not_appear_in_secrets():
    spec = _make_spec(fields=(
        AIProviderConfigurationField(
            name="api_key", label="K", field_type=FIELD_TYPE_PASSWORD,
        ),
        AIProviderConfigurationField(
            name="model", label="M", required=False,
        ),
    ))
    form = ProviderConfigForm(
        {"api_key": "", "model": "gpt-4o"}, spec=spec,
    )
    assert form.is_valid(), form.errors
    _, secrets = form.split_config_and_secrets()
    assert "api_key" not in secrets


# ---------------------------------------------------------------------------
# Credential state display
# ---------------------------------------------------------------------------

def test_credential_state_addendum_added_to_help_text():
    from cauldron_ai_admin.forms import CREDENTIAL_STATE_MANAGED
    spec = _make_spec(fields=(
        AIProviderConfigurationField(
            name="api_key", label="K",
            field_type=FIELD_TYPE_PASSWORD,
            help_text="Original help.",
        ),
    ))
    form = ProviderConfigForm(
        spec=spec,
        credential_states={"api_key": CREDENTIAL_STATE_MANAGED},
    )
    help_text = form.fields["api_key"].help_text
    assert "Original help." in help_text
    assert "Configured in managed storage" in help_text
    assert "Leave blank" in help_text


def test_credential_state_environment_shown():
    from cauldron_ai_admin.forms import CREDENTIAL_STATE_ENVIRONMENT
    spec = _make_spec(fields=(
        AIProviderConfigurationField(
            name="api_key", label="K", field_type=FIELD_TYPE_PASSWORD,
        ),
    ))
    form = ProviderConfigForm(
        spec=spec,
        credential_states={"api_key": CREDENTIAL_STATE_ENVIRONMENT},
    )
    assert "environment variable" in form.fields["api_key"].help_text.lower()


def test_credential_state_not_configured_shown():
    from cauldron_ai_admin.forms import CREDENTIAL_STATE_NOT_CONFIGURED
    spec = _make_spec(fields=(
        AIProviderConfigurationField(
            name="api_key", label="K", field_type=FIELD_TYPE_PASSWORD,
        ),
    ))
    form = ProviderConfigForm(
        spec=spec,
        credential_states={"api_key": CREDENTIAL_STATE_NOT_CONFIGURED},
    )
    assert "Not configured" in form.fields["api_key"].help_text


def test_get_credential_state_prefers_managed_storage(monkeypatch):
    from cauldron_ai_admin.forms import (
        CREDENTIAL_STATE_MANAGED, get_credential_state,
    )

    class _Store:
        def get_secret(self, provider, key):
            return "sk-stored"

    monkeypatch.setenv("MY_KEY", "sk-env")
    state = get_credential_state("openai", "api_key", _Store(), "MY_KEY")
    assert state == CREDENTIAL_STATE_MANAGED


def test_get_credential_state_falls_back_to_env(monkeypatch):
    from cauldron_ai_admin.forms import (
        CREDENTIAL_STATE_ENVIRONMENT, get_credential_state,
    )

    class _Store:
        def get_secret(self, provider, key):
            return ""

    monkeypatch.setenv("MY_KEY", "sk-env")
    state = get_credential_state("openai", "api_key", _Store(), "MY_KEY")
    assert state == CREDENTIAL_STATE_ENVIRONMENT


def test_get_credential_state_returns_not_configured(monkeypatch):
    from cauldron_ai_admin.forms import (
        CREDENTIAL_STATE_NOT_CONFIGURED, get_credential_state,
    )

    class _Store:
        def get_secret(self, provider, key):
            return ""

    monkeypatch.delenv("MY_KEY", raising=False)
    state = get_credential_state("openai", "api_key", _Store(), "MY_KEY")
    assert state == CREDENTIAL_STATE_NOT_CONFIGURED


# ---------------------------------------------------------------------------
# Clear-credential checkboxes
# ---------------------------------------------------------------------------

def test_clear_credential_checkbox_added_when_enabled():
    spec = _make_spec(fields=(
        AIProviderConfigurationField(
            name="api_key", label="K", field_type=FIELD_TYPE_PASSWORD,
        ),
    ))
    form = ProviderConfigForm(spec=spec, clear_keys=True)
    assert "clear_api_key" in form.fields


def test_clear_credential_checkbox_absent_when_disabled():
    spec = _make_spec(fields=(
        AIProviderConfigurationField(
            name="api_key", label="K", field_type=FIELD_TYPE_PASSWORD,
        ),
    ))
    form = ProviderConfigForm(spec=spec)
    assert "clear_api_key" not in form.fields


def test_clear_flags_reflects_form_state():
    spec = _make_spec(fields=(
        AIProviderConfigurationField(
            name="api_key", label="K", field_type=FIELD_TYPE_PASSWORD,
        ),
    ))
    form = ProviderConfigForm(
        {"api_key": "", "clear_api_key": "on"},
        spec=spec, clear_keys=True,
    )
    assert form.is_valid(), form.errors
    assert form.clear_flags()["api_key"] is True


# ---------------------------------------------------------------------------
# RuntimeSettingsForm
# ---------------------------------------------------------------------------

def test_runtime_form_defaults_are_valid():
    from cauldron_ai_admin.forms import RuntimeSettingsForm
    form = RuntimeSettingsForm(data={
        "max_model_turns": "8",
        "max_tool_calls": "12",
        "tool_timeout_seconds": "30",
        "run_timeout_seconds": "120",
        "max_argument_bytes": "32768",
        "max_result_bytes": "65536",
        "include_content_tools": "on",
    })
    assert form.is_valid(), form.errors


def test_runtime_form_rejects_tool_timeout_ge_run_timeout():
    from cauldron_ai_admin.forms import RuntimeSettingsForm
    form = RuntimeSettingsForm(data={
        "max_model_turns": "6",
        "max_tool_calls": "10",
        "tool_timeout_seconds": "120",
        "run_timeout_seconds": "60",
        "max_argument_bytes": "32768",
        "max_result_bytes": "65536",
    })
    assert not form.is_valid()
    assert "tool_timeout_seconds" in form.errors


def test_runtime_form_rejects_out_of_range_values():
    from cauldron_ai_admin.forms import RuntimeSettingsForm
    form = RuntimeSettingsForm(data={
        "max_model_turns": "0",  # below min_value=1
        "max_tool_calls": "10",
        "tool_timeout_seconds": "30",
        "run_timeout_seconds": "120",
        "max_argument_bytes": "32768",
        "max_result_bytes": "65536",
    })
    assert not form.is_valid()


def test_runtime_form_include_content_tools_unchecked_defaults_false():
    from cauldron_ai_admin.forms import RuntimeSettingsForm
    form = RuntimeSettingsForm(data={
        "max_model_turns": "6",
        "max_tool_calls": "10",
        "tool_timeout_seconds": "30",
        "run_timeout_seconds": "120",
        "max_argument_bytes": "32768",
        "max_result_bytes": "65536",
        # include_content_tools omitted → False
    })
    assert form.is_valid(), form.errors
    assert form.cleaned_data["include_content_tools"] is False
