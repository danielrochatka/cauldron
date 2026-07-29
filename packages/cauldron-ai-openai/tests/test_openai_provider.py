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
    AIProviderTimeoutError,
)
from cauldron_ai_openai.provider import (
    OpenAIProvider,
    OpenAIProviderFactory,
    _CONFIGURATION_SPEC,
    _DOT_ESCAPE,
    _PROVIDER_NAME,
    _decode_tool_name,
    _encode_tool_name,
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


def test_provider_timeout_raises_provider_timeout_error(monkeypatch):
    import openai
    provider, mock_client = _make_provider(monkeypatch)
    mock_client.responses.create.side_effect = openai.APITimeoutError(
        request=MagicMock(),
    )
    with pytest.raises(AIProviderTimeoutError) as exc_info:
        provider.complete(_simple_request())
    assert "timed out" in str(exc_info.value).lower()


def test_provider_timeout_is_distinct_from_connection_error(monkeypatch):
    """APITimeoutError must map to AIProviderTimeoutError, not AIProviderConnectionError."""
    import openai
    provider, mock_client = _make_provider(monkeypatch)
    mock_client.responses.create.side_effect = openai.APITimeoutError(
        request=MagicMock(),
    )
    with pytest.raises(AIProviderTimeoutError):
        provider.complete(_simple_request())
    # Explicit: connection error must NOT be raised for a timeout.
    mock_client.responses.create.side_effect = openai.APIConnectionError(
        request=MagicMock(),
    )
    with pytest.raises(AIProviderConnectionError):
        provider.complete(_simple_request())


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


def test_provider_rate_limit_carries_http_status(monkeypatch):
    """AIProviderRateLimitError must carry the HTTP status from the SDK exception."""
    import openai
    provider, mock_client = _make_provider(monkeypatch)
    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_client.responses.create.side_effect = openai.RateLimitError(
        "rate limited", response=mock_response, body={},
    )
    with pytest.raises(AIProviderRateLimitError) as exc_info:
        provider.complete(_simple_request())
    assert exc_info.value.http_status == 429


def test_provider_server_error_carries_http_status(monkeypatch):
    """AIProviderResponseError for 5xx must carry the HTTP status."""
    import openai
    provider, mock_client = _make_provider(monkeypatch)
    mock_response = MagicMock()
    mock_response.status_code = 503
    mock_client.responses.create.side_effect = openai.APIStatusError(
        "server error", response=mock_response, body={},
    )
    with pytest.raises(AIProviderResponseError) as exc_info:
        provider.complete(_simple_request())
    assert exc_info.value.http_status == 503


def test_provider_request_id_propagated_to_exception(monkeypatch):
    """The provider request ID from the SDK exception must appear on the raised exception."""
    import openai
    provider, mock_client = _make_provider(monkeypatch)
    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_response.headers = {}
    exc = openai.RateLimitError("limited", response=mock_response, body={})
    exc.request_id = "req-abc123"
    mock_client.responses.create.side_effect = exc
    with pytest.raises(AIProviderRateLimitError) as exc_info:
        provider.complete(_simple_request())
    assert exc_info.value.provider_request_id == "req-abc123"


def test_provider_request_id_not_in_exception_message(monkeypatch):
    """The request ID must not appear in the exception text (only in metadata)."""
    import openai
    provider, mock_client = _make_provider(monkeypatch)
    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_response.headers = {}
    exc = openai.RateLimitError("rate limited", response=mock_response, body={})
    exc.request_id = "req-secret-id"
    mock_client.responses.create.side_effect = exc
    with pytest.raises(AIProviderRateLimitError) as exc_info:
        provider.complete(_simple_request())
    # The request ID must not bleed into the message string.
    assert "req-secret-id" not in str(exc_info.value)
    # But it must appear on the metadata attribute.
    assert exc_info.value.provider_request_id == "req-secret-id"


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


def test_factory_test_connection_timeout_returns_timeout_status(monkeypatch):
    """An OpenAI SDK timeout during test_connection must produce status='timeout', not 'error'."""
    import openai
    mock_client = MagicMock()
    mock_client.responses.create.side_effect = openai.APITimeoutError(
        request=MagicMock(),
    )
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: mock_client)
    factory = OpenAIProviderFactory()
    result = factory.test_connection({"model": "gpt-4o"}, {"api_key": "sk-test"})
    assert result.success is False
    assert result.status == "timeout"
    assert result.latency_ms is not None
    # Message must be credential-safe — no API key or raw SDK text.
    assert "sk-test" not in result.message


