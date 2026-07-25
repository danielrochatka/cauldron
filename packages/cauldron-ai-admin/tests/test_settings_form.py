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
