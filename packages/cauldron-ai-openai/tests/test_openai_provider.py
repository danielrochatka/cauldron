"""Unit tests for OpenAIProviderFactory and OpenAIProvider.

These tests never make real network calls.  The OpenAI SDK client is
patched so we can assert the exact Responses API shape Cauldron sends,
and the error-mapping paths never leak vendor-side exception text into
user-visible surfaces.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from cauldron_ai.contracts import (
    AIModelMessage,
    AIModelRequest,
    AIModelToolCall,
    AIModelToolDefinition,
)
from cauldron_ai.provider_configuration import (
    AIProviderAuthenticationError,
    AIProviderConfigurationError,
    AIProviderConnectionError,
    AIProviderRateLimitError,
    AIProviderResponseError,
)
from cauldron_ai_openai.provider import (
    OpenAIProvider,
    OpenAIProviderFactory,
    _CONFIGURATION_SPEC,
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
    assert field.default is None


def test_spec_has_model_field_no_default():
    field = _CONFIGURATION_SPEC.field_by_name("model")
    assert field is not None
    assert field.required is True
    assert field.default is None


def test_spec_has_base_url_field():
    field = _CONFIGURATION_SPEC.field_by_name("base_url")
    assert field is not None
    assert field.advanced is True


def test_spec_does_not_have_organization_field():
    field = _CONFIGURATION_SPEC.field_by_name("organization")
    assert field is None


def test_spec_model_field_has_env_var_hint():
    field = _CONFIGURATION_SPEC.field_by_name("model")
    assert field.environment_variable == "OPENAI_MODEL"


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
        factory.build({"model": "gpt-4o"}, {})


def test_factory_build_raises_with_empty_api_key():
    factory = OpenAIProviderFactory()
    with pytest.raises(AIProviderConfigurationError):
        factory.build({"model": "gpt-4o"}, {"api_key": ""})


def test_factory_build_raises_without_model(monkeypatch):
    import openai
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: MagicMock())
    factory = OpenAIProviderFactory()
    with pytest.raises(AIProviderConfigurationError, match="model"):
        factory.build({}, {"api_key": "sk-test"})


def test_factory_build_succeeds_with_api_key_and_model(monkeypatch):
    import openai
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: MagicMock())
    factory = OpenAIProviderFactory()
    provider = factory.build({"model": "gpt-4o-mini"}, {"api_key": "sk-test"})
    assert isinstance(provider, OpenAIProvider)
    assert provider.name == _PROVIDER_NAME
    assert provider.model == "gpt-4o-mini"


def test_factory_build_reads_api_key_from_env(monkeypatch):
    import openai
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: MagicMock())
    factory = OpenAIProviderFactory()
    provider = factory.build({"model": "gpt-4o"}, {})
    assert isinstance(provider, OpenAIProvider)


def test_factory_build_reads_model_from_env(monkeypatch):
    import openai
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: MagicMock())
    factory = OpenAIProviderFactory()
    provider = factory.build({}, {"api_key": "sk-test"})
    assert provider.model == "gpt-4o-mini"


def test_factory_build_config_wins_over_env(monkeypatch):
    import openai
    monkeypatch.setenv("OPENAI_MODEL", "env-model")
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: MagicMock())
    factory = OpenAIProviderFactory()
    provider = factory.build({"model": "cfg-model"}, {"api_key": "sk-test"})
    assert provider.model == "cfg-model"


# ---------------------------------------------------------------------------
# Responses API — helpers
# ---------------------------------------------------------------------------

def _make_output_text_item(text: str = "Hi!") -> SimpleNamespace:
    return SimpleNamespace(type="output_text", text=text)


def _make_function_call_item(
    *, call_id: str, name: str, arguments: str,
) -> SimpleNamespace:
    return SimpleNamespace(
        type="function_call",
        call_id=call_id,
        name=name,
        arguments=arguments,
    )


def _make_response(
    *,
    output=None,
    request_id: str = "resp-1",
    status: str = "completed",
    incomplete_reason: str | None = None,
    input_tokens: int = 10,
    output_tokens: int = 5,
) -> SimpleNamespace:
    details = None
    if incomplete_reason is not None:
        details = SimpleNamespace(reason=incomplete_reason)
    return SimpleNamespace(
        id=request_id,
        output=output or [_make_output_text_item("Hi!")],
        usage=SimpleNamespace(
            input_tokens=input_tokens, output_tokens=output_tokens,
        ),
        status=status,
        incomplete_details=details,
    )


def _make_provider(monkeypatch, model: str = "gpt-4o"):
    import openai
    mock_client = MagicMock()
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: mock_client)
    provider = OpenAIProvider(model=model, api_key="sk-test")
    return provider, mock_client


def _simple_request(**overrides) -> AIModelRequest:
    return AIModelRequest(
        messages=(AIModelMessage(role="user", content="Hello"),),
        **overrides,
    )


# ---------------------------------------------------------------------------
# Provider complete() — uses the Responses API
# ---------------------------------------------------------------------------

def test_provider_uses_responses_api(monkeypatch):
    provider, mock_client = _make_provider(monkeypatch)
    mock_client.responses.create.return_value = _make_response()
    provider.complete(_simple_request())
    assert mock_client.responses.create.called
    # chat.completions must NOT be invoked at all.
    assert not mock_client.chat.completions.create.called


def test_provider_sends_store_false(monkeypatch):
    provider, mock_client = _make_provider(monkeypatch)
    mock_client.responses.create.return_value = _make_response()
    provider.complete(_simple_request())
    kwargs = mock_client.responses.create.call_args[1]
    assert kwargs["store"] is False


def test_provider_sends_max_output_tokens(monkeypatch):
    provider, mock_client = _make_provider(monkeypatch)
    mock_client.responses.create.return_value = _make_response()
    provider.complete(_simple_request(max_tokens=512))
    kwargs = mock_client.responses.create.call_args[1]
    assert kwargs["max_output_tokens"] == 512


def test_provider_sends_instructions(monkeypatch):
    provider, mock_client = _make_provider(monkeypatch)
    mock_client.responses.create.return_value = _make_response()
    provider.complete(_simple_request(system="Be brief."))
    kwargs = mock_client.responses.create.call_args[1]
    assert kwargs["instructions"] == "Be brief."


def test_provider_input_items_user_role(monkeypatch):
    provider, mock_client = _make_provider(monkeypatch)
    mock_client.responses.create.return_value = _make_response()
    provider.complete(_simple_request())
    input_items = mock_client.responses.create.call_args[1]["input"]
    assert input_items == [{"role": "user", "content": "Hello"}]


def test_provider_input_items_assistant_output_text(monkeypatch):
    provider, mock_client = _make_provider(monkeypatch)
    mock_client.responses.create.return_value = _make_response()
    provider.complete(AIModelRequest(messages=(
        AIModelMessage(role="user", content="Q"),
        AIModelMessage(role="assistant", content="A"),
    )))
    input_items = mock_client.responses.create.call_args[1]["input"]
    assert input_items[1] == {
        "role": "assistant",
        "content": [{"type": "output_text", "text": "A"}],
    }


def test_provider_input_items_assistant_tool_call(monkeypatch):
    provider, mock_client = _make_provider(monkeypatch)
    mock_client.responses.create.return_value = _make_response()
    provider.complete(AIModelRequest(messages=(
        AIModelMessage(role="user", content="Q"),
        AIModelMessage(
            role="assistant", content="",
            tool_calls=(AIModelToolCall(
                id="call-1", name="my_tool", arguments={"x": 1},
            ),),
        ),
        AIModelMessage(
            role="tool", tool_call_id="call-1", content='{"ok": true}',
        ),
    )))
    input_items = mock_client.responses.create.call_args[1]["input"]
    assert input_items[1]["type"] == "function_call"
    assert input_items[1]["call_id"] == "call-1"
    assert input_items[1]["name"] == "my_tool"
    assert json.loads(input_items[1]["arguments"]) == {"x": 1}
    assert input_items[2] == {
        "type": "function_call_output",
        "call_id": "call-1",
        "output": '{"ok": true}',
    }


def test_provider_extracts_output_text(monkeypatch):
    provider, mock_client = _make_provider(monkeypatch)
    mock_client.responses.create.return_value = _make_response(
        output=[_make_output_text_item("hello world")],
    )
    response = provider.complete(_simple_request())
    assert response.content == "hello world"
    assert response.stop_reason == "end_turn"
    assert response.provider_request_id == "resp-1"


def test_provider_extracts_function_call(monkeypatch):
    provider, mock_client = _make_provider(monkeypatch)
    mock_client.responses.create.return_value = _make_response(
        output=[_make_function_call_item(
            call_id="call-42", name="do_thing", arguments='{"a": 1}',
        )],
    )
    response = provider.complete(_simple_request())
    assert response.stop_reason == "tool_use"
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].id == "call-42"
    assert response.tool_calls[0].name == "do_thing"
    assert response.tool_calls[0].arguments["a"] == 1


def test_provider_maps_max_tokens(monkeypatch):
    provider, mock_client = _make_provider(monkeypatch)
    mock_client.responses.create.return_value = _make_response(
        status="incomplete", incomplete_reason="max_output_tokens",
    )
    response = provider.complete(_simple_request())
    assert response.stop_reason == "max_tokens"


def test_provider_reports_token_usage(monkeypatch):
    provider, mock_client = _make_provider(monkeypatch)
    mock_client.responses.create.return_value = _make_response(
        input_tokens=123, output_tokens=45,
    )
    response = provider.complete(_simple_request())
    assert response.input_tokens == 123
    assert response.output_tokens == 45


def test_provider_tool_definitions_use_function_shape(monkeypatch):
    provider, mock_client = _make_provider(monkeypatch)
    mock_client.responses.create.return_value = _make_response()
    tool = AIModelToolDefinition(
        name="do_thing",
        description="does a thing",
        parameters={"type": "object", "properties": {}},
    )
    provider.complete(AIModelRequest(
        messages=(AIModelMessage(role="user", content="Q"),),
        tools=(tool,),
    ))
    tools_arg = mock_client.responses.create.call_args[1]["tools"]
    assert tools_arg == [{
        "type": "function",
        "name": "do_thing",
        "description": "does a thing",
        "parameters": {"type": "object", "properties": {}},
        "strict": False,
    }]


# ---------------------------------------------------------------------------
# Provider complete() — malformed tool arguments
# ---------------------------------------------------------------------------

def test_provider_malformed_json_arguments_raises(monkeypatch):
    provider, mock_client = _make_provider(monkeypatch)
    mock_client.responses.create.return_value = _make_response(
        output=[_make_function_call_item(
            call_id="c", name="x", arguments="{invalid json",
        )],
    )
    with pytest.raises(AIProviderResponseError, match="invalid JSON"):
        provider.complete(_simple_request())


def test_provider_non_object_arguments_raises(monkeypatch):
    provider, mock_client = _make_provider(monkeypatch)
    mock_client.responses.create.return_value = _make_response(
        output=[_make_function_call_item(
            call_id="c", name="x", arguments='["a", "b"]',
        )],
    )
    with pytest.raises(AIProviderResponseError, match="non-object"):
        provider.complete(_simple_request())


# ---------------------------------------------------------------------------
# Provider complete() — credential-safe error mapping
# ---------------------------------------------------------------------------

def test_provider_auth_error_message_is_safe(monkeypatch):
    import openai
    provider, mock_client = _make_provider(monkeypatch)
    # The SDK exception often contains the API key in str(exc); the
    # provider must NOT let that leak into the raised message.
    mock_client.responses.create.side_effect = openai.AuthenticationError(
        "invalid_api_key sk-supersecret", response=MagicMock(), body={},
    )
    with pytest.raises(AIProviderAuthenticationError) as exc_info:
        provider.complete(_simple_request())
    assert "sk-supersecret" not in str(exc_info.value)
    assert "OpenAI rejected the API key" in str(exc_info.value)


def test_provider_rate_limit_message_is_safe(monkeypatch):
    import openai
    provider, mock_client = _make_provider(monkeypatch)
    mock_client.responses.create.side_effect = openai.RateLimitError(
        "org-abc exceeded quota", response=MagicMock(), body={},
    )
    with pytest.raises(AIProviderRateLimitError) as exc_info:
        provider.complete(_simple_request())
    assert "org-abc" not in str(exc_info.value)


def test_provider_timeout_message_is_safe(monkeypatch):
    import openai
    provider, mock_client = _make_provider(monkeypatch)
    mock_client.responses.create.side_effect = openai.APITimeoutError(
        request=MagicMock(),
    )
    with pytest.raises(AIProviderConnectionError) as exc_info:
        provider.complete(_simple_request())
    assert "timed out" in str(exc_info.value).lower()


def test_provider_connection_error_message_is_safe(monkeypatch):
    import openai
    provider, mock_client = _make_provider(monkeypatch)
    mock_client.responses.create.side_effect = openai.APIConnectionError(
        request=MagicMock(),
    )
    with pytest.raises(AIProviderConnectionError):
        provider.complete(_simple_request())


def test_provider_bad_request_error_message_is_safe(monkeypatch):
    import openai
    provider, mock_client = _make_provider(monkeypatch)
    mock_client.responses.create.side_effect = openai.BadRequestError(
        "malformed prompt org-xxx", response=MagicMock(), body={},
    )
    with pytest.raises(AIProviderResponseError) as exc_info:
        provider.complete(_simple_request())
    assert "org-xxx" not in str(exc_info.value)


def test_provider_empty_api_key_raises_at_init(monkeypatch):
    import openai
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: MagicMock())
    with pytest.raises(AIProviderConfigurationError):
        OpenAIProvider(api_key="", model="gpt-4o")


def test_provider_empty_model_raises_at_init(monkeypatch):
    import openai
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: MagicMock())
    with pytest.raises(AIProviderConfigurationError):
        OpenAIProvider(api_key="sk-test", model="")


def test_provider_does_not_set_retries_on_client(monkeypatch):
    import openai
    seen_kwargs: dict = {}

    def _capture(**kwargs):
        seen_kwargs.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(openai, "OpenAI", _capture)
    OpenAIProvider(api_key="sk-test", model="gpt-4o")
    assert "retries" not in seen_kwargs


def test_openai_client_constructed_with_max_retries_zero(monkeypatch):
    """Cauldron must own retry policy — the SDK's default 2 retries is off."""
    import openai
    captured: dict = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(openai, "OpenAI", _capture)
    OpenAIProvider(api_key="sk-test", model="gpt-4o")
    assert captured.get("max_retries") == 0