# ---------------------------------------------------------------------------
# Legacy model_name compatibility
# ---------------------------------------------------------------------------

def test_factory_build_reads_legacy_model_name(monkeypatch):
    """Factory must accept model_name as a backward-compat fallback."""
    import openai
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: MagicMock())
    factory = OpenAIProviderFactory()
    provider = factory.build({"model_name": "gpt-4o"}, {"api_key": "sk-test"})
    assert isinstance(provider, OpenAIProvider)
    assert provider.model == "gpt-4o"


def test_factory_build_model_wins_over_model_name(monkeypatch):
    """Explicit model field takes precedence over legacy model_name."""
    import openai
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: MagicMock())
    factory = OpenAIProviderFactory()
    provider = factory.build(
        {"model": "gpt-4o", "model_name": "old-model"},
        {"api_key": "sk-test"},
    )
    assert provider.model == "gpt-4o"


def test_factory_build_env_wins_over_empty_model_name(monkeypatch):
    """OPENAI_MODEL env var fills in when both model and model_name are absent."""
    import openai
    monkeypatch.setenv("OPENAI_MODEL", "env-model")
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: MagicMock())
    factory = OpenAIProviderFactory()
    provider = factory.build({}, {"api_key": "sk-test"})
    assert provider.model == "env-model"


# ---------------------------------------------------------------------------
# Response status validation — strict terminal-status enforcement
# ---------------------------------------------------------------------------

def test_provider_completed_text_returns_end_turn(monkeypatch):
    provider, mock_client = _make_provider(monkeypatch)
    mock_client.responses.create.return_value = _make_response(
        output=[_make_output_text_item("Hello")], status="completed",
    )
    response = provider.complete(_simple_request())
    assert response.stop_reason == "end_turn"
    assert response.content == "Hello"


def test_provider_completed_single_tool_call(monkeypatch):
    provider, mock_client = _make_provider(monkeypatch)
    mock_client.responses.create.return_value = _make_response(
        output=[_make_function_call_item(
            call_id="c1", name="my_tool", arguments='{"x": 1}',
        )],
        status="completed",
    )
    response = provider.complete(_simple_request())
    assert response.stop_reason == "tool_use"
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "my_tool"


def test_provider_completed_multiple_tool_calls(monkeypatch):
    provider, mock_client = _make_provider(monkeypatch)
    mock_client.responses.create.return_value = _make_response(
        output=[
            _make_function_call_item(call_id="c1", name="tool_a", arguments="{}"),
            _make_function_call_item(call_id="c2", name="tool_b", arguments="{}"),
        ],
        status="completed",
    )
    response = provider.complete(_simple_request())
    assert response.stop_reason == "tool_use"
    assert len(response.tool_calls) == 2
    names = {tc.name for tc in response.tool_calls}
    assert names == {"tool_a", "tool_b"}


def test_provider_incomplete_content_filter_raises(monkeypatch):
    """Content filtering produces an incomplete response — must raise, not succeed."""
    provider, mock_client = _make_provider(monkeypatch)
    mock_client.responses.create.return_value = _make_response(
        output=[_make_output_text_item("partial")],
        status="incomplete",
        incomplete_reason="content_filter",
    )
    with pytest.raises(AIProviderResponseError):
        provider.complete(_simple_request())


def test_provider_incomplete_missing_reason_raises(monkeypatch):
    """Incomplete with no reason is not a successful response."""
    provider, mock_client = _make_provider(monkeypatch)
    resp = _make_response(
        output=[_make_output_text_item("partial")],
        status="incomplete",
        incomplete_reason=None,
    )
    # Remove incomplete_details entirely so reason is missing
    resp.incomplete_details = None
    mock_client.responses.create.return_value = resp
    with pytest.raises(AIProviderResponseError):
        provider.complete(_simple_request())


def test_provider_failed_status_raises(monkeypatch):
    provider, mock_client = _make_provider(monkeypatch)
    mock_client.responses.create.return_value = _make_response(status="failed")
    with pytest.raises(AIProviderResponseError, match="failed"):
        provider.complete(_simple_request())


def test_provider_cancelled_status_raises(monkeypatch):
    provider, mock_client = _make_provider(monkeypatch)
    mock_client.responses.create.return_value = _make_response(status="cancelled")
    with pytest.raises(AIProviderResponseError, match="cancelled"):
        provider.complete(_simple_request())


