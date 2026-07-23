"""AdminAIService — provider-neutral pipeline for admin AI requests.

The service turns a single natural-language request into a bounded
tool-execution loop against an ``AIModelProvider``. Every tool call
produces a persisted ``AdminAIToolInvocation`` row (including denied
calls), and the containing ``AdminAIRun`` records the final state.

Policy summary
--------------
* Unknown tools produce ``tool.unknown``.
* Argument-schema violations produce ``tool.invalid_arguments``.
* Overlong argument payloads produce ``tool.arguments_too_large``.
* Missing Django permission produces ``tool.permission_denied``.
* Duplicate tool-call ids inside a run produce ``tool.duplicate_call_id``.
* Exceeding ``max_model_turns`` produces ``run.max_turns_exceeded``.
* Exceeding ``max_tool_calls`` produces ``run.max_tool_calls_exceeded``.
* ``MAINTENANCE`` tools return ``approval_required`` and put the run
  into ``waiting_for_approval`` without executing.
* ``PRIVILEGED`` tools return ``restricted`` and put the run into
  ``waiting_for_approval`` without executing.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from django.db import transaction
from django.utils import timezone as django_timezone

from cauldron_ai.contracts import (
    AIModelMessage,
    AIModelRequest,
    AIModelResponse,
    AIModelToolCall,
    AIModelToolDefinition,
)
from cauldron_ai.providers import AIModelProvider

from .models import AdminAIRun, AdminAIToolInvocation
from .tools import (
    AdminAIToolContext,
    AdminAIToolDefinition,
    AdminAIToolError,
    AdminAIToolRegistry,
    AdminAIToolResult,
    RiskLevel,
    get_tool_registry,
)


logger = logging.getLogger(__name__)


DEFAULT_SYSTEM_PROMPT = (
    "You are a cautious Cauldron admin assistant. You have access to a "
    "restricted set of tools. Prefer read-only inspection first, and "
    "use PROPOSE-level tools only when a change is needed — proposals "
    "must be reviewed by a human before they are applied."
)


@dataclass(frozen=True)
class _ValidationError:
    code: str
    message: str


class AdminAIService:
    def __init__(
        self,
        *,
        provider: AIModelProvider,
        tool_registry: AdminAIToolRegistry | None = None,
        content_service: Any = None,
        max_model_turns: int = 6,
        max_tool_calls: int = 10,
        tool_timeout_seconds: float = 30.0,
        run_timeout_seconds: float = 120.0,
        max_argument_bytes: int = 32768,
        max_result_bytes: int = 65536,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    ) -> None:
        if max_model_turns <= 0:
            raise ValueError("max_model_turns must be positive")
        if max_tool_calls <= 0:
            raise ValueError("max_tool_calls must be positive")
        if tool_timeout_seconds <= 0:
            raise ValueError("tool_timeout_seconds must be positive")
        if run_timeout_seconds <= 0:
            raise ValueError("run_timeout_seconds must be positive")
        if max_argument_bytes <= 0:
            raise ValueError("max_argument_bytes must be positive")
        if max_result_bytes <= 0:
            raise ValueError("max_result_bytes must be positive")

        self._provider = provider
        self._tool_registry = tool_registry or get_tool_registry()
        self._content_service = content_service
        self._max_model_turns = int(max_model_turns)
        self._max_tool_calls = int(max_tool_calls)
        self._tool_timeout_seconds = float(tool_timeout_seconds)
        self._run_timeout_seconds = float(run_timeout_seconds)
        self._max_argument_bytes = int(max_argument_bytes)
        self._max_result_bytes = int(max_result_bytes)
        self._system_prompt = system_prompt

    # ------------------------------------------------------------------ public

    @property
    def content_service(self) -> Any:
        return self._content_service

    def run(
        self,
        actor: Any,
        request_text: str,
        *,
        correlation_id: str = "",
    ) -> AdminAIRun:
        """Execute the full pipeline for a single natural-language request."""
        if actor is None or not getattr(actor, "is_active", False):
            raise PermissionError("Admin AI requires an authenticated, active actor.")
        if not isinstance(request_text, str) or not request_text.strip():
            raise ValueError("request_text must be a non-empty string.")

        run = AdminAIRun.objects.create(
            actor=actor,
            status="created",
            provider_name=getattr(self._provider, "name", "") or "",
            user_request=request_text,
            correlation_id=correlation_id or "",
        )
        run.started_at = django_timezone.now()
        run.status = "running"
        run.save(update_fields=["started_at", "status"])

        try:
            self._execute_loop(run, actor, request_text, correlation_id)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Admin AI run %s crashed", run.run_id)
            self._finalize_error(run, "run.internal_error", str(exc)[:512])
        return run

    # --------------------------------------------------------------- internals

    def _execute_loop(
        self,
        run: AdminAIRun,
        actor: Any,
        request_text: str,
        correlation_id: str,
    ) -> None:
        permitted_defs = self._tool_registry.list_for_actor(actor)
        tool_defs_for_model = tuple(
            _to_model_tool_definition(d) for d in permitted_defs
        )
        messages: list[AIModelMessage] = [
            AIModelMessage(role="user", content=request_text)
        ]
        seen_call_ids: set[str] = set()
        tool_calls_made = 0
        started = time.monotonic()

        for turn_index in range(self._max_model_turns):
            if time.monotonic() - started > self._run_timeout_seconds:
                self._finalize_error(run, "run.timeout", "Run exceeded time budget.")
                return

            provider_request = AIModelRequest(
                messages=tuple(messages),
                tools=tool_defs_for_model,
                system=self._system_prompt,
                max_tokens=4096,
                timeout_seconds=self._tool_timeout_seconds,
                correlation_id=correlation_id or "",
            )
            try:
                response = self._provider.complete(provider_request)
            except Exception as exc:
                self._finalize_error(
                    run, "provider.error", f"{type(exc).__name__}: {exc}"[:512]
                )
                return

            # Persist provider request id on the run once we have it.
            if response.provider_request_id and not run.provider_request_id:
                run.provider_request_id = response.provider_request_id[:256]
                run.save(update_fields=["provider_request_id"])

            if not response.tool_calls:
                # No tool calls; treat as the final answer.
                self._finalize_success(run, response.content)
                return

            # Handle every tool call the model returned this turn.
            for call in response.tool_calls:
                if tool_calls_made >= self._max_tool_calls:
                    self._finalize_error(
                        run,
                        "run.max_tool_calls_exceeded",
                        "Model exceeded the per-run tool-call budget.",
                    )
                    return

                if call.id in seen_call_ids:
                    self._record_denied_invocation(
                        run,
                        call,
                        risk_level=RiskLevel.READ_ONLY,
                        error_code="tool.duplicate_call_id",
                        message="Duplicate tool-call id within the same run.",
                    )
                    self._finalize_error(
                        run,
                        "tool.duplicate_call_id",
                        f"Duplicate tool call id {call.id!r}.",
                    )
                    return
                seen_call_ids.add(call.id)

                tool_calls_made += 1
                tool_message = self._handle_tool_call(run, actor, call, correlation_id)
                messages.append(
                    AIModelMessage(
                        role="assistant",
                        content=self._describe_tool_call(call),
                    )
                )
                messages.append(tool_message)

                # If the tool call triggered a stop condition (approval /
                # restricted / hard error), the invocation handler already
                # finalized the run — stop iterating.
                if run.status in {"waiting_for_approval", "failed"}:
                    return

        # We exhausted the model-turn budget without a final answer.
        self._finalize_error(
            run,
            "run.max_turns_exceeded",
            "Model exceeded the per-run turn budget without a final answer.",
        )

    def _handle_tool_call(
        self,
        run: AdminAIRun,
        actor: Any,
        call: AIModelToolCall,
        correlation_id: str,
    ) -> AIModelMessage:
        entry = self._tool_registry.get(call.name)
        if entry is None:
            self._record_denied_invocation(
                run,
                call,
                risk_level=RiskLevel.READ_ONLY,
                error_code="tool.unknown",
                message=f"Unknown tool {call.name!r}.",
            )
            self._finalize_error(run, "tool.unknown", f"Unknown tool {call.name!r}.")
            return _tool_error_message(call.id, "tool.unknown", "Unknown tool.")

        definition, handler = entry

        arg_bytes = _payload_bytes(call.arguments)
        if arg_bytes > self._max_argument_bytes:
            self._record_denied_invocation(
                run, call, definition.risk_level,
                error_code="tool.arguments_too_large",
                message=f"Argument payload {arg_bytes}B exceeds limit.",
                definition=definition,
            )
            self._finalize_error(
                run, "tool.arguments_too_large",
                f"Argument payload for {call.name!r} is too large.",
            )
            return _tool_error_message(
                call.id, "tool.arguments_too_large", "Arguments too large."
            )

        # Basic JSON-Schema shape check: required keys must exist and types
        # of top-level primitives must roughly match. We deliberately keep
        # this shallow — full validation belongs to the tool handler.
        schema_error = _shallow_validate(call.arguments, definition.argument_schema)
        if schema_error is not None:
            self._record_denied_invocation(
                run, call, definition.risk_level,
                error_code="tool.invalid_arguments",
                message=schema_error.message,
                definition=definition,
            )
            self._finalize_error(
                run, "tool.invalid_arguments", schema_error.message[:512],
            )
            return _tool_error_message(
                call.id, "tool.invalid_arguments", schema_error.message,
            )

        # Permission check.
        try:
            has_perm = bool(actor.has_perm(definition.required_permission))
        except Exception:
            has_perm = False
        if not has_perm:
            self._record_denied_invocation(
                run, call, definition.risk_level,
                error_code="tool.permission_denied",
                message=(
                    f"Actor lacks permission {definition.required_permission!r}."
                ),
                definition=definition,
            )
            self._finalize_error(
                run, "tool.permission_denied",
                f"Actor lacks permission {definition.required_permission!r}.",
            )
            return _tool_error_message(
                call.id, "tool.permission_denied", "Permission denied.",
            )

        # Risk-level policy.
        if definition.risk_level == RiskLevel.MAINTENANCE:
            self._record_denied_invocation(
                run, call, definition.risk_level,
                error_code="approval_required",
                message="Tool requires human approval.",
                definition=definition,
                status="denied",
            )
            self._finalize_waiting_for_approval(
                run, "approval_required",
                f"Tool {call.name!r} requires human approval.",
            )
            return _tool_error_message(
                call.id, "approval_required", "Human approval required.",
            )
        if definition.risk_level == RiskLevel.PRIVILEGED:
            self._record_denied_invocation(
                run, call, definition.risk_level,
                error_code="restricted",
                message="Tool is restricted; contact an administrator.",
                definition=definition,
                status="denied",
            )
            self._finalize_waiting_for_approval(
                run, "restricted",
                f"Tool {call.name!r} is restricted.",
            )
            return _tool_error_message(
                call.id, "restricted", "Restricted tool.",
            )

        # Execute (READ_ONLY / PROPOSE).
        invocation = AdminAIToolInvocation.objects.create(
            run=run,
            tool_call_id=call.id[:128],
            tool_name=definition.name,
            tool_version=definition.version,
            owning_module=definition.owning_module,
            risk_level=definition.risk_level.value,
            status="running",
            arguments_hash=_hash_arguments(call.arguments),
            argument_summary=_summarise(call.arguments, 512),
            started_at=django_timezone.now(),
        )
        context = AdminAIToolContext(
            actor=actor,
            run_id=str(run.run_id),
            correlation_id=correlation_id or "",
            dry_run=False,
        )

        t0 = time.monotonic()
        try:
            outcome = handler(context, **dict(call.arguments))
        except Exception as exc:
            duration_ms = int((time.monotonic() - t0) * 1000)
            invocation.status = "failed"
            invocation.error_code = "tool.handler_exception"
            invocation.result_summary = f"{type(exc).__name__}: {exc}"[:1024]
            invocation.duration_ms = duration_ms
            invocation.completed_at = django_timezone.now()
            invocation.save()
            run.tool_call_count = run.tool_call_count + 1
            run.save(update_fields=["tool_call_count"])
            return _tool_error_message(
                call.id, "tool.handler_exception",
                "Tool handler raised an exception.",
            )

        duration_ms = int((time.monotonic() - t0) * 1000)
        invocation.duration_ms = duration_ms
        invocation.completed_at = django_timezone.now()

        if isinstance(outcome, AdminAIToolError):
            invocation.status = "failed"
            invocation.error_code = outcome.error_code[:128]
            invocation.result_summary = _summarise(outcome.message, 1024)
            invocation.save()
            run.tool_call_count = run.tool_call_count + 1
            run.save(update_fields=["tool_call_count"])
            return _tool_error_message(call.id, outcome.error_code, outcome.message)

        # Success.
        if not isinstance(outcome, AdminAIToolResult):
            invocation.status = "failed"
            invocation.error_code = "tool.bad_return_type"
            invocation.result_summary = f"{type(outcome).__name__}"[:1024]
            invocation.save()
            run.tool_call_count = run.tool_call_count + 1
            run.save(update_fields=["tool_call_count"])
            return _tool_error_message(
                call.id, "tool.bad_return_type",
                "Tool returned unsupported type.",
            )

        data_bytes = _payload_bytes(outcome.data or {})
        if data_bytes > self._max_result_bytes:
            invocation.status = "failed"
            invocation.error_code = "tool.result_too_large"
            invocation.result_summary = (
                f"Result {data_bytes}B exceeds {self._max_result_bytes}B limit."
            )
            invocation.save()
            run.tool_call_count = run.tool_call_count + 1
            run.save(update_fields=["tool_call_count"])
            return _tool_error_message(
                call.id, "tool.result_too_large", "Result too large.",
            )

        invocation.status = "completed"
        invocation.result_summary = _summarise(outcome.data, 1024)
        invocation.save()
        run.tool_call_count = run.tool_call_count + 1
        run.save(update_fields=["tool_call_count"])

        payload = {
            "tool_name": outcome.tool_name,
            "success": True,
            "data": outcome.data,
            "message": outcome.message,
        }
        return AIModelMessage(
            role="tool",
            content=json.dumps(payload, sort_keys=True, default=str)[
                : self._max_result_bytes
            ],
            tool_call_id=call.id,
        )

    # -------------------------------------------------------------- finalizers

    def _finalize_success(self, run: AdminAIRun, content: str) -> None:
        with transaction.atomic():
            run.refresh_from_db()
            run.status = "completed"
            run.final_response = content or ""
            run.completed_at = django_timezone.now()
            run.version = run.version + 1
            run.save(update_fields=[
                "status", "final_response", "completed_at", "version",
            ])

    def _finalize_error(self, run: AdminAIRun, code: str, summary: str) -> None:
        with transaction.atomic():
            run.refresh_from_db()
            run.status = "failed"
            run.error_code = code[:128]
            run.error_summary = summary[:512]
            run.completed_at = django_timezone.now()
            run.version = run.version + 1
            run.save(update_fields=[
                "status", "error_code", "error_summary", "completed_at", "version",
            ])

    def _finalize_waiting_for_approval(
        self, run: AdminAIRun, code: str, summary: str,
    ) -> None:
        with transaction.atomic():
            run.refresh_from_db()
            run.status = "waiting_for_approval"
            run.error_code = code[:128]
            run.error_summary = summary[:512]
            run.completed_at = django_timezone.now()
            run.version = run.version + 1
            run.save(update_fields=[
                "status", "error_code", "error_summary", "completed_at", "version",
            ])

    def _record_denied_invocation(
        self,
        run: AdminAIRun,
        call: AIModelToolCall,
        risk_level: RiskLevel,
        *,
        error_code: str,
        message: str,
        definition: AdminAIToolDefinition | None = None,
        status: str = "denied",
    ) -> None:
        invocation = AdminAIToolInvocation.objects.create(
            run=run,
            tool_call_id=(call.id or "")[:128],
            tool_name=call.name[:128] if call.name else "",
            tool_version=(definition.version if definition else ""),
            owning_module=(definition.owning_module if definition else ""),
            risk_level=risk_level.value,
            status=status,
            arguments_hash=_hash_arguments(call.arguments),
            argument_summary=_summarise(call.arguments, 512),
            error_code=error_code[:128],
            result_summary=message[:1024],
            completed_at=django_timezone.now(),
        )
        run.tool_call_count = run.tool_call_count + 1
        run.save(update_fields=["tool_call_count"])
        return None

    def _describe_tool_call(self, call: AIModelToolCall) -> str:
        try:
            arg_preview = json.dumps(call.arguments, sort_keys=True, default=str)
        except Exception:
            arg_preview = str(call.arguments)
        if len(arg_preview) > 512:
            arg_preview = arg_preview[:509] + "..."
        return f"Called {call.name}({arg_preview})"


# ---------------------------------------------------------------------- helpers


def _to_model_tool_definition(defn: AdminAIToolDefinition) -> AIModelToolDefinition:
    return AIModelToolDefinition(
        name=defn.name,
        description=defn.description,
        parameters=dict(defn.argument_schema),
    )


def _tool_error_message(call_id: str, code: str, message: str) -> AIModelMessage:
    payload = json.dumps({"success": False, "error_code": code, "message": message})
    return AIModelMessage(role="tool", content=payload, tool_call_id=call_id)


def _payload_bytes(payload: Any) -> int:
    try:
        return len(
            json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False).encode(
                "utf-8"
            )
        )
    except Exception:
        return len(str(payload).encode("utf-8"))


def _hash_arguments(arguments: Any) -> str:
    try:
        serialised = json.dumps(
            arguments, sort_keys=True, default=str, ensure_ascii=False,
        )
    except Exception:
        serialised = repr(arguments)
    return hashlib.sha256(serialised.encode("utf-8")).hexdigest()


def _summarise(value: Any, limit: int) -> str:
    try:
        text = json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)
    except Exception:
        text = str(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _shallow_validate(
    arguments: Any, schema: dict,
) -> _ValidationError | None:
    """Very small JSON-Schema check (types and required keys).

    We accept an ``object`` schema and check top-level ``required`` and
    ``properties``. Anything more advanced is the tool handler's problem.
    """
    if not isinstance(schema, dict):
        return _ValidationError("tool.invalid_arguments", "Tool schema is not a dict.")
    if not isinstance(arguments, dict):
        return _ValidationError(
            "tool.invalid_arguments",
            "Tool arguments must be a JSON object.",
        )
    for key in schema.get("required", []) or []:
        if key not in arguments:
            return _ValidationError(
                "tool.invalid_arguments",
                f"Missing required argument {key!r}.",
            )
    properties = schema.get("properties") or {}
    if not isinstance(properties, dict):
        return None
    type_map = {
        "string": (str,),
        "integer": (int,),
        "number": (int, float),
        "boolean": (bool,),
        "array": (list, tuple),
        "object": (dict,),
    }
    for key, value in arguments.items():
        prop = properties.get(key)
        if not isinstance(prop, dict):
            continue
        expected = prop.get("type")
        if expected is None:
            continue
        # booleans are ints in Python — special-case number/integer to reject bools.
        if expected in ("integer", "number") and isinstance(value, bool):
            return _ValidationError(
                "tool.invalid_arguments",
                f"Argument {key!r} must be a {expected}.",
            )
        types = type_map.get(expected)
        if types is None:
            continue
        if not isinstance(value, types):
            return _ValidationError(
                "tool.invalid_arguments",
                f"Argument {key!r} must be a {expected}.",
            )
        if expected in ("integer", "number"):
            minimum = prop.get("minimum")
            maximum = prop.get("maximum")
            if minimum is not None and value < minimum:
                return _ValidationError(
                    "tool.invalid_arguments",
                    f"Argument {key!r} must be >= {minimum}.",
                )
            if maximum is not None and value > maximum:
                return _ValidationError(
                    "tool.invalid_arguments",
                    f"Argument {key!r} must be <= {maximum}.",
                )
    return None
