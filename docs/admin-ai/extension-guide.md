# Extending Admin AI with New Tools

Any Cauldron module (or Django app) may extend Admin AI's tool surface
without importing anything from `cauldron_ai_admin` except the two
public helpers listed below.

## Recommended pattern

Do the registration inside your Django `AppConfig.ready()`. This
happens after apps are loaded but before requests are served, so the
registry is populated exactly once per process.

```python
# my_module/apps.py
from django.apps import AppConfig


class MyModuleConfig(AppConfig):
    name = "my_module"

    def ready(self) -> None:
        from cauldron_ai_admin.tools import (
            AdminAIToolDefinition,
            AdminAIToolResult,
            RiskLevel,
            register_tool,
        )
        from .tools import handle_ping

        register_tool(
            AdminAIToolDefinition(
                name="myproject.ping",
                version="1.0",
                description="Return 'pong' with server time.",
                argument_schema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                risk_level=RiskLevel.READ_ONLY,
                required_permission="my_module.use_ping",
                owning_module="my_module",
            ),
            handle_ping,
        )
```

Where `handle_ping` is a plain function:

```python
from datetime import datetime, timezone
from cauldron_ai_admin.tools import (
    AdminAIToolContext, AdminAIToolResult,
)


def handle_ping(context: AdminAIToolContext, **kwargs):
    return AdminAIToolResult(
        tool_name="myproject.ping",
        success=True,
        data={"pong": True, "when": datetime.now(tz=timezone.utc).isoformat()},
    )
```

## Namespace reservation

`server.*` names are reserved for `owning_module ==
"cauldron.ai.admin.server"`. Attempts to register a `server.*` tool
from any other module raise `ValueError` and cause the
`admin_ai.E007` system check to fire at startup.

Use dotted namespaces to avoid clashes:

* Prefer your Django app label as the leading segment
  (`myproject.foo.bar`).
* Never rely on `content.*` or `system.*` — the shipping module owns
  those.

## Argument schema

Schemas are validated against JSON Schema Draft-07 at registration
time via `jsonschema.Draft7Validator.check_schema()`. Malformed
schemas raise `jsonschema.SchemaError` immediately, so a broken tool
never joins the registry.

At invocation time the same library validates the model's arguments
against your schema. Keep schemas strict:

* Set `additionalProperties: false` to reject unknown keys.
* Add `minLength: 1` to any required strings.
* Use `enum` for choices.
* Use integer bounds (`minimum`, `maximum`) where meaningful.

## Handler expectations

* Never mutate state outside the `context.content_service` seam.
* Return a `AdminAIToolResult` on success (JSON-serialisable data) or
  `AdminAIToolError` on failure (stable error code).
* Do not raise exceptions with sensitive text — the service redacts,
  but keeping messages boring is better.
* Refuse mutations when `context.deadline_remaining_seconds()` is
  negative or below ~100 ms.