def test_openai_factory_build_passes_max_retries_zero(monkeypatch):
    """The same guarantee holds when the provider is built via the factory."""
    import openai
    captured: dict = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(openai, "OpenAI", _capture)
    OpenAIProviderFactory().build(
        {"model": "gpt-4o"}, {"api_key": "sk-test"},
    )
    assert captured.get("max_retries") == 0


def test_openai_client_with_base_url_still_sets_max_retries_zero(monkeypatch):
    """base_url must not disturb the max_retries=0 guarantee."""
    import openai
    captured: dict = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(openai, "OpenAI", _capture)
    OpenAIProvider(
        api_key="sk-test", model="gpt-4o", base_url="https://example.com/v1",
    )
    assert captured.get("max_retries") == 0
    assert captured.get("base_url") == "https://example.com/v1"


# ---------------------------------------------------------------------------
# Factory test_connection
# ---------------------------------------------------------------------------

def test_factory_test_connection_success(monkeypatch):
    import openai
    mock_client = MagicMock()
    mock_client.responses.create.return_value = _make_response()
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: mock_client)
    factory = OpenAIProviderFactory()
    result = factory.test_connection(
        {"model": "gpt-4o"}, {"api_key": "sk-test"},
    )
    assert result.success is True
    assert result.status == "ok"
    assert result.latency_ms is not None


def test_factory_test_connection_auth_error_returns_safe_failure(monkeypatch):
    import openai
    mock_client = MagicMock()
    mock_client.responses.create.side_effect = openai.AuthenticationError(
        "sk-verybad", response=MagicMock(), body={},
    )
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: mock_client)
    factory = OpenAIProviderFactory()
    result = factory.test_connection(
        {"model": "gpt-4o"}, {"api_key": "sk-bad"},
    )
    assert result.success is False
    assert result.status == "authentication_error"
    assert "sk-verybad" not in result.message
    assert "sk-bad" not in result.message


def test_factory_test_connection_no_api_key_returns_config_error():
    factory = OpenAIProviderFactory()
    result = factory.test_connection({"model": "gpt-4o"}, {})
    assert result.success is False
    assert result.status == "configuration_error"


def test_factory_test_connection_no_model_returns_config_error():
    factory = OpenAIProviderFactory()
    result = factory.test_connection({}, {"api_key": "sk-test"})
    assert result.success is False
    assert result.status == "configuration_error"
