"""OpenAI provider adapter for Cauldron Admin AI.

Uses the OpenAI Responses API (``openai.responses.create``) with
``store=False`` so no conversation data is retained on OpenAI's servers.

The provider is constructed by ``OpenAIProviderFactory.build()``; it
should never be instantiated directly by application code.

Design notes
------------

* The provider deliberately raises fixed, credential-safe error messages
  for every vendor SDK exception.  Raw ``str(exc)`` output from ``openai``
  can (and does) contain fragments of the failed request — including the
  API key or partial response bodies.  Cauldron never propagates those
  strings into user-visible surfaces.
* We never set ``retries`` on the SDK client — the Cauldron service is
  the single owner of retry policy so it can enforce deadlines and audit
  the outcomes.
* Tool arguments returned by the model are validated as strict JSON
  objects.  Anything else (malformed JSON, non-object payloads) is a
  hard ``AIProviderResponseError`` — the caller MUST NOT try to guess
  what the model meant.
"""
from __future__ import annotations

import json
import os
import time
from types import MappingProxyType
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
_MAX_TOKENS_TEST = 32


_CONFIGURATION_SPEC = AIProviderConfigurationSpec(
    provider_name=_PROVIDER_NAME,
    display_name="OpenAI",
    version="2.0",
    description=(
        "Connects to OpenAI using the Responses API. "
        "Requires an API key from platform.openai.com."
    ),
    supports_connection_test=True,
    fields=(
        AIProviderConfigurationField(
            name="model",
            label="Model",
            field_type=FIELD_TYPE_TEXT,
            required=True,
            default=None,
            help_text=(
                "OpenAI model (e.g. gpt-4o, o3). "
                "See platform.openai.com/docs/models."
            ),
            max_length=128,
            environment_variable="OPENAI_MODEL",
        ),
        AIProviderConfigurationField(
            name="api_key",
            label="API Key",
            field_type=FIELD_TYPE_PASSWORD,
            required=True,
            default=None,
            help_text=(
                "Your OpenAI API key (sk-…). "
                "Also read from OPENAI_API_KEY when unset here."
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
                "Override the OpenAI API base URL. Leave blank for the "
                "default. Useful for OpenAI-compatible endpoints."
            ),
            max_length=512,
            environment_variable="OPENAI_BASE_URL",
            advanced=True,
        ),
    ),
)


def _plain(value: Any) -> Any:
    """Return a plain dict/list projection of a deep-frozen contract value."""
    if isinstance(value, MappingProxyType):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _build_openai_client(api_key: str, base_url: str | None):
    try:
        import openai
    except ImportError as exc:  # pragma: no cover - covered by install docs
        raise AIProviderConfigurationError(
            "The 'openai' package is not installed. "
            "Install cauldron-ai-openai to use the OpenAI provider."
        ) from exc
    # Retries are disabled — Cauldron controls retry and deadline behavior.
    # Leaving the SDK's default of 2 retries in place would let a single
    # ``complete()`` call quietly issue three requests and consume the
    # caller's deadline before we ever hear about it.
    kwargs: dict[str, Any] = {"api_key": api_key, "max_retries": 0}
    if base_url:
        kwargs["base_url"] = base_url
    return openai.OpenAI(**kwargs)


def _messages_to_input_items(
    messages: tuple[AIModelMessage, ...],
) -> list[dict[str, Any]]:
    """Translate provider-neutral messages into Responses API input items.

    ``system`` messages are dropped here — they are surfaced via the
    top-level ``instructions`` argument, which is the officially-supported
    channel for system prompts in the Responses API.
    """
    items: list[dict[str, Any]] = []
    for msg in messages:
        if msg.role == "system":
            continue
        if msg.role == "user":
            items.append({"role": "user", "content": msg.content})
        elif msg.role == "assistant":
            if msg.tool_calls:
                # One function_call input item per tool call. Text content
                # from the same assistant turn is dropped intentionally:
                # the Responses API models an assistant tool-calling turn
                # as function_call items, not as an output_text sibling.
                for tc in msg.tool_calls:
                    items.append({
                        "type": "function_call",
                        "call_id": tc.id,
                        "name": tc.name,
                        "arguments": json.dumps(
                            _plain(tc.arguments),
                            ensure_ascii=False,
                            allow_nan=False,
                        ),
                    })
            else:
                items.append({
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": msg.content or ""},
                    ],
                })
        elif msg.role == "tool":
            items.append({
                "type": "function_call_output",
                "call_id": msg.tool_call_id or "",
                "output": msg.content or "",
            })
    return items


