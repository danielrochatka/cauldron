"""Tests for provider_configuration contracts."""
from __future__ import annotations

import pytest

from cauldron_ai.provider_configuration import (
    FIELD_TYPE_BOOLEAN,
    FIELD_TYPE_INTEGER,
    FIELD_TYPE_PASSWORD,
    FIELD_TYPE_TEXT,
    FIELD_TYPE_URL,
    AIModelProviderFactory,
    AIProviderAuthenticationError,
    AIProviderConfigurationError,
    AIProviderConfigurationField,
    AIProviderConfigurationSpec,
    AIProviderConnectionError,
    AIProviderConnectionResult,
    AIProviderRateLimitError,
    AIProviderResponseError,
)


# ---------------------------------------------------------------------------
# AIProviderConfigurationField
# ---------------------------------------------------------------------------

def test_field_minimal():
    f = AIProviderConfigurationField(name="api_key", label="API Key", field_type=FIELD_TYPE_PASSWORD)
    assert f.name == "api_key"
    assert f.field_type == FIELD_TYPE_PASSWORD
    assert f.required is False
    assert f.advanced is False


def test_field_full():
    f = AIProviderConfigurationField(
        name="model_name",
        label="Model",
        field_type=FIELD_TYPE_TEXT,
        required=False,
        default="gpt-4o",
        help_text="Which model to use.",
        max_length=128,
        environment_variable="OPENAI_MODEL",
        advanced=True,
    )
    assert f.default == "gpt-4o"
    assert f.environment_variable == "OPENAI_MODEL"
    assert f.advanced is True


def test_field_empty_name_raises():
    with pytest.raises(ValueError, match="non-empty"):
        AIProviderConfigurationField(name="", label="L")


def test_field_invalid_type_raises():
    with pytest.raises(ValueError, match="field_type"):
        AIProviderConfigurationField(name="x", label="X", field_type="color")


def test_field_negative_max_length_raises():
    with pytest.raises(ValueError, match="max_length"):
        AIProviderConfigurationField(name="x", label="X", max_length=0)


def test_field_max_length_none_ok():
    f = AIProviderConfigurationField(name="x", label="X", max_length=None)
    assert f.max_length is None


def test_field_all_types_accepted():
    for ft in (FIELD_TYPE_TEXT, FIELD_TYPE_PASSWORD, FIELD_TYPE_INTEGER, FIELD_TYPE_BOOLEAN, FIELD_TYPE_URL):
        f = AIProviderConfigurationField(name="f", label="F", field_type=ft)
        assert f.field_type == ft


def test_field_is_frozen():
    f = AIProviderConfigurationField(name="x", label="X")
    with pytest.raises((AttributeError, TypeError)):
        f.name = "y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# AIProviderConfigurationSpec
# ---------------------------------------------------------------------------

def test_spec_minimal():
    spec = AIProviderConfigurationSpec(provider_name="openai", display_name="OpenAI")
    assert spec.provider_name == "openai"
    assert spec.fields == ()
    assert spec.supports_connection_test is False


def test_spec_with_fields():
    f1 = AIProviderConfigurationField(name="api_key", label="API Key", field_type=FIELD_TYPE_PASSWORD)
    f2 = AIProviderConfigurationField(name="model", label="Model")
    spec = AIProviderConfigurationSpec(
        provider_name="openai",
        display_name="OpenAI",
        fields=(f1, f2),
        supports_connection_test=True,
    )
    assert len(spec.fields) == 2
    assert spec.supports_connection_test is True


def test_spec_empty_provider_name_raises():
    with pytest.raises(ValueError, match="provider_name"):
        AIProviderConfigurationSpec(provider_name="", display_name="X")


def test_spec_empty_display_name_raises():
    with pytest.raises(ValueError, match="display_name"):
        AIProviderConfigurationSpec(provider_name="x", display_name="")


def test_spec_fields_must_be_tuple():
    with pytest.raises(TypeError, match="tuple"):
        AIProviderConfigurationSpec(
            provider_name="x",
            display_name="X",
            fields=[AIProviderConfigurationField(name="a", label="A")],  # type: ignore[arg-type]
        )


def test_spec_duplicate_field_names_raises():
    f = AIProviderConfigurationField(name="dup", label="Dup")
    with pytest.raises(ValueError, match="duplicate"):
        AIProviderConfigurationSpec(
            provider_name="x", display_name="X", fields=(f, f)
        )


def test_spec_field_by_name():
    f = AIProviderConfigurationField(name="api_key", label="API Key")
    spec = AIProviderConfigurationSpec(
        provider_name="x", display_name="X", fields=(f,)
    )
    assert spec.field_by_name("api_key") is f
    assert spec.field_by_name("missing") is None


def test_spec_is_frozen():
    spec = AIProviderConfigurationSpec(provider_name="x", display_name="X")
    with pytest.raises((AttributeError, TypeError)):
        spec.provider_name = "y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# AIProviderConnectionResult
# ---------------------------------------------------------------------------

def test_connection_result_success():
    r = AIProviderConnectionResult(success=True, status="ok", message="Connected.", latency_ms=42.5)
    assert r.success is True
    assert r.latency_ms == 42.5


def test_connection_result_failure():
    r = AIProviderConnectionResult(success=False, status="auth_error", message="Bad key.")
    assert r.success is False
    assert r.provider_request_id == ""


def test_connection_result_empty_status_raises():
    with pytest.raises(ValueError, match="status"):
        AIProviderConnectionResult(success=True, status="")


def test_connection_result_non_bool_raises():
    with pytest.raises(TypeError, match="bool"):
        AIProviderConnectionResult(success=1, status="ok")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# AIModelProviderFactory protocol check
# ---------------------------------------------------------------------------

def test_factory_protocol_structural():
    class _Factory:
        name = "fake"

        @property
        def configuration_spec(self):
            return AIProviderConfigurationSpec(provider_name="fake", display_name="Fake")

        def build(self, config, secrets):
            return object()

        def test_connection(self, config, secrets):
            return AIProviderConnectionResult(success=True, status="ok")

    assert isinstance(_Factory(), AIModelProviderFactory)


def test_missing_build_not_factory():
    class _NoBuild:
        name = "fake"

        @property
        def configuration_spec(self):
            return None

        def test_connection(self, config, secrets):
            return None

    assert not isinstance(_NoBuild(), AIModelProviderFactory)


# ---------------------------------------------------------------------------
# Provider-neutral exceptions
# ---------------------------------------------------------------------------

def test_exceptions_are_runtime_errors():
    for cls in (
        AIProviderConfigurationError,
        AIProviderAuthenticationError,
        AIProviderConnectionError,
        AIProviderRateLimitError,
        AIProviderResponseError,
    ):
        exc = cls("msg")
        assert isinstance(exc, RuntimeError)
        assert str(exc) == "msg"


def test_exceptions_hierarchy():
    from cauldron_ai.provider_configuration import AIProviderError
    for cls in (
        AIProviderConfigurationError,
        AIProviderAuthenticationError,
        AIProviderConnectionError,
        AIProviderRateLimitError,
        AIProviderResponseError,
    ):
        assert issubclass(cls, AIProviderError)
