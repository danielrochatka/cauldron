# Provider Contract

Cauldron Admin AI is provider-neutral. All communication with a model
vendor goes through the `AIModelProvider` protocol, defined in
`cauldron_ai.providers`. No vendor SDK is imported anywhere in this
repository.

## `AIModelProvider`

```python
class AIModelProvider(Protocol):
    name: str
    def complete(self, request: AIModelRequest) -> AIModelResponse: ...
```

Contracts:

* `name` — unique per registered provider process-wide.
* `complete()` must be a pure function of `AIModelRequest`. No hidden
  state that depends on the caller's identity, no side effects other
  than the API call and observability.

Register at Django startup:

```python
from cauldron_ai.providers import register_provider
register_provider(MyProvider())
```

Sites pin a provider by name via
`CAULDRON_MODULES["cauldron.ai.admin"]["provider"]`.

## `AIModelRequest`

Fields (all frozen, defensively copied):

| Field              | Type                                | Notes                                        |
|--------------------|-------------------------------------|----------------------------------------------|
| `messages`         | `tuple[AIModelMessage, ...]`        | Ordered conversation.                        |
| `tools`            | `tuple[AIModelToolDefinition, ...]` | Tools the model may call.                    |
| `system`           | `str`                               | System prompt.                               |
| `max_tokens`       | `int`                               | Positive.                                    |
| `timeout_seconds`  | `float`                             | Positive.                                    |
| `correlation_id`   | `str`                               | Opaque tracing token.                        |
| `deadline_seconds` | `float | None`                      | Cooperative deadline; provider may respect.  |

## `AIModelResponse`

| Field                 | Type                              | Notes                             |
|-----------------------|-----------------------------------|-----------------------------------|
| `provider_request_id` | `str`                             | Vendor request ID.                |
| `content`             | `str`                             | Assistant text.                   |
| `tool_calls`          | `tuple[AIModelToolCall, ...]`     | Any requested tool calls.         |
| `input_tokens`        | `int`                             | Reported usage.                   |
| `output_tokens`       | `int`                             | Reported usage.                   |
| `stop_reason`         | `"" | "end_turn" | "tool_use" | "max_tokens" | "timeout"` | See below. |

## Response validation

The service rejects any response that:

* Is not an `AIModelResponse` instance → `provider.invalid_response`.
* Contains duplicate tool-call IDs within a single response
  → `provider.invalid_response`.
* Carries `tool_calls` without `stop_reason == "tool_use"`
  → `provider.invalid_response`.
* Emits a final answer with `stop_reason != "end_turn"`
  → `provider.invalid_response`.
* Sets `stop_reason == "max_tokens"` → `provider.max_tokens` (run fails).
* Sets `stop_reason == "timeout"` → `provider.timeout` (run fails).
* Exceeds `max_result_bytes` in content or any tool-call payload
  → `provider.response_too_large`.

## Conversation structure

Each service turn appends:

1. An assistant `AIModelMessage` carrying `tool_calls`.
2. One `AIModelMessage(role="tool", tool_call_id=…)` per tool result.

Role invariants:

* `user`/`system`: no `tool_calls`, no `tool_call_id`.
* `assistant`: may carry `content`, `tool_calls`, or both; no
  `tool_call_id`.
* `tool`: requires `tool_call_id`; no `tool_calls`.

## Descriptors

Every registered provider ships with an `AIModelProviderDescriptor`
(name, display name, version) available through
`cauldron_ai.descriptor_for(name)` for introspection.