def test_provider_queued_status_raises(monkeypatch):
    provider, mock_client = _make_provider(monkeypatch)
    mock_client.responses.create.return_value = _make_response(status="queued")
    with pytest.raises(AIProviderResponseError):
        provider.complete(_simple_request())


def test_provider_in_progress_status_raises(monkeypatch):
    provider, mock_client = _make_provider(monkeypatch)
    mock_client.responses.create.return_value = _make_response(status="in_progress")
    with pytest.raises(AIProviderResponseError):
        provider.complete(_simple_request())


def test_provider_missing_status_raises(monkeypatch):
    """A response with no status attribute at all must raise."""
    provider, mock_client = _make_provider(monkeypatch)
    resp = _make_response()
    del resp.status
    mock_client.responses.create.return_value = resp
    with pytest.raises(AIProviderResponseError):
        provider.complete(_simple_request())


def test_provider_unknown_status_raises(monkeypatch):
    provider, mock_client = _make_provider(monkeypatch)
    mock_client.responses.create.return_value = _make_response(status="superseded")
    with pytest.raises(AIProviderResponseError):
        provider.complete(_simple_request())


def test_provider_partial_text_on_incomplete_is_rejected(monkeypatch):
    """Partial text from a non-max_tokens incomplete must not be returned."""
    provider, mock_client = _make_provider(monkeypatch)
    mock_client.responses.create.return_value = _make_response(
        output=[_make_output_text_item("this text is partial")],
        status="incomplete",
        incomplete_reason="content_filter",
    )
    with pytest.raises(AIProviderResponseError):
        provider.complete(_simple_request())


def test_provider_failed_status_does_not_expose_vendor_details(monkeypatch):
    """Error messages for failed/cancelled responses must be credential-safe."""
    provider, mock_client = _make_provider(monkeypatch)
    # Include a fake secret in the response to confirm it is never echoed.
    resp = _make_response(status="failed")
    resp.error = "Internal error: sk-supersecret exposed"
    mock_client.responses.create.return_value = resp
    with pytest.raises(AIProviderResponseError) as exc_info:
        provider.complete(_simple_request())
    assert "sk-supersecret" not in str(exc_info.value)


def test_provider_incomplete_other_reason_does_not_expose_reason(monkeypatch):
    """The raw incomplete reason string must not appear in the exception."""
    provider, mock_client = _make_provider(monkeypatch)
    mock_client.responses.create.return_value = _make_response(
        status="incomplete",
        incomplete_reason="some_internal_filter_reason_with_secret",
    )
    with pytest.raises(AIProviderResponseError) as exc_info:
        provider.complete(_simple_request())
    assert "some_internal_filter_reason_with_secret" not in str(exc_info.value)


# ---------------------------------------------------------------------------
# Tool name dot-encoding (OpenAI forbids dots in function names)
# ---------------------------------------------------------------------------


def test_encode_tool_name_replaces_dots():
    assert _encode_tool_name("content.list_collections") == f"content{_DOT_ESCAPE}list_collections"


def test_encode_tool_name_multiple_segments():
    assert _encode_tool_name("a.b.c") == f"a{_DOT_ESCAPE}b{_DOT_ESCAPE}c"


def test_encode_tool_name_no_dots_unchanged():
    assert _encode_tool_name("nodots") == "nodots"


def test_decode_tool_name_restores_dots():
    assert _decode_tool_name(f"content{_DOT_ESCAPE}list_collections") == "content.list_collections"


def test_decode_tool_name_roundtrip():
    original = "content.list_collections"
    assert _decode_tool_name(_encode_tool_name(original)) == original


def test_provider_dotted_tool_name_encoded_on_wire(monkeypatch):
    """Dotted tool names are encoded to the OpenAI-safe wire format."""
    provider, mock_client = _make_provider(monkeypatch)
    mock_client.responses.create.return_value = _make_response()
    tool = AIModelToolDefinition(
        name="content.list_collections",
        description="List content collections.",
        parameters={"type": "object", "properties": {}},
    )
    provider.complete(AIModelRequest(
        messages=(AIModelMessage(role="user", content="Q"),),
        tools=(tool,),
    ))
    tools_arg = mock_client.responses.create.call_args[1]["tools"]
    assert len(tools_arg) == 1
    assert tools_arg[0]["name"] == f"content{_DOT_ESCAPE}list_collections"
    assert "." not in tools_arg[0]["name"]


