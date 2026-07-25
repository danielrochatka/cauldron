"""OpenAI provider adapter for Cauldron Admin AI.

Uses the OpenAI Responses API (``openai.responses.create``) with
``store=False`` so no conversation data is retained on OpenAI's servers.

The provider is constructed by ``OpenAIProviderFactory.build()``; it
should never be instantiated directly by application code.
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from cauldron_ai.contracts import (
    AIModelMessage,
    AIModelRequest,
    AIModelResponse,
    AIModelToolCall,
)
from cauldron_ai.provider_configuration import (
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

_PROVIDER_NAME = "openai"
_DEFAULT_MODEL = "gpt-4o"
_TEST_MODEL = "gpt-4o-mini"
_MAX_TOKENS_TEST = 16


_CONFIGURATION_SPEC = AIProviderConfigurationSpec(
    provider_name=_PROVIDER_NAME,
    display_name="OpenAI",
    version="1.0",
    description=(
        "Connects to OpenAI using the Responses API. "
        "Requires an API key from platform.openai.com."
    ),
    supports_connection_test=True,
    fields=(
        AIProviderConfigurationField(
            name="model_name",
            label="Model",
            field_type=FIELD_TYPE_TEXT,
            required=False,
            default=_DEFAULT_MODEL,
            help_text=(
                "OpenAI model to use (e.g. gpt-4o, gpt-4o-mini, o3). "
                "Defaults to gpt-4o."
            ),
            max_length=128,
        ),
        AIProviderConfigurationField(
            name="api_key",
            label="API Key",
            field_type=FIELD_TYPE_PASSWORD,
            required=True,
            default=None,
            help_text=(
                "Your OpenAI API key (sk-…). "
                "Can also be set via the OPENAI_API_KEY environment variable."
            ),
            max_length=256,
            environment_variable="OPENAI_API_KEY",
        ),
        AIProviderConfigurationField(
            name="base_url",
            label="API Base URL",
            field_type=FIELD_TYPE_URL,
            required=False,
            default=None,
            help_text=(
                "Override the OpenAI API base URL. Leave blank for the default. "
                "Useful for OpenAI-compatible endpoints."
            ),
            max_length=512,
            advanced=True,
        ),
        AIProviderConfigurationField(
            name="organization",
            label="Organization ID",
            field_type=FIELD_TYPE_TEXT,
            required=False,
            default=None,
            help_text="OpenAI organization ID (optional).",
            max_length=256,
            advanced=True,
        ),
    ),
)


def _build_openai_client(api_key: str, base_url: str | None, organization: str | None):
    try:
        import openai
    except ImportError as exc:  # pragma: no cover
        raise AIProviderConfigurationError(
            "The 'openai' package is not installed. "
            "Install cauldron-ai-openai to use the OpenAI provider."
        ) from exc
    kwargs: dict[str, Any] = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    if organization:
        kwargs["organization"] = organization
    return openai.OpenAI(**kwargs)


def _messages_to_openai(messages: tuple[AIModelMessage, ...]) -> list[dict]:
    result = []
    for msg in messages:
        if msg.role == "system":
            continue  # passed via system parameter at the top level
        if msg.role == "tool":
            result.append({
                "role": "tool",
                "tool_call_id": msg.tool_call_id,
                "content": msg.content,
            })
        elif msg.role == "assistant" and msg.tool_calls:
            result.append({
                "role": "assistant",
                "content": msg.content or None,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": _args_to_json(tc.arguments),
                        },
                    }
                    for tc in msg.tool_calls
                ],
            })
        else:
            result.append({"role": msg.role, "content": msg.content})
    return result


def _args_to_json(args: Any) -> str:
    import json
    from types import MappingProxyType
    def _plain(v: Any) -> Any:
        if isinstance(v, MappingProxyType):
            return {k: _plain(vv) for k, vv in v.items()}
        if isinstance(v, tuple):
            return [_plain(vv) for vv in v]
        return v
    return json.dumps(_plain(args))


def _tools_to_openai(tools: tuple) -> list[dict]:
    result = []
    for t in tools:
        from types import MappingProxyType
        def _plain(v: Any) -> Any:
            if isinstance(v, MappingProxyType):
                return {k: _plain(vv) for k, vv in v.items()}
            if isinstance(v, tuple):
                return [_plain(vv) for vv in v]
            return v
        result.append({
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": _plain(t.parameters),
            },
        })
    return result


def _map_stop_reason(finish_reason: str | None) -> str:
    if finish_reason == "stop":
        return "end_turn"
    if finish_reason == "tool_calls":
        return "tool_use"
    if finish_reason == "length":
        return "max_tokens"
    return "end_turn"


class OpenAIProvider:
    """Live provider built by ``OpenAIProviderFactory``."""

    name = _PROVIDER_NAME
    display_name = "OpenAI"

    def __init__(
        self,
        *,
        model_name: str = _DEFAULT_MODEL,
        api_key: str,
        base_url: str | None = None,
        organization: str | None = None,
    ) -> None:
        if not api_key:
            raise AIProviderConfigurationError(
                "OpenAI provider requires a non-empty api_key."
            )
        self._model_name = model_name or _DEFAULT_MODEL
        self._client = _build_openai_client(api_key, base_url, organization)

    def complete(self, request: AIModelRequest) -> AIModelResponse:
        import openai

        system_text = request.system or ""
        # Extract system messages from the message list and prepend them.
        for m in request.messages:
            if m.role == "system" and m.content:
                system_text = m.content
                break

        messages = _messages_to_openai(request.messages)
        if system_text and (not messages or messages[0].get("role") != "system"):
            messages.insert(0, {"role": "system", "content": system_text})

        kwargs: dict[str, Any] = {
            "model": self._model_name,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "store": False,
        }
        if request.tools:
            kwargs["tools"] = _tools_to_openai(request.tools)
            kwargs["tool_choice"] = "auto"

        try:
            response = self._client.chat.completions.create(**kwargs)
        except openai.AuthenticationError as exc:
            raise AIProviderAuthenticationError(
                f"OpenAI rejected credentials: {exc}"
            ) from exc
        except openai.RateLimitError as exc:
            raise AIProviderRateLimitError(
                f"OpenAI rate limit: {exc}"
            ) from exc
        except openai.APIConnectionError as exc:
            raise AIProviderConnectionError(
                f"OpenAI connection error: {exc}"
            ) from exc
        except Exception as exc:
            raise AIProviderResponseError(
                f"OpenAI unexpected error: {type(exc).__name__}: {exc}"
            ) from exc

        choice = response.choices[0] if response.choices else None
        if choice is None:
            raise AIProviderResponseError("OpenAI returned no choices")

        content = ""
        tool_calls: list[AIModelToolCall] = []

        msg = choice.message
        if msg.content:
            content = msg.content
        if msg.tool_calls:
            import json as _json
            for tc in msg.tool_calls:
                try:
                    args = _json.loads(tc.function.arguments or "{}")
                except Exception:
                    args = {}
                tool_calls.append(AIModelToolCall(
                    id=tc.id or str(uuid.uuid4()),
                    name=tc.function.name,
                    arguments=args,
                ))

        usage = response.usage or None
        input_tokens = usage.prompt_tokens if usage else 0
        output_tokens = usage.completion_tokens if usage else 0

        return AIModelResponse(
            provider_request_id=response.id or "",
            content=content,
            tool_calls=tuple(tool_calls),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            stop_reason=_map_stop_reason(choice.finish_reason),
        )


class OpenAIProviderFactory:
    """Factory registered at module load time.

    Call ``build(config, secrets)`` to get a live ``OpenAIProvider``.
    """

    name = _PROVIDER_NAME

    @property
    def configuration_spec(self) -> AIProviderConfigurationSpec:
        return _CONFIGURATION_SPEC

    def build(
        self,
        config: dict[str, Any],
        secrets: dict[str, str],
    ) -> OpenAIProvider:
        import os
        api_key = (
            secrets.get("api_key", "")
            or os.environ.get("OPENAI_API_KEY", "")
        ).strip()
        if not api_key:
            raise AIProviderConfigurationError(
                "OpenAI provider: api_key is required. "
                "Set it in the AI settings page or via OPENAI_API_KEY."
            )
        return OpenAIProvider(
            model_name=str(config.get("model_name", "") or _DEFAULT_MODEL),
            api_key=api_key,
            base_url=str(config.get("base_url", "") or "") or None,
            organization=str(config.get("organization", "") or "") or None,
        )

    def test_connection(
        self,
        config: dict[str, Any],
        secrets: dict[str, str],
    ) -> AIProviderConnectionResult:
        import openai
        try:
            provider = self.build(config, secrets)
        except AIProviderConfigurationError as exc:
            return AIProviderConnectionResult(
                success=False,
                status="configuration_error",
                message=str(exc),
            )
        from cauldron_ai.contracts import AIModelMessage, AIModelRequest
        req = AIModelRequest(
            messages=(AIModelMessage(role="user", content="Respond with the word OK."),),
            max_tokens=_MAX_TOKENS_TEST,
        )
        t0 = time.monotonic()
        try:
            response = provider.complete(req)
            latency_ms = (time.monotonic() - t0) * 1000
            return AIProviderConnectionResult(
                success=True,
                status="ok",
                message=f"Connected. Model: {provider._model_name}",
                provider_request_id=response.provider_request_id,
                latency_ms=latency_ms,
            )
        except AIProviderAuthenticationError as exc:
            return AIProviderConnectionResult(
                success=False,
                status="authentication_error",
                message=str(exc),
                latency_ms=(time.monotonic() - t0) * 1000,
            )
        except AIProviderConnectionError as exc:
            return AIProviderConnectionResult(
                success=False,
                status="connection_error",
                message=str(exc),
                latency_ms=(time.monotonic() - t0) * 1000,
            )
        except AIProviderRateLimitError as exc:
            return AIProviderConnectionResult(
                success=False,
                status="rate_limit",
                message=str(exc),
                latency_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as exc:
            return AIProviderConnectionResult(
                success=False,
                status="error",
                message=f"{type(exc).__name__}: {exc}",
                latency_ms=(time.monotonic() - t0) * 1000,
            )