def _tools_to_response_tools(tools: tuple) -> list[dict[str, Any]]:
    """Translate AIModelToolDefinition tuples into Responses API tool defs."""
    result: list[dict[str, Any]] = []
    for t in tools:
        result.append({
            "type": "function",
            "name": t.name,
            "description": t.description,
            "parameters": _plain(t.parameters),
            "strict": False,
        })
    return result


class OpenAIProvider:
    """Live provider built by :class:`OpenAIProviderFactory`.

    Direct construction should be avoided outside of tests — the factory
    is the single entry point that materialises credentials from the
    config store or environment.
    """

    name = _PROVIDER_NAME
    display_name = "OpenAI"

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str | None = None,
    ) -> None:
        if not api_key:
            raise AIProviderConfigurationError(
                "OpenAI provider requires a non-empty api_key."
            )
        if not model:
            raise AIProviderConfigurationError(
                "OpenAI provider requires a non-empty model name."
            )
        self._model = model
        self._client = _build_openai_client(api_key, base_url)

    # Publicly-visible aliases so callers (service factory / audit logging)
    # can record the active model without touching a leading-underscore
    # attribute.
    @property
    def model(self) -> str:
        return self._model

    def complete(self, request: AIModelRequest) -> AIModelResponse:
        import openai

        # System prompt: prefer the explicit ``AIModelRequest.system``
        # value, falling back to the first ``system``-role message for
        # backward compatibility.
        system_text = request.system or ""
        if not system_text:
            for m in request.messages:
                if m.role == "system" and m.content:
                    system_text = m.content
                    break

        input_items = _messages_to_input_items(request.messages)

        kwargs: dict[str, Any] = {
            "model": self._model,
            "input": input_items,
            "max_output_tokens": request.max_tokens,
            "store": False,
        }
        if system_text:
            kwargs["instructions"] = system_text
        if request.tools:
            kwargs["tools"] = _tools_to_response_tools(request.tools)
        if request.timeout_seconds:
            kwargs["timeout"] = request.timeout_seconds

        try:
            response = self._client.responses.create(**kwargs)
        except openai.AuthenticationError:
            raise AIProviderAuthenticationError(
                "OpenAI rejected the API key. "
                "Check your credentials in AI settings."
            )
        except openai.RateLimitError:
            raise AIProviderRateLimitError(
                "OpenAI rate limit reached. Please wait before retrying."
            )
        except openai.APITimeoutError:
            raise AIProviderConnectionError(
                "OpenAI request timed out. The model may be under load."
            )
        except openai.APIConnectionError:
            raise AIProviderConnectionError(
                "Could not reach OpenAI. "
                "Check your network or API base URL."
            )
        except openai.BadRequestError:
            raise AIProviderResponseError(
                "OpenAI returned a bad request error."
            )
        except openai.APIStatusError:
            raise AIProviderResponseError(
                "OpenAI returned an unexpected response."
            )

        # Validate terminal status before touching output; raises for
        # failed, cancelled, queued, in-progress, or unknown responses so
        # partial content can never be surfaced as a successful run.
        status_tag = _validate_response_status(response)
        content, tool_calls = _extract_output(response)

        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)

        if status_tag == "max_tokens":
            stop_reason = "max_tokens"
        else:  # "completed"
            stop_reason = "tool_use" if tool_calls else "end_turn"

        return AIModelResponse(
            provider_request_id=str(getattr(response, "id", "") or ""),
            content=content,
            tool_calls=tuple(tool_calls),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            stop_reason=stop_reason,
        )