def test_provider_dotted_tool_name_decoded_from_response(monkeypatch):
    """Encoded tool names in function_call responses are decoded back to dotted form."""
    provider, mock_client = _make_provider(monkeypatch)
    encoded_name = f"content{_DOT_ESCAPE}list_collections"
    mock_client.responses.create.return_value = _make_response(
        output=[_make_function_call_item(
            call_id="c1", name=encoded_name, arguments="{}",
        )],
    )
    response = provider.complete(_simple_request())
    assert response.tool_calls[0].name == "content.list_collections"


def test_provider_undotted_name_survives_roundtrip(monkeypatch):
    """A tool name without dots is unchanged across encode/decode."""
    provider, mock_client = _make_provider(monkeypatch)
    mock_client.responses.create.return_value = _make_response(
        output=[_make_function_call_item(
            call_id="c1", name="nodots", arguments="{}",
        )],
    )
    response = provider.complete(_simple_request())
    assert response.tool_calls[0].name == "nodots"


def test_historical_assistant_function_call_name_encoded(monkeypatch):
    """Historical function_call items (from assistant turns) are re-encoded on follow-up requests."""
    provider, mock_client = _make_provider(monkeypatch)
    mock_client.responses.create.return_value = _make_response()
    # Simulate a follow-up request that includes a previous assistant tool call
    # stored with the canonical (dotted) name.
    provider.complete(AIModelRequest(messages=(
        AIModelMessage(role="user", content="List content."),
        AIModelMessage(
            role="assistant", content="",
            tool_calls=(AIModelToolCall(
                id="call-1", name="content.list_collections", arguments={},
            ),),
        ),
        AIModelMessage(
            role="tool", tool_call_id="call-1", content='{"items": []}',
        ),
    )))
    input_items = mock_client.responses.create.call_args[1]["input"]
    # The function_call item must carry the wire-safe encoded name.
    fc_item = next(i for i in input_items if i.get("type") == "function_call")
    assert fc_item["name"] == f"content{_DOT_ESCAPE}list_collections"
    assert "." not in fc_item["name"]
    # The function_call_output must use the same unmodified call_id.
    fco_item = next(i for i in input_items if i.get("type") == "function_call_output")
    assert fco_item["call_id"] == "call-1"


# ---------------------------------------------------------------------------
# Full provider loop: encode → execute → re-encode → final response
# ---------------------------------------------------------------------------


def test_full_single_tool_loop(monkeypatch):
    """Integration: full single-tool request/response cycle through the provider.

    Turn 1: Cauldron sends encoded tool definition.
            OpenAI returns an encoded function_call.
            Provider decodes → AIModelToolCall with canonical dotted name.

    Turn 2: Cauldron re-encodes the historical function_call name.
            Tool output uses the same call_id.
            OpenAI returns final text.
    """
    provider, mock_client = _make_provider(monkeypatch)

    tool = AIModelToolDefinition(
        name="content.list_collections",
        description="List content collections.",
        parameters={"type": "object", "properties": {}},
    )
    encoded_name = f"content{_DOT_ESCAPE}list_collections"

    # --- Turn 1 setup --------------------------------------------------------
    mock_client.responses.create.return_value = _make_response(
        request_id="resp-1",
        output=[_make_function_call_item(
            call_id="call-abc", name=encoded_name, arguments="{}",
        )],
    )
    turn1_req = AIModelRequest(
        messages=(AIModelMessage(role="user", content="List my collections."),),
        tools=(tool,),
    )
    resp1 = provider.complete(turn1_req)

    # Provider decoded the encoded name back to the canonical dotted form.
    assert len(resp1.tool_calls) == 1
    assert resp1.tool_calls[0].name == "content.list_collections"
    assert resp1.tool_calls[0].id == "call-abc"

    # OpenAI received the encoded (dot-free) tool definition.
    tools_sent = mock_client.responses.create.call_args[1]["tools"]
    assert tools_sent[0]["name"] == encoded_name

    # --- Turn 2 setup --------------------------------------------------------
    mock_client.responses.create.return_value = _make_response(
        request_id="resp-2",
        output=[_make_output_text_item("Found 3 collections.")],
    )
    turn2_req = AIModelRequest(
        messages=(
            AIModelMessage(role="user", content="List my collections."),
            AIModelMessage(
                role="assistant", content="",
                tool_calls=(AIModelToolCall(
                    id="call-abc",
                    name="content.list_collections",  # canonical
                    arguments={},
                ),),
            ),
            AIModelMessage(
                role="tool",
                tool_call_id="call-abc",
                content='{"collections": ["pages", "posts", "products"]}',
            ),
        ),
        tools=(tool,),
    )
    resp2 = provider.complete(turn2_req)

    assert resp2.content == "Found 3 collections."
    assert resp2.stop_reason == "end_turn"

    # The historical function_call item must be re-encoded for the follow-up.
    input2 = mock_client.responses.create.call_args[1]["input"]
    fc_items = [i for i in input2 if i.get("type") == "function_call"]
    assert len(fc_items) == 1
    assert fc_items[0]["name"] == encoded_name
    assert fc_items[0]["call_id"] == "call-abc"

    # The tool output item uses the same call_id, unchanged.
    fco_items = [i for i in input2 if i.get("type") == "function_call_output"]
    assert len(fco_items) == 1
    assert fco_items[0]["call_id"] == "call-abc"


