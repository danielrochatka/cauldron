"""Provider error classification, retry logic, deadline guards, and diagnostics."""
from __future__ import annotations

import json
from unittest import mock

import pytest

from cauldron_ai.contracts import (
    AIModelMessage,
    AIModelRequest,
    AIModelResponse,
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
from cauldron_ai_admin.service import (
    AdminAIService,
    _bound_diagnostic_summary,
    _build_provider_error_summary,
    _classify_provider_error,
    _measure_request_bytes,
)
from cauldron_ai_admin.service_factory import EXECUTION_BUDGET_DEFAULTS
from cauldron_ai_admin.tools import (
    AdminAIToolContext,
    AdminAIToolDefinition,
    AdminAIToolRegistry,
    AdminAIToolResult,
    RiskLevel,
)
from helpers import make_assembly_service_for_tools

pytestmark = pytest.mark.django_db

_GOOD_RESPONSE = AIModelResponse(
    provider_request_id="r1",
    content="Done.",
    stop_reason="end_turn",
)


def _mock_provider(*side_effects):
    p = mock.MagicMock()
    p.name = "fake"
    p.model = "fake-model"
    p.complete.side_effect = list(side_effects)
    return p


def _service(provider, *, run_timeout_seconds=30, tool_timeout_seconds=10):
    asm = make_assembly_service_for_tools()
    return AdminAIService(
        provider=provider,
        tool_registry=AdminAIToolRegistry(),
        run_timeout_seconds=run_timeout_seconds,
        tool_timeout_seconds=tool_timeout_seconds,
        prompt_assembly_service=asm,
    )


def _make_user():
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    User = get_user_model()
    user, _ = User.objects.get_or_create(username="pr-retry-user")
    perm = Permission.objects.get(
        codename="use_admin_ai",
        content_type__app_label="cauldron_ai_admin",
    )
    user.user_permissions.add(perm)
    return User.objects.get(pk=user.pk)


# ---------------------------------------------------------------------------
# classify_provider_error unit tests
# ---------------------------------------------------------------------------

def test_classify_connection_error_is_retryable():
    code, retryable = _classify_provider_error(AIProviderConnectionError("net fail"))
    assert code == "provider.connection_error"
    assert retryable is True


def test_classify_timeout_error_is_provider_timeout():
    code, retryable = _classify_provider_error(AIProviderTimeoutError("timed out"))
    assert code == "provider.timeout"
    assert retryable is True


def test_classify_timeout_and_connection_produce_different_codes():
    t_code, _ = _classify_provider_error(AIProviderTimeoutError("timed out"))
    c_code, _ = _classify_provider_error(AIProviderConnectionError("net fail"))
    assert t_code == "provider.timeout"
    assert c_code == "provider.connection_error"
    assert t_code != c_code


def test_classify_rate_limit_error_is_retryable():
    code, retryable = _classify_provider_error(AIProviderRateLimitError("429"))
    assert code == "provider.rate_limited"
    assert retryable is True


def test_classify_auth_error_is_not_retryable():
    code, retryable = _classify_provider_error(AIProviderAuthenticationError("bad key"))
    assert code == "provider.authentication_error"
    assert retryable is False


def test_classify_configuration_error_is_not_retryable():
    code, retryable = _classify_provider_error(AIProviderConfigurationError("bad config"))
    assert code == "provider.configuration_error"
    assert retryable is False


def test_classify_unknown_exception_is_internal_error_not_retryable():
    code, retryable = _classify_provider_error(RuntimeError("surprise"))
    assert code == "provider.internal_error"
    assert retryable is False


def test_classify_response_error_with_5xx_is_server_error_and_retryable():
    exc = AIProviderResponseError("server boom", http_status=503)
    code, retryable = _classify_provider_error(exc)
    assert code == "provider.server_error"
    assert retryable is True


def test_classify_response_error_with_429_is_rate_limited_and_retryable():
    exc = AIProviderResponseError("rate limit", http_status=429)
    code, retryable = _classify_provider_error(exc)
    assert code == "provider.rate_limited"
    assert retryable is True


def test_classify_response_error_without_status_is_invalid_request_not_retryable():
    code, retryable = _classify_provider_error(AIProviderResponseError("bad body"))
    assert code == "provider.invalid_request"
    assert retryable is False


# ---------------------------------------------------------------------------
# Provider timeout calc: remaining run deadline used as HTTP timeout
# ---------------------------------------------------------------------------

def test_provider_request_uses_remaining_deadline_not_tool_timeout():
    """The provider HTTP timeout must derive from remaining run time, not tool_timeout."""
    captured = []

    def _capture(request):
        captured.append(request)
        return _GOOD_RESPONSE

    provider = mock.MagicMock()
    provider.name = "fake"
    provider.model = "fake"
    provider.complete.side_effect = _capture

    # run_timeout_seconds=60, tool_timeout_seconds=10.
    # Provider request must carry timeout_seconds close to 60 (remaining), not 10.
    svc = AdminAIService(
        provider=provider,
        tool_registry=AdminAIToolRegistry(),
        run_timeout_seconds=60,
        tool_timeout_seconds=10,
        prompt_assembly_service=make_assembly_service_for_tools(),
    )
    user = _make_user()
    svc.run(user, "Hello")

    assert len(captured) == 1
    req = captured[0]
    # timeout_seconds must be >= remaining deadline (close to 60), not capped at 10.
    assert req.timeout_seconds > 10
    # And deadline_seconds must equal timeout_seconds (both carry the run budget).
    assert req.deadline_seconds == req.timeout_seconds


def test_tool_execution_still_uses_tool_timeout_seconds():
    """Tool execution deadline must still respect tool_timeout_seconds."""
    from cauldron_ai_admin.tools import (
        AdminAIToolDefinition,
        AdminAIToolResult,
        RiskLevel,
    )

    context_snapshots: list[AdminAIToolContext] = []

    def _handler(context: AdminAIToolContext, **kwargs):
        context_snapshots.append(context)
        return AdminAIToolResult(tool_name="my.tool", success=True, data={})

    reg = AdminAIToolRegistry()
    defn = AdminAIToolDefinition(
        name="my.tool",
        version="1.0",
        description="test",
        argument_schema={"type": "object", "properties": {}},
        risk_level=RiskLevel.READ_ONLY,
        required_permission="auth.view_user",
        owning_module="test.module",
    )
    reg.register(defn, _handler)

    asm = make_assembly_service_for_tools("my.tool")
    provider = mock.MagicMock()
    provider.name = "fake"
    provider.model = "fake"
    provider.complete.side_effect = [
        AIModelResponse(
            provider_request_id="r1",
            content="",
            stop_reason="tool_use",
            tool_calls=(AIModelToolCall(id="c1", name="my.tool", arguments={}),),
        ),
        _GOOD_RESPONSE,
    ]

    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    User = get_user_model()
    user, _ = User.objects.get_or_create(username="tool-timeout-user")
    for spec in ("auth.view_user", "cauldron_ai_admin.use_admin_ai"):
        app_label, codename = spec.split(".", 1)
        try:
            perm = Permission.objects.get(
                codename=codename, content_type__app_label=app_label,
            )
            user.user_permissions.add(perm)
        except Permission.DoesNotExist:
            pass
    user = User.objects.get(pk=user.pk)

    svc = AdminAIService(
        provider=provider,
        tool_registry=reg,
        run_timeout_seconds=60,
        tool_timeout_seconds=10,
        prompt_assembly_service=asm,
    )
    svc.run(user, "Call my tool.")

    assert len(context_snapshots) == 1
    ctx = context_snapshots[0]
    # The effective tool deadline must be bounded by tool_timeout_seconds (10s),
    # not the full run budget (60s).
    from datetime import datetime, timezone
    remaining_on_tool_deadline = (ctx.deadline - datetime.now(tz=timezone.utc)).total_seconds()
    # Deadline is in the near future (within ~10 + small margin seconds).
    assert remaining_on_tool_deadline < 15


# ---------------------------------------------------------------------------
# Pre-call deadline guard
# ---------------------------------------------------------------------------

def test_pre_call_deadline_too_short_produces_provider_timeout():
    """When remaining time < _MIN_PROVIDER_CALL_DEADLINE, fail before calling provider."""
    provider = _mock_provider()
    svc = _service(provider, run_timeout_seconds=3)
    user = _make_user()

    run = svc.run(user, "Hello")

    assert run.status == "failed"
    assert run.error_code == "provider.timeout"
    assert provider.complete.call_count == 0


# ---------------------------------------------------------------------------
# Retry behaviour
# ---------------------------------------------------------------------------

def test_connection_error_produces_classified_error_code():
    provider = _mock_provider(
        AIProviderConnectionError("net fail"),
        AIProviderConnectionError("net fail again"),
    )
    svc = _service(provider)
    user = _make_user()

    with mock.patch("cauldron_ai_admin.service._PROVIDER_RETRY_BACKOFF", 0):
        run = svc.run(user, "Hello")

    assert run.status == "failed"
    assert run.error_code == "provider.connection_error"


def test_auth_error_is_not_retried():
    provider = _mock_provider(AIProviderAuthenticationError("bad key"))
    svc = _service(provider)
    user = _make_user()

    run = svc.run(user, "Hello")

    assert run.status == "failed"
    assert run.error_code == "provider.authentication_error"
    assert provider.complete.call_count == 1


def test_configuration_error_is_not_retried():
    provider = _mock_provider(AIProviderConfigurationError("bad config"))
    svc = _service(provider)
    user = _make_user()

    run = svc.run(user, "Hello")

    assert run.status == "failed"
    assert run.error_code == "provider.configuration_error"
    assert provider.complete.call_count == 1


def test_unknown_exception_is_not_retried():
    provider = _mock_provider(RuntimeError("unexpected"))
    svc = _service(provider)
    user = _make_user()

    run = svc.run(user, "Hello")

    assert run.status == "failed"
    assert run.error_code == "provider.internal_error"
    assert provider.complete.call_count == 1


def test_connection_error_is_retried_once_when_deadline_allows():
    provider = _mock_provider(
        AIProviderConnectionError("net fail"),
        AIProviderConnectionError("net fail again"),
    )
    svc = _service(provider)
    user = _make_user()

    with mock.patch("cauldron_ai_admin.service._PROVIDER_RETRY_BACKOFF", 0):
        run = svc.run(user, "Hello")

    assert run.status == "failed"
    assert run.error_code == "provider.connection_error"
    assert provider.complete.call_count == 2


def test_connection_error_not_retried_when_deadline_too_close():
    """Pre-sleep deadline check blocks retry when backoff would exhaust the budget."""
    provider = _mock_provider(AIProviderConnectionError("net fail"))
    svc = _service(provider)
    user = _make_user()

    with mock.patch("cauldron_ai_admin.service._PROVIDER_RETRY_BACKOFF", 9999):
        run = svc.run(user, "Hello")

    assert run.status == "failed"
    assert run.error_code == "provider.connection_error"
    assert provider.complete.call_count == 1


def test_retry_succeeds_on_second_attempt():
    provider = _mock_provider(AIProviderConnectionError("transient"), _GOOD_RESPONSE)
    svc = _service(provider)
    user = _make_user()

    with mock.patch("cauldron_ai_admin.service._PROVIDER_RETRY_BACKOFF", 0):
        run = svc.run(user, "Hello")

    assert run.status == "completed"
    assert provider.complete.call_count == 2


def test_retry_uses_new_request_with_refreshed_timeout():
    """The retry must create a fresh AIModelRequest, not reuse the stale original."""
    captured = []

    def _side_effect(request):
        captured.append(request)
        if len(captured) == 1:
            raise AIProviderConnectionError("transient")
        return _GOOD_RESPONSE

    provider = mock.MagicMock()
    provider.name = "fake"
    provider.model = "fake"
    provider.complete.side_effect = _side_effect

    svc = _service(provider, run_timeout_seconds=30)
    user = _make_user()

    with mock.patch("cauldron_ai_admin.service._PROVIDER_RETRY_BACKOFF", 0):
        run = svc.run(user, "Hello")

    assert run.status == "completed"
    assert len(captured) == 2
    # The retry must be a distinct object (new request built with refreshed values).
    assert captured[0] is not captured[1]
    # Both carry a positive timeout derived from the run deadline.
    assert captured[0].timeout_seconds > 0
    assert captured[1].timeout_seconds > 0


def test_completed_tools_not_re_executed_during_provider_retry():
    """Tools that completed in a prior turn must not be called again during a provider retry."""
    tool_call_count = 0

    def _handler(context, **kwargs):
        nonlocal tool_call_count
        tool_call_count += 1
        return AdminAIToolResult(tool_name="my.tool", success=True, data={"n": tool_call_count})

    reg = AdminAIToolRegistry()
    defn = AdminAIToolDefinition(
        name="my.tool",
        version="1.0",
        description="test",
        argument_schema={"type": "object", "properties": {}},
        risk_level=RiskLevel.READ_ONLY,
        required_permission="auth.view_user",
        owning_module="test.module",
    )
    reg.register(defn, _handler)

    asm = make_assembly_service_for_tools("my.tool")
    provider = mock.MagicMock()
    provider.name = "fake"
    provider.model = "fake"

    # Turn 1: model returns a tool call.
    # Turn 2 first attempt: provider connection error (retry triggered).
    # Turn 2 retry: provider succeeds with final answer.
    provider.complete.side_effect = [
        AIModelResponse(
            provider_request_id="r1",
            content="",
            stop_reason="tool_use",
            tool_calls=(AIModelToolCall(id="c1", name="my.tool", arguments={}),),
        ),
        AIProviderConnectionError("transient on turn 2"),
        _GOOD_RESPONSE,
    ]

    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    User = get_user_model()
    user, _ = User.objects.get_or_create(username="no-re-exec-user")
    for spec in ("auth.view_user", "cauldron_ai_admin.use_admin_ai"):
        app_label, codename = spec.split(".", 1)
        try:
            perm = Permission.objects.get(
                codename=codename, content_type__app_label=app_label,
            )
            user.user_permissions.add(perm)
        except Permission.DoesNotExist:
            pass
    user = User.objects.get(pk=user.pk)

    svc = AdminAIService(
        provider=provider,
        tool_registry=reg,
        run_timeout_seconds=30,
        tool_timeout_seconds=10,
        prompt_assembly_service=asm,
    )

    with mock.patch("cauldron_ai_admin.service._PROVIDER_RETRY_BACKOFF", 0):
        run = svc.run(user, "Call my tool then answer.")

    assert run.status == "completed"
    # The tool was executed exactly once despite the provider retry on turn 2.
    assert tool_call_count == 1
    assert provider.complete.call_count == 3  # turn1 + retry-fail + retry-success


# ---------------------------------------------------------------------------
# Default run budget
# ---------------------------------------------------------------------------

def test_default_run_timeout_is_120_seconds():
    """The module default must support multi-step workflows without exhausting budget."""
    assert EXECUTION_BUDGET_DEFAULTS["run_timeout_seconds"] == 120.0


def test_explicit_30s_run_timeout_is_respected():
    """An explicit 30-second limit must pass through even though the default is 120s."""
    provider = _mock_provider(_GOOD_RESPONSE)
    svc = _service(provider, run_timeout_seconds=30)
    assert svc._run_timeout_seconds == 30.0


# ---------------------------------------------------------------------------
# Diagnostic summary
# ---------------------------------------------------------------------------

def test_error_summary_contains_safe_diagnostic_json():
    """error_summary must be parseable JSON with all required diagnostic fields."""
    provider = _mock_provider(
        AIProviderConnectionError("net fail"),
        AIProviderConnectionError("again"),
    )
    svc = _service(provider)
    user = _make_user()

    with mock.patch("cauldron_ai_admin.service._PROVIDER_RETRY_BACKOFF", 0):
        run = svc.run(user, "Hello")

    assert run.error_code == "provider.connection_error"
    data = json.loads(run.error_summary)
    for field in ("error_code", "exc_class", "turn", "attempt",
                  "elapsed_ms", "remaining_ms", "request_bytes",
                  "tool_result_bytes", "retryable"):
        assert field in data, f"missing field: {field}"
    assert data["error_code"] == "provider.connection_error"
    assert data["exc_class"] == "AIProviderConnectionError"
    assert data["turn"] == 0
    assert data["attempt"] == 2  # first attempt + one retry
    assert data["retryable"] is True
    assert data["provider_name"] == "fake"
    assert data["model_name"] == "fake-model"


def test_error_summary_excludes_sensitive_data():
    """Exception messages and raw text must never appear in the diagnostic summary."""
    exc = AIProviderConnectionError("secret-token-should-not-leak")
    summary = _build_provider_error_summary(
        exc=exc,
        error_code="provider.connection_error",
        turn=0,
        elapsed_ms=100,
        remaining_ms=25000,
        retryable=True,
        attempt=1,
        request_bytes=512,
        tool_result_bytes=0,
        provider_name="openai",
        model_name="gpt-4o",
    )
    assert "secret-token-should-not-leak" not in summary
    data = json.loads(summary)
    assert data["exc_class"] == "AIProviderConnectionError"
    # No raw string from the exception
    for value in data.values():
        if isinstance(value, str):
            assert "secret-token" not in value


def test_error_summary_includes_http_status_and_request_id_when_available():
    """http_status and provider_request_id from structured exceptions appear in summary."""
    exc = AIProviderRateLimitError(
        "rate limited",
        http_status=429,
        provider_request_id="req-abc",
        retry_after=60.0,
    )
    summary = _build_provider_error_summary(
        exc=exc,
        error_code="provider.rate_limited",
        turn=1,
        elapsed_ms=500,
        remaining_ms=90000,
        retryable=True,
        attempt=1,
    )
    data = json.loads(summary)
    assert data["http_status"] == 429
    assert data["provider_request_id"] == "req-abc"
    assert data["retry_after"] == 60.0


# ---------------------------------------------------------------------------
# request_bytes measurement accuracy
# ---------------------------------------------------------------------------

def _bare_request(**kwargs):
    """Return a minimal AIModelRequest with a single user message."""
    defaults = dict(
        messages=(AIModelMessage(role="user", content="Hi"),),
        system="",
        tools=(),
    )
    defaults.update(kwargs)
    return AIModelRequest(**defaults)


def test_request_bytes_includes_system_prompt():
    """System-prompt bytes must be counted in request_bytes."""
    base = _bare_request(system="")
    with_system = _bare_request(system="A" * 100)
    assert _measure_request_bytes(with_system) > _measure_request_bytes(base)


def test_request_bytes_includes_tool_schema():
    """Tool-definition schema bytes must be counted in request_bytes."""
    base = _bare_request(tools=())
    tool = AIModelToolDefinition(
        name="my.tool",
        description="Does something",
        parameters={
            "type": "object",
            "properties": {"x": {"type": "string", "description": "A" * 200}},
        },
    )
    with_tool = _bare_request(tools=(tool,))
    assert _measure_request_bytes(with_tool) > _measure_request_bytes(base)


def test_request_bytes_includes_tool_call_arguments():
    """Tool-call argument bytes inside messages must be counted in request_bytes."""
    small_args = AIModelToolCall(id="c1", name="tool", arguments={})
    large_args = AIModelToolCall(id="c1", name="tool", arguments={"input": "A" * 200})

    base = AIModelRequest(
        messages=(
            AIModelMessage(role="user", content="Hi"),
            AIModelMessage(role="assistant", content="", tool_calls=(small_args,)),
        ),
    )
    with_args = AIModelRequest(
        messages=(
            AIModelMessage(role="user", content="Hi"),
            AIModelMessage(role="assistant", content="", tool_calls=(large_args,)),
        ),
    )
    assert _measure_request_bytes(with_args) > _measure_request_bytes(base)


def test_request_bytes_counts_unicode_as_utf8():
    """Multi-byte Unicode chars must count their full UTF-8 width in request_bytes."""
    # "€" encodes as 3 bytes in UTF-8; "A" is 1 byte.
    ascii_req = _bare_request(messages=(AIModelMessage(role="user", content="A" * 20),))
    euro_req = _bare_request(messages=(AIModelMessage(role="user", content="€" * 20),))
    assert _measure_request_bytes(euro_req) > _measure_request_bytes(ascii_req)


def test_diagnostic_stores_byte_count_not_content():
    """error_summary must carry request_bytes as a number, never raw request content."""
    provider = _mock_provider(
        AIProviderConnectionError("net fail"),
        AIProviderConnectionError("net fail again"),
    )
    svc = _service(provider)
    user = _make_user()

    with mock.patch("cauldron_ai_admin.service._PROVIDER_RETRY_BACKOFF", 0):
        run = svc.run(user, "do something secret")

    data = json.loads(run.error_summary)
    assert "request_bytes" in data
    assert isinstance(data["request_bytes"], int)
    assert data["request_bytes"] > 0
    # Raw content keys and values must never appear in the summary.
    assert "messages" not in data
    assert "system" not in data
    assert "do something secret" not in run.error_summary


# ---------------------------------------------------------------------------
# _bound_diagnostic_summary — JSON-aware bounding
# ---------------------------------------------------------------------------

_REQUIRED_SUMMARY_FIELDS = (
    "error_code", "turn", "attempt", "elapsed_ms", "remaining_ms",
    "request_bytes", "tool_result_bytes", "retryable",
)


def _base_fields(**overrides) -> dict:
    """Minimal valid diagnostic field dict."""
    base = {
        "error_code": "provider.connection_error",
        "exc_class": "AIProviderConnectionError",
        "turn": 0,
        "attempt": 1,
        "elapsed_ms": 100,
        "remaining_ms": 25000,
        "request_bytes": 512,
        "tool_result_bytes": 0,
        "retryable": True,
    }
    base.update(overrides)
    return base


def test_bound_summary_fits_within_budget_with_no_truncation_flag():
    """A summary that fits naturally must parse cleanly and carry no truncated key."""
    text = _bound_diagnostic_summary(_base_fields(provider_name="openai", model_name="gpt-4o"))
    assert len(text.encode("utf-8")) <= 512
    data = json.loads(text)
    assert "truncated" not in data


def test_bound_summary_always_valid_json_with_max_length_model_name():
    """A 200-char model name must produce valid JSON ≤ 512 bytes."""
    fields = _base_fields(model_name="m" * 200, provider_name="openai")
    text = _bound_diagnostic_summary(fields)
    json.loads(text)  # must not raise
    assert len(text.encode("utf-8")) <= 512


def test_bound_summary_always_valid_json_with_max_length_request_id():
    """A 64-char provider_request_id must produce valid JSON ≤ 512 bytes."""
    fields = _base_fields(
        model_name="gpt-4o",
        provider_request_id="req-" + "x" * 60,
        http_status=503,
        retry_after=120.0,
    )
    text = _bound_diagnostic_summary(fields)
    json.loads(text)  # must not raise
    assert len(text.encode("utf-8")) <= 512


def test_bound_summary_never_exceeds_512_bytes_with_all_optional_fields():
    """Every combination of max-size optional fields must still fit in 512 bytes."""
    fields = _base_fields(
        model_name="m" * 200,
        provider_name="p" * 50,
        provider_request_id="r" * 64,
        exc_class="A" * 80,
        http_status=503,
        retry_after=999.0,
    )
    text = _bound_diagnostic_summary(fields)
    assert len(text.encode("utf-8")) <= 512


def test_bound_summary_required_fields_survive_truncation():
    """Required diagnostic fields must remain present after optional fields are dropped."""
    fields = _base_fields(model_name="m" * 300, provider_request_id="r" * 64)
    text = _bound_diagnostic_summary(fields)
    data = json.loads(text)
    for key in _REQUIRED_SUMMARY_FIELDS:
        assert key in data, f"required field missing after truncation: {key!r}"
    assert data["truncated"] is True


def test_bound_summary_optional_fields_dropped_deterministically():
    """Given the same inputs the bounding function must always produce the same output."""
    fields = _base_fields(
        model_name="m" * 200,
        provider_request_id="r" * 64,
        retry_after=60.0,
        http_status=429,
    )
    assert _bound_diagnostic_summary(fields) == _bound_diagnostic_summary(fields)


def test_bound_summary_excludes_sensitive_content():
    """No exception message text, credentials, or raw content may appear in the summary."""
    fields = _base_fields(
        model_name="gpt-4o",
        provider_name="openai",
        provider_request_id="req-safe-id",
    )
    text = _bound_diagnostic_summary(fields)
    data = json.loads(text)
    # The function only serializes what it's given; verify no raw strings
    # from exception messages or sensitive patterns are injected.
    for value in data.values():
        if isinstance(value, str):
            assert "sk-" not in value
            assert "password" not in value.lower()
            assert "secret" not in value.lower()


def test_persisted_error_summary_is_always_valid_json_with_long_model_name():
    """End-to-end: a long model name must not cause the persisted summary to be invalid JSON."""
    exc = AIProviderConnectionError("net fail")
    summary = _build_provider_error_summary(
        exc=exc,
        error_code="provider.connection_error",
        turn=0,
        elapsed_ms=100,
        remaining_ms=25000,
        retryable=True,
        attempt=2,
        request_bytes=1024,
        tool_result_bytes=0,
        provider_name="openai",
        model_name="gpt-4o-" + "x" * 300,  # 307 chars — forces truncation
    )
    assert len(summary.encode("utf-8")) <= 512
    data = json.loads(summary)
    assert data["error_code"] == "provider.connection_error"
    assert data["truncated"] is True
