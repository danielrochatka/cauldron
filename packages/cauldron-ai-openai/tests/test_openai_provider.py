"""Unit tests for OpenAIProviderFactory and OpenAIProvider.

These tests never make real network calls.  They patch the openai client
to validate mapping, error handling, and configuration contracts.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from cauldron_ai.contracts import AIModelMessage, AIModelRequest
from cauldron_ai.provider_configuration import (
    AIProviderAuthenticationError,
    AIProviderConfigurationError,
    AIProviderConnectionError,
    AIProviderRateLimitError,
)
from cauldron_ai_openai.provider import (
    OpenAIProvider,
    OpenAIProviderFactory,
    _CONFIGURATION_SPEC,
    _DEFAULT_MODEL,
    _PROVIDER_NAME,
)


# ---------------------------------------------------------------------------
# Configuration spec
# ---------------------------------------------------------------------------

def test_spec_provider_name():
    assert _CONFIGURATION_SPEC.provider_name == _PROVIDER_NAME


def test_spec_supports_connection_test():
    assert _CONFIGURATION_SPEC.supports_connection_test is True


def test_spec_has_api_key_field():
    field = _CONFIGURATION_SPEC.field_by_name("api_key")
    assert field is not None
    assert field.field_type == "password"
    assert field.required is True


def test_spec_has_model_name_field():
    field = _CONFIGURATION_SPEC.field_by_name("model_name")
    assert field is not None
    assert field.default == _DEFAULT_MODEL


def test_spec_has_base_url_field():
    field = _CONFIGURATION_SPEC.field_by_name("base_url")
    assert field is not None
    assert field.advanced is True


def test_spec_has_organization_field():
    field = _CONFIGURATION_SPEC.field_by_name("organization")
    assert field is not None
    assert field.advanced is True


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def test_factory_name():
    assert OpenAIProviderFactory().name == _PROVIDER_NAME


def test_factory_configuration_spec():
    spec = OpenAIProviderFactory().configuration_spec
    assert spec.provider_name == _PROVIDER_NAME


def test_factory_build_raises_without_api_key():
    factory = OpenAIProviderFactory()
    with pytest.raises(AIProviderConfigurationError, match="api_key"):
        factory.build({}, {})


def test_factory_build_raises_with_empty_api_key():
    factory = OpenAIProviderFactory()
    with pytest.raises(AIProviderConfigurationError):
        factory.build({}, {"api_key": ""})


def test_factory_build_succeeds_with_api_key(monkeypatch):
    import openai
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: MagicMock())
    factory = OpenAIProviderFactory()
    provider = factory.build({}, {"api_key": "sk-test"})
    assert isinstance(provider, OpenAIProvider)
    assert provider.name == _PROVIDER_NAME


def test_factory_build_uses_custom_model(monkeypatch):
    import openai
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: MagicMock())
    factory = OpenAIProviderFactory()
    provider = factory.build({"model_name": "gpt-4o-mini"}, {"api_key": "sk-test"})
    assert provider._model_name == "gpt-4o-mini"


def test_factory_build_defaults_to_default_model(monkeypatch):
    import openai
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: MagicMock())
    factory = OpenAIProviderFactory()
    provider = factory.build({}, {"api_key": "sk-test"})
    assert provider._model_name == _DEFAULT_MODEL


def test_factory_build_reads_api_key_from_env(monkeypatch):
    import openai
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: MagicMock())
    factory = OpenAIProviderFactory()
    provider = factory.build({}, {})
    assert isinstance(provider, OpenAIProvider)


# ---------------------------------------------------------------------------
# Provider complete()
# ---------------------------------------------------------------------------

def _make_chat_response(content="Hello!", finish_reason="stop", tool_calls=None, request_id="req-1"):
    msg = SimpleNamespace(
        content=content,
        tool_calls=tool_calls,
    )
    choice = SimpleNamespace(message=msg, finish_reason=finish_reason)
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
    return SimpleNamespace(id=request_id, choices=[choice], usage=usage)


def _make_provider(monkeypatch, model="gpt-4o"):
    import openai
    mock_client = MagicMock()
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: mock_client)
    provider = OpenAIProvider(model_name=model, api_key="sk-test")
    return provider, mock_client


def _simple_request():
    return AIModelRequest(
        messages=(AIModelMessage(role="user", content="Hello"),),
    )


def test_provider_complete_returns_response(monkeypatch):
    provider, mock_client = _make_provider(monkeypatch)
    mock_client.chat.completions.create.return_value = _make_chat_response("Hi!")
    response = provider.complete(_simple_request())
    assert response.content == "Hi!"
    assert response.stop_reason == "end_turn"
    assert response.provider_request_id == "req-1"


def test_provider_complete_maps_stop_to_end_turn(monkeypatch):
    provider, mock_client = _make_provider(monkeypatch)
    mock_client.chat.completions.create.return_value = _make_chat_response(finish_reason="stop")
    response = provider.complete(_simple_request())
    assert response.stop_reason == "end_turn"


def test_provider_complete_maps_tool_calls_to_tool_use(monkeypatch):
    provider, mock_client = _make_provider(monkeypatch)
    tc = SimpleNamespace(
        id="tc-1",
        function=SimpleNamespace(name="my_tool", arguments='{"x": 1}'),
    )
    mock_client.chat.completions.create.return_value = _make_chat_response(
        content="", finish_reason="tool_calls", tool_calls=[tc]
    )
    response = provider.complete(_simple_request())
    assert response.stop_reason == "tool_use"
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "my_tool"
    assert response.tool_calls[0].arguments["x"] == 1


def test_provider_complete_maps_length_to_max_tokens(monkeypatch):
    provider, mock_client = _make_provider(monkeypatch)
    mock_client.chat.completions.create.return_value = _make_chat_response(finish_reason="length")
    response = provider.complete(_simple_request())
    assert response.stop_reason == "max_tokens"


def test_provider_complete_includes_token_counts(monkeypatch):
    provider, mock_client = _make_provider(monkeypatch)
    mock_client.chat.completions.create.return_value = _make_chat_response()
    response = provider.complete(_simple_request())
    assert response.input_tokens == 10
    assert response.output_tokens == 5


def test_provider_complete_store_false(monkeypatch):
    provider, mock_client = _make_provider(monkeypatch)
    mock_client.chat.completions.create.return_value = _make_chat_response()
    provider.complete(_simple_request())
    call_kwargs = mock_client.chat.completions.create.call_args[1]
    assert call_kwargs.get("store") is False


def test_provider_raises_auth_error(monkeypatch):
    import openai
    provider, mock_client = _make_provider(monkeypatch)
    mock_client.chat.completions.create.side_effect = openai.AuthenticationError(
        "invalid key", response=MagicMock(), body={}
    )
    with pytest.raises(AIProviderAuthenticationError):
        provider.complete(_simple_request())


def test_provider_raises_rate_limit_error(monkeypatch):
    import openai
    provider, mock_client = _make_provider(monkeypatch)
    mock_client.chat.completions.create.side_effect = openai.RateLimitError(
        "rate limit", response=MagicMock(), body={}
    )
    with pytest.raises(AIProviderRateLimitError):
        provider.complete(_simple_request())


def test_provider_raises_connection_error(monkeypatch):
    import openai
    provider, mock_client = _make_provider(monkeypatch)
    mock_client.chat.completions.create.side_effect = openai.APIConnectionError(
        request=MagicMock()
    )
    with pytest.raises(AIProviderConnectionError):
        provider.complete(_simple_request())


def test_provider_empty_api_key_raises_at_init(monkeypatch):
    import openai
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: MagicMock())
    with pytest.raises(AIProviderConfigurationError):
        OpenAIProvider(api_key="")


# ---------------------------------------------------------------------------
# Factory test_connection
# ---------------------------------------------------------------------------

def test_factory_test_connection_success(monkeypatch):
    import openai
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _make_chat_response("OK")
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: mock_client)
    factory = OpenAIProviderFactory()
    result = factory.test_connection({}, {"api_key": "sk-test"})
    assert result.success is True
    assert result.status == "ok"
    assert result.latency_ms is not None


def test_factory_test_connection_auth_error_returns_failure(monkeypatch):
    import openai
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = openai.AuthenticationError(
        "bad key", response=MagicMock(), body={}
    )
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: mock_client)
    factory = OpenAIProviderFactory()
    result = factory.test_connection({}, {"api_key": "sk-bad"})
    assert result.success is False
    assert result.status == "authentication_error"


def test_factory_test_connection_no_api_key_returns_config_error():
    factory = OpenAIProviderFactory()
    result = factory.test_connection({}, {})
    assert result.success is False
    assert result.status == "configuration_error"