def test_full_multiple_tool_turns(monkeypatch):
    """Integration: two sequential tool calls before the final response.

    Turn 1: OpenAI calls content.list_collections.
    Turn 2: OpenAI calls content.list_items.
    Turn 3: OpenAI returns final text.

    All historical function_call names must be re-encoded on each follow-up.
    """
    provider, mock_client = _make_provider(monkeypatch)

    tools = (
        AIModelToolDefinition(
            name="content.list_collections",
            description="List collections.",
            parameters={"type": "object", "properties": {}},
        ),
        AIModelToolDefinition(
            name="content.list_items",
            description="List items in a collection.",
            parameters={"type": "object", "properties": {}},
        ),
    )
    enc_list = f"content{_DOT_ESCAPE}list_collections"
    enc_items = f"content{_DOT_ESCAPE}list_items"

    # Turn 1: model calls list_collections.
    mock_client.responses.create.return_value = _make_response(
        request_id="r1",
        output=[_make_function_call_item(call_id="c1", name=enc_list, arguments="{}")]
    )
    resp1 = provider.complete(AIModelRequest(
        messages=(AIModelMessage(role="user", content="What items are in pages?"),),
        tools=tools,
    ))
    assert resp1.tool_calls[0].name == "content.list_collections"

    # Turn 2: model calls list_items, history includes turn-1 function_call.
    mock_client.responses.create.return_value = _make_response(
        request_id="r2",
        output=[_make_function_call_item(call_id="c2", name=enc_items, arguments='{"collection":"pages"}')]
    )
    resp2 = provider.complete(AIModelRequest(
        messages=(
            AIModelMessage(role="user", content="What items are in pages?"),
            AIModelMessage(
                role="assistant", content="",
                tool_calls=(AIModelToolCall(id="c1", name="content.list_collections", arguments={}),),
            ),
            AIModelMessage(role="tool", tool_call_id="c1", content='["pages","posts"]'),
        ),
        tools=tools,
    ))
    assert resp2.tool_calls[0].name == "content.list_items"

    # Turn-1 historical function_call re-encoded in the follow-up.
    input2 = mock_client.responses.create.call_args[1]["input"]
    fc2 = [i for i in input2 if i.get("type") == "function_call"]
    assert len(fc2) == 1
    assert fc2[0]["name"] == enc_list

    # Turn 3: final text, history includes both tool turns.
    mock_client.responses.create.return_value = _make_response(
        request_id="r3",
        output=[_make_output_text_item("The pages collection has 12 items.")],
    )
    resp3 = provider.complete(AIModelRequest(
        messages=(
            AIModelMessage(role="user", content="What items are in pages?"),
            AIModelMessage(
                role="assistant", content="",
                tool_calls=(AIModelToolCall(id="c1", name="content.list_collections", arguments={}),),
            ),
            AIModelMessage(role="tool", tool_call_id="c1", content='["pages","posts"]'),
            AIModelMessage(
                role="assistant", content="",
                tool_calls=(AIModelToolCall(id="c2", name="content.list_items", arguments={"collection": "pages"}),),
            ),
            AIModelMessage(role="tool", tool_call_id="c2", content='{"items":["home","about"]}'),
        ),
        tools=tools,
    ))
    assert resp3.content == "The pages collection has 12 items."
    assert resp3.stop_reason == "end_turn"

    # Both historical function_call items must be encoded in the final request.
    input3 = mock_client.responses.create.call_args[1]["input"]
    fc3 = [i for i in input3 if i.get("type") == "function_call"]
    assert len(fc3) == 2
    names3 = {i["name"] for i in fc3}
    assert names3 == {enc_list, enc_items}
    assert all("." not in n for n in names3)
