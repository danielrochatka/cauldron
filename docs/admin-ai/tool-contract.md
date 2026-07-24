# Tool Contract

Every tool exposed to the Admin AI is declared through an
`AdminAIToolDefinition`, registered against the process-wide
`AdminAIToolRegistry`.

## `AdminAIToolDefinition`

```python
@dataclass(frozen=True)
class AdminAIToolDefinition:
    name: str
    version: str
    description: str
    argument_schema: dict            # Draft-07 JSON Schema
    risk_level: RiskLevel
    required_permission: str
    owning_module: str
    timeout_seconds: float = 30.0
    max_output_bytes: int = 65536
    supports_dry_run: bool = False
    requires_human_approval: bool = False
```

Registration invariants (`ValueError` on violation):

* `name` matches `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$`, ≤128 chars.
* `version` matches `^(v\d+|\d+\.\d+(\.\d+)?…)$`, ≤32 chars.
* `owning_module` is dotted lowercase, ≤128 chars.
* `required_permission` matches `^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$`.
* `argument_schema` is a valid Draft-07 JSON Schema.
* `timeout_seconds` > 0, `max_output_bytes` > 0.
* `server.*` names may only be registered by
  `owning_module == "cauldron.ai.admin.server"` — the reserved
  namespace for the future server module.

Re-registering the identical `(definition, handler)` is a silent no-op
so Django autoreload does not raise. A different handler or definition
under the same name raises `ValueError`.

## Handler signature

```python
def handler(context: AdminAIToolContext, **arguments) -> AdminAIToolResult | AdminAIToolError:
    ...
```

`AdminAIToolContext` provides:

* `actor` — the calling Django user.
* `run_id` / `correlation_id` — audit tokens.
* `content_service` — the injected `ContentOperationService` or `None`.
* `deadline` — UTC datetime; use `deadline_remaining_seconds()` before
  any mutation.
* `dry_run` — future flag; currently always `False`.

## Result types

```python
@dataclass(frozen=True)
class AdminAIToolResult:
    tool_name: str
    success: bool = True
    data: Any = None            # JSON-serialisable
    message: str = ""

@dataclass(frozen=True)
class AdminAIToolError:
    tool_name: str
    error_code: str
    message: str
```

The service verifies:

* Result is one of the two dataclasses.
* `tool_name == invoked_tool_name`.
* `AdminAIToolResult.success is True`.
* `data` round-trips through `json.dumps()` **without** `default=str`.
* Full JSON-encoded payload fits `min(tool.max_output_bytes,
  service.max_result_bytes)` → `tool.result_too_large` otherwise.

## Argument validation

Arguments are validated at request time using `jsonschema.Draft7Validator`.
Nested schemas, `additionalProperties`, `enum`, `minimum`/`maximum`,
`minLength`/`maxLength`, `minItems`/`maxItems`, and the bool/int
distinction are all enforced. Arguments containing values that are not
JSON-serialisable are rejected as `tool.invalid_arguments`.

## Risk levels

| Level       | Meaning                                                                 |
|-------------|-------------------------------------------------------------------------|
| READ_ONLY   | Inspection only.                                                        |
| PROPOSE     | May create non-canonical proposals (change-requests).                   |
| MAINTENANCE | Refused with `approval_required`; run ends in `waiting_for_approval`.   |
| PRIVILEGED  | Refused with `restricted`; run ends in `waiting_for_approval`.          |

## Built-in tools shipped by `cauldron.ai.admin`

* `content.list_collections` (READ_ONLY)
* `content.list_items` (READ_ONLY)
* `content.get_item` (READ_ONLY)
* `content.create_proposal` (PROPOSE — only calls
  `create_change_request`)
* `content.preview_change_request` (READ_ONLY — never mutates)
* `system.django_checks` (READ_ONLY, tag allow-list)
* `system.module_status` (READ_ONLY, redacted graph info)
