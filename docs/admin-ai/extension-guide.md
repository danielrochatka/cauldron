# Extending Admin AI with New Tools

Any Cauldron module (or Django app) may extend Admin AI's tool surface
without importing anything from `cauldron_ai_admin` except the public
helpers listed below.

## What every tool requires

Every provider-visible Admin AI tool requires **exactly one versioned
prompt template** in addition to its tool definition. The template is how
the model learns the tool's purpose, expected inputs, risk level, and
when to use it. Without a template the service fails closed before
invoking the provider — the run is stopped with error code
`prompt.missing_template`.

The Django system check `admin_ai.E017` reports any registered tool that
is missing a prompt template at startup. You should resolve E017 before
deploying.

## Recommended pattern

Register both the tool definition and the prompt template from your
Django `AppConfig.ready()`. This happens after apps are loaded but before
requests are served, so both registries are populated exactly once per
process.

```python
# my_module/apps.py
from django.apps import AppConfig


class MyModuleConfig(AppConfig):
    name = "my_module"

    def ready(self) -> None:
        from cauldron_ai.prompt_templates import (
            AIToolPromptTemplate,
            register_tool_template,
        )
        from cauldron_ai_admin.tools import (
            AdminAIToolDefinition,
            RiskLevel,
            register_tool,
        )
        from .tools import handle_ping

        _TOOL_NAME = "myproject.ping"
        _TOOL_VERSION = "1.0"
        _TEMPLATE_VERSION = "v1"
        _PERMISSION = "my_module.use_ping"
        _MODULE = "my_module"

        register_tool(
            AdminAIToolDefinition(
                name=_TOOL_NAME,
                version=_TOOL_VERSION,
                description="Return 'pong' with server time.",
                argument_schema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                risk_level=RiskLevel.READ_ONLY,
                required_permission=_PERMISSION,
                owning_module=_MODULE,
            ),
            handle_ping,
        )

        register_tool_template(
            AIToolPromptTemplate(
                tool_name=_TOOL_NAME,
                template_version=_TEMPLATE_VERSION,
                owning_module=_MODULE,
                purpose=(
                    "Verify that the Admin AI pipeline is reachable by returning a "
                    "pong response with the current server time."
                ),
                supported_tasks=("connectivity check", "liveness verification"),
                required_permission=_PERMISSION,
                risk_level="READ_ONLY",
                read_scope=(
                    "Reads only the server clock; no content, users, or config "
                    "are accessed."
                ),
                write_scope="None.",
                preconditions=(
                    "No preconditions. Safe to call at any time.",
                ),
                input_expectations="No arguments required.",
                result_behavior=(
                    "Returns {pong: true, when: <ISO-8601 timestamp>}. "
                    "Treat an error response as a connectivity failure."
                ),
                approval_requirements="None; read-only.",
                clarification_behavior=(
                    "No clarification needed — this tool takes no arguments."
                ),
                refusal_behavior=(
                    "Refuse if the deadline has expired or the user's stated "
                    "intent does not match a connectivity or liveness check."
                ),
                error_guidance=(
                    "Report the error code and message verbatim. Do not retry "
                    "automatically."
                ),
                positive_examples=(
                    "User: 'Is the AI pipeline working?' → call myproject.ping.",
                ),
                boundary_examples=(
                    "User: 'Ping every minute' → refuse; automation is out of scope.",
                ),
            )
        )
```

Where `handle_ping` is a plain function:

```python
# my_module/tools.py
from datetime import datetime, timezone

from cauldron_ai_admin.tools import AdminAIToolContext, AdminAIToolResult


def handle_ping(context: AdminAIToolContext, **kwargs):
    return AdminAIToolResult(
        tool_name="myproject.ping",
        success=True,
        data={"pong": True, "when": datetime.now(tz=timezone.utc).isoformat()},
    )
```

## Matching the definition and template

The tool definition and prompt template must agree on the following fields:

| Field | Requirement |
|-------|-------------|
| `tool_name` / `name` | Identical dotted-lowercase name in both |
| `required_permission` | Same permission string (or `None` in the template to skip the check) |
| `risk_level` | Same symbolic level (`READ_ONLY`, `PROPOSE`, `MAINTENANCE`, `PRIVILEGED`) |
| `owning_module` | Same module identifier |
| `input_expectations` | Must accurately describe the argument schema |

The system check `admin_ai.E021` reports permission mismatches between a
tool definition and its template.

## Template versioning

Use explicit version strings such as `v1`, `v2`, or `1.0.0`. Increment
the version whenever the prompt behavior materially changes — for example,
when you add new tasks, change the risk scope, or update the approval
requirements. Version strings follow the pattern `v<N>` or
`<major>.<minor>[.<patch>]`.

The version is recorded on every `AdminAIRun` row for audit purposes.
Stale version strings make audits meaningless, so treat them with the
same discipline as database migration numbers.

## Django is the authority for permissions and validation

Prompt templates guide what the model is told about a tool. They do not
replace server-side enforcement. Django still:

- checks `required_permission` before exposing a tool to an actor
- validates the model's arguments against the JSON Schema
- enforces risk-level rules (e.g. `MAINTENANCE` tools require approval)
- runs all handler logic under the run deadline

Templates are advisory to the model, not authoritative over access
control.

## Optional modules: register and remove together

If your app can be enabled or disabled at runtime, register both the tool
and its template in the same `ready()` block and remove both when the
module is disabled. The system checks E017 and E018 will fire if either
registration is present without the other:

- **E017** — a registered tool has no prompt template
- **E018** — a prompt template exists for an unknown tool

## Namespace reservation

`server.*` names are reserved for `owning_module ==
"cauldron.ai.admin.server"`. Attempts to register a `server.*` tool
from any other module raise `ValueError` and cause the
`admin_ai.E007` system check to fire at startup.

Use dotted namespaces to avoid clashes:

- Prefer your Django app label as the leading segment (`myproject.foo.bar`).
- Never rely on `content.*` or `system.*` — the shipping module owns those.

## Argument schema

Schemas are validated against JSON Schema Draft-07 at registration time
via `jsonschema.Draft7Validator.check_schema()`. Malformed schemas raise
`jsonschema.SchemaError` immediately, so a broken tool never joins the
registry.

At invocation time the same library validates the model's arguments
against your schema. Keep schemas strict:

- Set `additionalProperties: false` to reject unknown keys.
- Add `minLength: 1` to any required strings.
- Use `enum` for choices.
- Use integer bounds (`minimum`, `maximum`) where meaningful.

## Handler expectations

- Never mutate state outside the `context.content_service` seam.
- Return an `AdminAIToolResult` on success (JSON-serialisable data) or
  `AdminAIToolError` on failure (stable error code).
- Do not raise exceptions with sensitive text — the service redacts,
  but keeping messages boring is better.
- Refuse mutations when `context.deadline_remaining_seconds()` is
  negative or below ~100 ms.

---

## Migration guide for existing extensions

If your extension was written before prompt templates were required, follow
these steps to bring it into compliance:

**1. Create an `AIToolPromptTemplate` for each existing tool**

For each `AdminAIToolDefinition` you already register, add a matching
`AIToolPromptTemplate`. Use the same `tool_name`, `required_permission`,
`risk_level`, and `owning_module`. Set `template_version` to `"v1"`.

**2. Register the template during app startup**

Add `register_tool_template(...)` immediately after your existing
`register_tool(...)` call in `AppConfig.ready()`. See the full example
above.

**3. Run the system check**

```shell
python manage.py check
```

`admin_ai.E017` fires for any tool that still lacks a template.
`admin_ai.E018` fires for any template that has no matching tool. Both
must be clear before deploying.

**4. Verify E017 and E018 are absent**

After adding your templates, `manage.py check` should produce no
`admin_ai.E017` or `admin_ai.E018` messages. If E017 still appears,
confirm the `tool_name` in the template exactly matches the `name` in the
tool definition (case-sensitive, dotted lowercase).
