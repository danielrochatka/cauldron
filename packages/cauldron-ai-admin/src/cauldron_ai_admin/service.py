"""AdminAIService — provider-neutral pipeline for admin AI requests.

The service turns a single natural-language request into a bounded
tool-execution loop against an ``AIModelProvider``. Every tool call
produces a persisted ``AdminAIToolInvocation`` row (including denied
calls), and the containing ``AdminAIRun`` records the final state.

Policy summary
--------------
* Unknown tools produce ``tool.unknown``.
* Argument-schema violations produce ``tool.invalid_arguments``.
* Non-JSON-serialisable tool arguments produce ``tool.invalid_arguments``.
* Overlong argument payloads produce ``tool.arguments_too_large``.
* Missing Django permission produces ``tool.permission_denied``.
* Duplicate tool-call ids inside a run produce ``tool.duplicate_call_id``.
* Exceeding ``max_model_turns`` produces ``run.max_turns_exceeded``.
* Exceeding ``max_tool_calls`` produces ``run.max_tool_calls_exceeded``.
* Run deadline exceeded produces ``run.timeout`` (before provider) or
  ``tool.timeout`` (before a specific tool executes).
* Provider ``stop_reason == "max_tokens"`` produces ``provider.max_tokens``.
* Provider ``stop_reason == "timeout"`` produces ``provider.timeout``.
* Malformed provider response produces ``provider.invalid_response``.
* Oversized provider content/tool-call payload produces
  ``provider.response_too_large``.
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
from datetime import datetime, timedelta, timezone
from typing import Any

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import F
from django.utils import timezone as django_timezone

from cauldron_ai.contracts import (
    AIModelMessage,
    AIModelRequest,
    AIModelResponse,
    AIModelToolCall,
    AIModelToolDefinition,
)
from cauldron_ai.providers import AIModelProvider

from .models import (
    AdminAIRun,
    AdminAIToolInvocation,
    ConcurrentModificationError,
)
from .redaction import bound_utf8, redact, redact_exception
from .tools import (
    AdminAIToolContext,
    AdminAIToolDefinition,
    AdminAIToolError,
    AdminAIToolRegistry,
    AdminAIToolResult,
    RiskLevel,
    ToolArgumentValidationError,
    get_tool_registry,
    validate_tool_arguments,
)


logger = logging.getLogger(__name__)


DEFAULT_SYSTEM_PROMPT = (
    "You are a cautious Cauldron admin assistant. You have access to a "
    "restricted set of tools. Prefer read-only inspection first, and "
    "use PROPOSE-level tools only when a change is needed — proposals "
    "must be reviewed by a human before they are applied."
)

# The service-level permission that gates AdminAIService.run() entirely.
ADMIN_AI_PERMISSION = "cauldron_ai_admin.use_admin_ai"

# Minimum meaningful remaining-deadline before a mutation is allowed.
_DEADLINE_EPSILON = 0.1

# Hard caps on model-supplied identifiers. Anything longer is refused
# outright (never truncated) — a runaway ID is almost always a bug in the
# provider adapter or a hostile response.
MAX_TOOL_CALL_ID_BYTES = 256
MAX_TOOL_NAME_BYTES = 128
# Caller-supplied correlation IDs are truncated (never rejected) since
# they originate from local trusted callers.
MAX_CORRELATION_ID_BYTES = 128


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

    @property
    def tool_timeout_seconds(self) -> float:
        return self._tool_timeout_seconds

    @property
    def max_result_bytes(self) -> int:
        return self._max_result_bytes

    def run(
        self,
        actor: Any,
        request_text: str,
        *,
        correlation_id: str = "",
    ) -> AdminAIRun:
        """Execute the full pipeline for a single natural-language request."""
        if actor is None or not getattr(actor, "is_active", False):
            raise PermissionDenied(
                "Admin AI requires an authenticated, active actor."
            )
        # Service-level authorization: PermissionDenied fires before any
        # row is created so unauthorized calls don't pollute audit history.
        has_perm = False
        try:
            has_perm = bool(actor.has_perm(ADMIN_AI_PERMISSION))
        except Exception:  # pragma: no cover - defensive
            has_perm = False
        if not has_perm:
            raise PermissionDenied(
                f"Actor lacks {ADMIN_AI_PERMISSION} permission."
            )

        if not isinstance(request_text, str) or not request_text.strip():
            raise ValueError("request_text must be a non-empty string.")

        # Redact and truncate the user request before persisting so any
        # sensitive key/value fragments never enter the durable audit row.
        bounded_request = redact(request_text, max_bytes=self._max_argument_bytes)

        # Caller-supplied correlation_id: truncate at 128 UTF-8 bytes.
        safe_correlation_id = bound_utf8(correlation_id or "", 128)

        run = AdminAIRun.objects.create(
            actor=actor,
            status="created",
            provider_name=getattr(self._provider, "name", "") or "unknown",
            user_request=bounded_request,
            correlation_id=safe_correlation_id,
        )
        run.started_at = django_timezone.now()
        run.status = "running"
        run.save(update_fields=["started_at", "status"])

        deadline = datetime.now(tz=timezone.utc) + timedelta(
            seconds=self._run_timeout_seconds
        )

        try:
            self._execute_loop(run, actor, request_text, correlation_id, deadline)
        except PermissionDenied:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Admin AI run %s crashed", run.run_id)
            self._finalize_error(
                run, "run.internal_error", redact_exception(exc)
            )
        return run

    # --------------------------------------------------------------- internals

    def _execute_loop(
        self,
        run: AdminAIRun,
        actor: Any,
        request_text: str,
        correlation_id: str,
        deadline: datetime,
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

        for turn_index in range(self._max_model_turns):
            if datetime.now(tz=timezone.utc) >= deadline:
                self._finalize_error(run, "run.timeout", "Run exceeded time budget.")
                return

            remaining = (deadline - datetime.now(tz=timezone.utc)).total_seconds()
            provider_request = AIModelRequest(
                messages=tuple(messages),
                tools=tool_defs_for_model,
                system=self._system_prompt,
                max_tokens=4096,
                timeout_seconds=self._tool_timeout_seconds,
                correlation_id=correlation_id or "",
                deadline_seconds=max(remaining, 0.1),
            )
            try:
                response = self._provider.complete(provider_request)
            except Exception as exc:
                self._finalize_error(
                    run, "provider.error", redact_exception(exc)
                )
                return

            validation_code = self._validate_provider_response(response)
            if validation_code is not None:
                self._finalize_error(
                    run, validation_code, f"Provider response rejected: {validation_code}",
                )
                return

            # Enforce hard caps on model-supplied tool-call IDs and tool
            # names BEFORE any per-invocation row is created. Oversized or
            # empty values are always refusals — never silently truncated.
            for tc in response.tool_calls:
                if not tc.id:
                    self._finalize_error(
                        run, "provider.invalid_response",
                        "Empty tool-call ID from provider.",
                    )
                    return
                if len(tc.id.encode("utf-8")) > MAX_TOOL_CALL_ID_BYTES:
                    self._finalize_error(
                        run, "provider.invalid_response",
                        f"Tool-call ID exceeds {MAX_TOOL_CALL_ID_BYTES} bytes.",
                    )
                    return
                if len(tc.name.encode("utf-8")) > MAX_TOOL_NAME_BYTES:
                    self._finalize_error(
                        run, "tool.unknown",
                        f"Tool name exceeds {MAX_TOOL_NAME_BYTES} bytes.",
                    )
                    return

            # Persist provider request id on the run once we have it.
            if response.provider_request_id and not run.provider_request_id:
                run.provider_request_id = bound_utf8(response.provider_request_id, 256)
                run.save(update_fields=["provider_request_id"])

            if not response.tool_calls:
                # No tool calls; treat as the final answer. Must be end_turn.
                if response.stop_reason != "end_turn":
                    self._finalize_error(
                        run, "provider.invalid_response",
                        "Final response must have stop_reason 'end_turn'.",
                    )
                    return
                # Redact before persisting: models can echo back sensitive
                # tokens from the conversation history.
                bounded = redact(
                    response.content or "", max_bytes=self._max_result_bytes,
                )
                self._finalize_success(run, bounded)
                return

            # Handle every tool call the model returned this turn.
            # Append the assistant message that carries the tool calls first.
            messages.append(AIModelMessage(
                role="assistant",
                content=response.content or "",
                tool_calls=tuple(response.tool_calls),
            ))

            for call in response.tool_calls:
                if tool_calls_made >= self._max_tool_calls:
                    self._finalize_error(
                        run,
                        "run.max_tool_calls_exceeded",
                        "Model exceeded the per-run tool-call budget.",
                    )
                    return

                if call.id in seen_call_ids:
                    # Avoid tripping the unique constraint on (run, tool_call_id):
                    # persist the denial with an empty tool_call_id so the
                    # audit row still lands.
                    self._record_denied_invocation(
                        run,
                        AIModelToolCall(
                            id=call.id, name=call.name,
                            arguments=_to_plain_json(call.arguments),
                        ),
                        risk_level=RiskLevel.READ_ONLY,
                        error_code="tool.duplicate_call_id",
                        message="Duplicate tool-call id within the same run.",
                        drop_tool_call_id=True,
                    )
                    self._finalize_error(
                        run,
                        "tool.duplicate_call_id",
                        f"Duplicate tool call id.",
                    )
                    return
                seen_call_ids.add(call.id)

                tool_calls_made += 1
                tool_message = self._handle_tool_call(
                    run, actor, call, correlation_id, deadline,
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

    def _validate_provider_response(self, response: Any) -> str | None:
        """Return a stable error code if *response* is unusable, else None."""
        if not isinstance(response, AIModelResponse):
            return "provider.invalid_response"
        if response.stop_reason == "max_tokens":
            return "provider.max_tokens"
        if response.stop_reason == "timeout":
            return "provider.timeout"
        # Duplicate tool-call ids within one response are always malformed.
        ids = [tc.id for tc in response.tool_calls]
        if len(ids) != len(set(ids)):
            return "provider.invalid_response"
        # tool_calls without tool_use is a protocol violation.
        if response.tool_calls and response.stop_reason != "tool_use":
            return "provider.invalid_response"
        # Size checks: content or full tool-call payload must fit budget.
        content_bytes = len((response.content or "").encode("utf-8"))
        if content_bytes > self._max_result_bytes:
            return "provider.response_too_large"
        for tc in response.tool_calls:
            try:
                enc = json.dumps(
                    _to_plain_json(tc.arguments), ensure_ascii=False,
                    allow_nan=False,
                ).encode("utf-8")
            except (TypeError, ValueError):
                return "provider.invalid_response"
            if len(enc) > self._max_result_bytes:
                return "provider.response_too_large"
        return None

    def _handle_tool_call(
        self,
        run: AdminAIRun,
        actor: Any,
        call: AIModelToolCall,
        correlation_id: str,
        deadline: datetime,
    ) -> AIModelMessage:
        entry = self._tool_registry.get(call.name)
        if entry is None:
            self._record_denied_invocation(
                run,
                call,
                risk_level=RiskLevel.READ_ONLY,
                error_code="tool.unknown",
                message=f"Unknown tool.",
            )
            self._finalize_error(run, "tool.unknown", f"Unknown tool {call.name!r}.")
            return _tool_error_message(call.id, "tool.unknown", "Unknown tool.")

        definition, handler = entry

        arg_bytes = _payload_bytes(call.arguments)
        if arg_bytes > self._max_argument_bytes:
            self._record_denied_invocation(
                run, call, definition.risk_level,
                error_code="tool.arguments_too_large",
                message="Argument payload exceeds limit.",
                definition=definition,
            )
            self._finalize_error(
                run, "tool.arguments_too_large",
                "Argument payload for tool call is too large.",
            )
            return _tool_error_message(
                call.id, "tool.arguments_too_large", "Arguments too large."
            )

        # JSON-Schema validation: full Draft-07 semantics via jsonschema.
        try:
            validate_tool_arguments(definition.argument_schema, call.arguments)
        except ToolArgumentValidationError as exc:
            self._record_denied_invocation(
                run, call, definition.risk_level,
                error_code="tool.invalid_arguments",
                message=exc.stable_message,
                definition=definition,
            )
            self._finalize_error(
                run, "tool.invalid_arguments", exc.stable_message,
            )
            return _tool_error_message(
                call.id, "tool.invalid_arguments", exc.stable_message,
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
                message="Actor lacks required permission.",
                definition=definition,
            )
            self._finalize_error(
                run, "tool.permission_denied",
                "Actor lacks required tool permission.",
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
                "Tool requires human approval.",
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
                "Tool is restricted.",
            )
            return _tool_error_message(
                call.id, "restricted", "Restricted tool.",
            )

        # Deadline check before execution.
        remaining = (deadline - datetime.now(tz=timezone.utc)).total_seconds()
        if remaining <= 0:
            invocation = self._new_invocation_row(
                run, call, definition, status="requested",
                correlation_id=correlation_id,
            )
            invocation.status = "authorized"
            invocation.save(update_fields=["status"])
            invocation.status = "timed_out"
            invocation.error_code = "tool.timeout"
            invocation.result_summary = redact(
                "Run deadline exceeded before tool execution.", max_bytes=1024,
            )
            invocation.completed_at = django_timezone.now()
            invocation.duration_ms = 0
            invocation.save(update_fields=[
                "status", "error_code", "result_summary",
                "completed_at", "duration_ms",
            ])
            run.tool_call_count = run.tool_call_count + 1
            run.save(update_fields=["tool_call_count"])
            self._finalize_error(
                run, "tool.timeout",
                "Run deadline exceeded before tool execution.",
            )
            return _tool_error_message(
                call.id, "tool.timeout", "Tool execution deadline exceeded.",
            )

        # Execute (READ_ONLY / PROPOSE).
        # Status transitions: requested → authorized → running → completed/failed/timed_out
        invocation = self._new_invocation_row(
            run, call, definition, status="requested",
            correlation_id=correlation_id,
        )
        invocation.status = "authorized"
        invocation.save(update_fields=["status"])
        invocation.status = "running"
        invocation.started_at = django_timezone.now()
        invocation.save(update_fields=["status", "started_at"])

        effective_deadline = min(
            deadline,
            datetime.now(tz=timezone.utc) + timedelta(
                seconds=min(definition.timeout_seconds, self._tool_timeout_seconds)
            ),
        )
        context = AdminAIToolContext(
            actor=actor,
            run_id=str(run.run_id),
            correlation_id=correlation_id or "",
            content_service=self._content_service,
            deadline=effective_deadline,
            dry_run=False,
        )

        # Per-tool byte budget is bounded by the service max.
        effective_max_bytes = min(
            definition.max_output_bytes, self._max_result_bytes,
        )

        t0 = time.monotonic()
        try:
            outcome = handler(context, **_to_plain_json(call.arguments))
        except Exception as exc:
            duration_ms = int((time.monotonic() - t0) * 1000)
            invocation.status = "failed"
            invocation.error_code = "tool.handler_exception"
            invocation.result_summary = redact_exception(exc, max_bytes=1024)
            invocation.duration_ms = duration_ms
            invocation.completed_at = django_timezone.now()
            invocation.save(update_fields=[
                "status", "error_code", "result_summary",
                "duration_ms", "completed_at",
            ])
            run.tool_call_count = run.tool_call_count + 1
            run.save(update_fields=["tool_call_count"])
            return _tool_error_message(
                call.id, "tool.handler_exception",
                "Tool handler raised an exception.",
            )

        duration_ms = int((time.monotonic() - t0) * 1000)
        invocation.duration_ms = duration_ms
        invocation.completed_at = django_timezone.now()

        # Post-handler deadline check: if the handler ran past the
        # effective deadline, refuse to treat its result as authoritative
        # — record as timed_out and fail the run.
        now_after = datetime.now(tz=timezone.utc)
        if now_after > effective_deadline:
            invocation.status = "timed_out"
            invocation.error_code = "tool.timeout"
            invocation.result_summary = redact(
                "Tool ran past run deadline.", max_bytes=1024,
            )
            invocation.save(update_fields=[
                "status", "error_code", "result_summary",
                "duration_ms", "completed_at",
            ])
            run.tool_call_count = run.tool_call_count + 1
            run.save(update_fields=["tool_call_count"])
            self._finalize_error(
                run, "tool.timeout",
                "Tool ran past the run deadline.",
            )
            return _tool_error_message(
                call.id, "tool.timeout",
                "Tool execution deadline exceeded.",
            )

        if isinstance(outcome, AdminAIToolError):
            # Tool-name mismatch on an error return is a handler contract
            # violation — record as bad_return_type and fail the run so an
            # operator can trace the mis-behaving handler.
            if outcome.tool_name != definition.name:
                invocation.status = "failed"
                invocation.error_code = "tool.bad_return_type"
                invocation.result_summary = redact(
                    "Handler returned AdminAIToolError with mismatched tool_name.",
                    max_bytes=1024,
                )
                invocation.save(update_fields=[
                    "status", "error_code", "result_summary",
                    "duration_ms", "completed_at",
                ])
                run.tool_call_count = run.tool_call_count + 1
                run.save(update_fields=["tool_call_count"])
                self._finalize_error(
                    run, "tool.bad_return_type",
                    "Handler returned AdminAIToolError with mismatched tool_name.",
                )
                return _tool_error_message(
                    call.id, "tool.bad_return_type",
                    "Tool returned mismatched tool_name.",
                )
            invocation.status = "failed"
            invocation.error_code = bound_utf8(outcome.error_code, 128)
            invocation.result_summary = redact(outcome.message, max_bytes=1024)
            invocation.save(update_fields=[
                "status", "error_code", "result_summary",
                "duration_ms", "completed_at",
            ])
            run.tool_call_count = run.tool_call_count + 1
            run.save(update_fields=["tool_call_count"])
            return _tool_error_message(call.id, outcome.error_code, outcome.message)

        # Success.
        if not isinstance(outcome, AdminAIToolResult):
            invocation.status = "failed"
            invocation.error_code = "tool.bad_return_type"
            invocation.result_summary = f"{type(outcome).__name__}"[:1024]
            invocation.save(update_fields=[
                "status", "error_code", "result_summary",
                "duration_ms", "completed_at",
            ])
            run.tool_call_count = run.tool_call_count + 1
            run.save(update_fields=["tool_call_count"])
            return _tool_error_message(
                call.id, "tool.bad_return_type",
                "Tool returned unsupported type.",
            )

        # tool_name mismatch
        if outcome.tool_name != definition.name:
            invocation.status = "failed"
            invocation.error_code = "tool.bad_return_type"
            invocation.result_summary = "Handler returned mismatched tool_name."
            invocation.save(update_fields=[
                "status", "error_code", "result_summary",
                "duration_ms", "completed_at",
            ])
            run.tool_call_count = run.tool_call_count + 1
            run.save(update_fields=["tool_call_count"])
            return _tool_error_message(
                call.id, "tool.bad_return_type",
                "Tool returned mismatched tool_name.",
            )

        # success flag must be True
        if outcome.success is not True:
            invocation.status = "failed"
            invocation.error_code = "tool.bad_return_type"
            invocation.result_summary = "Handler returned AdminAIToolResult with success=False."
            invocation.save(update_fields=[
                "status", "error_code", "result_summary",
                "duration_ms", "completed_at",
            ])
            run.tool_call_count = run.tool_call_count + 1
            run.save(update_fields=["tool_call_count"])
            return _tool_error_message(
                call.id, "tool.bad_return_type",
                "Tool returned unexpected success flag.",
            )

        # JSON-serialisability check (no default=str).
        try:
            data_encoded = json.dumps(outcome.data, sort_keys=True, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            invocation.status = "failed"
            invocation.error_code = "tool.bad_return_type"
            invocation.result_summary = "Tool result is not JSON-serialisable."
            invocation.save(update_fields=[
                "status", "error_code", "result_summary",
                "duration_ms", "completed_at",
            ])
            run.tool_call_count = run.tool_call_count + 1
            run.save(update_fields=["tool_call_count"])
            return _tool_error_message(
                call.id, "tool.bad_return_type",
                "Tool result is not JSON-serialisable.",
            )

        # Compose the full tool result payload to send back to the model.
        payload = {
            "tool_name": outcome.tool_name,
            "success": True,
            "data": outcome.data,
            "message": outcome.message,
        }
        try:
            payload_encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):  # pragma: no cover - covered by earlier check
            payload_encoded = data_encoded
        payload_bytes = len(payload_encoded.encode("utf-8"))
        if payload_bytes > effective_max_bytes:
            invocation.status = "failed"
            invocation.error_code = "tool.result_too_large"
            invocation.result_summary = (
                f"Result {payload_bytes}B exceeds {effective_max_bytes}B limit."
            )
            invocation.save(update_fields=[
                "status", "error_code", "result_summary",
                "duration_ms", "completed_at",
            ])
            run.tool_call_count = run.tool_call_count + 1
            run.save(update_fields=["tool_call_count"])
            return _tool_error_message(
                call.id, "tool.result_too_large", "Result too large.",
            )

        invocation.status = "completed"
        invocation.result_summary = redact(outcome.data, max_bytes=1024)
        invocation.save(update_fields=[
            "status", "result_summary", "duration_ms", "completed_at",
        ])
        run.tool_call_count = run.tool_call_count + 1
        run.save(update_fields=["tool_call_count"])

        return AIModelMessage(
            role="tool",
            content=bound_utf8(payload_encoded, effective_max_bytes),
            tool_call_id=call.id,
        )

    # -------------------------------------------------------------- finalizers

    def _finalize_success(self, run: AdminAIRun, content: str) -> None:
        self._compare_and_finalize(
            run,
            new_status="completed",
            final_response=content or "",
            error_code="",
            error_summary="",
        )

    def _finalize_error(self, run: AdminAIRun, code: str, summary: str) -> None:
        self._compare_and_finalize(
            run,
            new_status="failed",
            final_response=None,
            error_code=code,
            error_summary=summary,
        )

    def _finalize_waiting_for_approval(
        self, run: AdminAIRun, code: str, summary: str,
    ) -> None:
        self._compare_and_finalize(
            run,
            new_status="waiting_for_approval",
            final_response=None,
            error_code=code,
            error_summary=summary,
        )

    def _compare_and_finalize(
        self,
        run: AdminAIRun,
        *,
        new_status: str,
        final_response: str | None,
        error_code: str,
        error_summary: str,
    ) -> None:
        """Perform an optimistic-concurrency finalization of *run*.

        Uses ``UPDATE ... WHERE version=<current>`` semantics: the row is
        atomically written iff its version hasn't advanced. On success we
        refresh the in-memory instance so callers observe the new state.
        """
        current_version = run.version
        completed_at = django_timezone.now()
        updates: dict[str, Any] = {
            "status": new_status,
            "version": F("version") + 1,
            "completed_at": completed_at,
            "error_code": bound_utf8(error_code, 128),
            "error_summary": redact(error_summary, max_bytes=512),
        }
        if final_response is not None:
            # Redact the final response before persistence — the model may
            # echo sensitive substrings from the conversation history.
            updates["final_response"] = redact(
                final_response, max_bytes=self._max_result_bytes,
            )

        with transaction.atomic():
            rows = AdminAIRun.objects.filter(
                run_id=run.run_id, version=current_version,
            ).update(**updates)
        if rows == 0:
            raise ConcurrentModificationError(
                f"AdminAIRun {run.run_id} was modified concurrently."
            )
        run.refresh_from_db()

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
        drop_tool_call_id: bool = False,
    ) -> None:
        # Reject oversized tool_call_id before persistence (max 256 chars).
        raw_tool_call_id = "" if drop_tool_call_id else (call.id or "")
        if len(raw_tool_call_id) > 256:
            raw_tool_call_id = ""  # drop it — better than truncating
        invocation = AdminAIToolInvocation.objects.create(
            run=run,
            tool_call_id=raw_tool_call_id,
            tool_name=bound_utf8(call.name or "", 128) or "unknown",
            tool_version=(definition.version if definition else ""),
            owning_module=(definition.owning_module if definition else ""),
            required_permission=(
                definition.required_permission if definition else ""
            ),
            correlation_id=(run.correlation_id or "")[:128],
            risk_level=risk_level.value,
            status=status,
            arguments_hash=_hash_arguments(call.arguments),
            argument_summary=redact(call.arguments, max_bytes=512),
            error_code=bound_utf8(error_code, 128),
            result_summary=redact(message, max_bytes=1024),
            duration_ms=0,
            completed_at=django_timezone.now(),
        )
        run.tool_call_count = run.tool_call_count + 1
        run.save(update_fields=["tool_call_count"])
        return None

    def _new_invocation_row(
        self,
        run: AdminAIRun,
        call: AIModelToolCall,
        definition: AdminAIToolDefinition,
        *,
        status: str,
        correlation_id: str,
    ) -> AdminAIToolInvocation:
        raw_tool_call_id = call.id or ""
        if len(raw_tool_call_id) > 256:
            raw_tool_call_id = ""
        return AdminAIToolInvocation.objects.create(
            run=run,
            tool_call_id=raw_tool_call_id,
            tool_name=definition.name,
            tool_version=definition.version,
            owning_module=definition.owning_module,
            required_permission=definition.required_permission,
            correlation_id=(correlation_id or run.correlation_id or "")[:128],
            risk_level=definition.risk_level.value,
            status=status,
            arguments_hash=_hash_arguments(call.arguments),
            argument_summary=redact(call.arguments, max_bytes=512),
        )


# ---------------------------------------------------------------------- helpers


def _to_plain_json(value: Any) -> Any:
    """Convert deep-frozen MappingProxy/tuple views back to plain dict/list.

    Needed so :func:`json.dumps` (and anything else that special-cases
    ``dict``) sees the expected concrete types after
    :func:`cauldron_ai.contracts._deep_freeze` has been applied.
    """
    from collections.abc import Mapping as _M
    if isinstance(value, _M):
        return {str(k): _to_plain_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_plain_json(v) for v in value]
    return value


def _to_model_tool_definition(defn: AdminAIToolDefinition) -> AIModelToolDefinition:
    return AIModelToolDefinition(
        name=defn.name,
        description=defn.description,
        parameters=_to_plain_json(defn.argument_schema),
    )


def _tool_error_message(call_id: str, code: str, message: str) -> AIModelMessage:
    payload = json.dumps({
        "success": False,
        "error_code": code,
        "message": message,
    }, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return AIModelMessage(role="tool", content=payload, tool_call_id=call_id)


def _payload_bytes(payload: Any) -> int:
    try:
        return len(
            json.dumps(
                _to_plain_json(payload), sort_keys=True, ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        )
    except (TypeError, ValueError):
        return len(str(payload).encode("utf-8"))


def _hash_arguments(arguments: Any) -> str:
    try:
        serialised = json.dumps(
            _to_plain_json(arguments), sort_keys=True, ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        serialised = repr(arguments)
    return hashlib.sha256(serialised.encode("utf-8")).hexdigest()