def _extract_output(response: Any) -> tuple[str, list[AIModelToolCall]]:
    """Return (aggregated text content, function-call tool calls)."""
    output = getattr(response, "output", None) or []
    text_parts: list[str] = []
    tool_calls: list[AIModelToolCall] = []
    for item in output:
        item_type = getattr(item, "type", "")
        if item_type == "output_text":
            text_parts.append(str(getattr(item, "text", "") or ""))
        elif item_type == "message":
            # Some SDK versions wrap output_text inside a message item.
            for chunk in getattr(item, "content", None) or []:
                if getattr(chunk, "type", "") == "output_text":
                    text_parts.append(str(getattr(chunk, "text", "") or ""))
        elif item_type == "function_call":
            call_id = str(getattr(item, "call_id", "") or "")
            name = str(getattr(item, "name", "") or "")
            raw_args = getattr(item, "arguments", "") or ""
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError as exc:
                raise AIProviderResponseError(
                    "OpenAI returned a function call with invalid JSON arguments."
                ) from exc
            if not isinstance(args, dict):
                raise AIProviderResponseError(
                    "OpenAI returned a function call with non-object arguments."
                )
            tool_calls.append(AIModelToolCall(
                id=call_id, name=name, arguments=args,
            ))
    return "".join(text_parts), tool_calls


def _validate_response_status(response: Any) -> str:
    """Validate the OpenAI response status and return a terminal tag.

    Returns ``"completed"`` or ``"max_tokens"`` for responses that can be
    safely surfaced to the caller.  Raises ``AIProviderResponseError`` for
    every other status so that failed, cancelled, filtered, or incomplete
    responses never appear as successful Admin AI runs.

    No vendor error details are embedded in the raised message.
    """
    status = str(getattr(response, "status", "") or "").lower()

    if status == "completed":
        return "completed"

    if status == "incomplete":
        details = getattr(response, "incomplete_details", None)
        reason = str(getattr(details, "reason", "") or "").lower()
        if reason == "max_output_tokens":
            return "max_tokens"
        # Content filtering, safety blocking, truncation, or any other
        # non-token-limit reason means the response is partial.
        raise AIProviderResponseError(
            "OpenAI returned an incomplete response."
        )

    if status == "failed":
        raise AIProviderResponseError(
            "OpenAI returned a failed response."
        )

    if status == "cancelled":
        raise AIProviderResponseError(
            "OpenAI response was cancelled."
        )

    if status in ("queued", "in_progress"):
        # A synchronous responses.create call should never return these;
        # receiving one means something unexpected happened server-side.
        raise AIProviderResponseError(
            "OpenAI returned an unexpected non-terminal response "
            "from a synchronous call."
        )

    # Empty string (no status attribute) or any unrecognised value.
    raise AIProviderResponseError(
        "OpenAI returned a response with an unrecognised status."
    )


class OpenAIProviderFactory:
    """Factory registered at Django AppConfig ready() time.

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
        api_key = (
            str(secrets.get("api_key", "") or "")
            or os.environ.get("OPENAI_API_KEY", "")
        ).strip()
        if not api_key:
            raise AIProviderConfigurationError(
                "OpenAI provider: api_key is required. "
                "Configure it in AI settings or set OPENAI_API_KEY."
            )
        model = (
            str(config.get("model", "") or "")
            or str(config.get("model_name", "") or "")  # legacy compat
            or os.environ.get("OPENAI_MODEL", "")
        ).strip()
        if not model:
            raise AIProviderConfigurationError(
                "OpenAI provider: model is required. "
                "Configure it in AI settings or set OPENAI_MODEL."
            )
        base_url = (
            str(config.get("base_url", "") or "")
            or os.environ.get("OPENAI_BASE_URL", "")
        ).strip() or None
        return OpenAIProvider(
            model=model,
            api_key=api_key,
            base_url=base_url,
        )

    def test_connection(
        self,
        config: dict[str, Any],
        secrets: dict[str, str],
    ) -> AIProviderConnectionResult:
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
            messages=(AIModelMessage(
                role="user", content="Respond with the word OK.",
            ),),
            max_tokens=_MAX_TOKENS_TEST,
            timeout_seconds=10.0,
        )
        t0 = time.monotonic()
        try:
            response = provider.complete(req)
            latency_ms = (time.monotonic() - t0) * 1000
            return AIProviderConnectionResult(
                success=True,
                status="ok",
                message=f"Connected to OpenAI as model {provider.model}.",
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
        except AIProviderResponseError as exc:
            return AIProviderConnectionResult(
                success=False,
                status="response_error",
                message=str(exc),
                latency_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception:
            # Never surface raw SDK/exception text — the message we return
            # to the settings page is fixed and credential-safe.
            return AIProviderConnectionResult(
                success=False,
                status="error",
                message="OpenAI connection test failed with an unexpected error.",
                latency_ms=(time.monotonic() - t0) * 1000,
            )
