"""Tests for the Admin AI execution-budget configuration.

Verifies that budget limits live entirely in cauldron-ai-admin (not in
cauldron_site/settings.py), that EXECUTION_BUDGET_DEFAULTS is the single
source of truth for module defaults, and that the precedence chain
saved settings → deployment cfg → module defaults works correctly across
all six numeric budget keys.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_store(tmp_path):
    from cauldron_ai_admin.provider_config import (
        AIProviderSettingsStore,
        _reset_store_for_tests,
    )
    p = tmp_path / "ai.json"
    _reset_store_for_tests(path=p)
    store = AIProviderSettingsStore(p)
    return store, _reset_store_for_tests


def _make_user():
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    User = get_user_model()
    user, _ = User.objects.get_or_create(username="budget-test-user")
    for spec in ("auth.view_user", "cauldron_ai_admin.use_admin_ai"):
        app_label, codename = spec.split(".", 1)
        try:
            perm = Permission.objects.get(
                codename=codename, content_type__app_label=app_label,
            )
        except Permission.DoesNotExist:
            continue
        user.user_permissions.add(perm)
    return User.objects.get(pk=user.pk)


def _make_service(provider, registry, *, max_tool_calls=12, max_model_turns=8,
                  max_argument_bytes=4096, max_result_bytes=8192):
    from cauldron_ai_admin.service import AdminAIService
    from helpers import make_assembly_service_for_tools
    asm = make_assembly_service_for_tools(
        *[d.name for d in registry.all_definitions()]
    )
    return AdminAIService(
        provider=provider,
        tool_registry=registry,
        max_model_turns=max_model_turns,
        max_tool_calls=max_tool_calls,
        max_argument_bytes=max_argument_bytes,
        max_result_bytes=max_result_bytes,
        prompt_assembly_service=asm,
    )


def _read_tool(name="t.read"):
    from cauldron_ai_admin.tools import (
        AdminAIToolDefinition,
        AdminAIToolResult,
        RiskLevel,
    )
    defn = AdminAIToolDefinition(
        name=name,
        version="1.0",
        description="test read tool",
        argument_schema={"type": "object", "properties": {}},
        risk_level=RiskLevel.READ_ONLY,
        required_permission="auth.view_user",
        owning_module="cauldron.test",
    )
    handler = lambda ctx, **kw: AdminAIToolResult(
        tool_name=name, success=True, data={"ok": True},
    )
    return defn, handler


# ---------------------------------------------------------------------------
# EXECUTION_BUDGET_DEFAULTS
# ---------------------------------------------------------------------------

def test_execution_budget_defaults_max_model_turns():
    from cauldron_ai_admin.service_factory import EXECUTION_BUDGET_DEFAULTS
    assert EXECUTION_BUDGET_DEFAULTS["max_model_turns"] == 8


def test_execution_budget_defaults_max_tool_calls():
    from cauldron_ai_admin.service_factory import EXECUTION_BUDGET_DEFAULTS
    assert EXECUTION_BUDGET_DEFAULTS["max_tool_calls"] == 12


def test_execution_budget_defaults_all_six_numeric_keys_present():
    from cauldron_ai_admin.service_factory import EXECUTION_BUDGET_DEFAULTS
    for key in (
        "max_model_turns", "max_tool_calls", "tool_timeout_seconds",
        "run_timeout_seconds", "max_argument_bytes", "max_result_bytes",
    ):
        assert key in EXECUTION_BUDGET_DEFAULTS, f"Missing key: {key}"


def test_execution_budget_defaults_preserved_numeric_values():
    """Former self-hosted limits in settings.py are now the module defaults."""
    from cauldron_ai_admin.service_factory import EXECUTION_BUDGET_DEFAULTS
    assert EXECUTION_BUDGET_DEFAULTS["tool_timeout_seconds"] == 10.0
    assert EXECUTION_BUDGET_DEFAULTS["run_timeout_seconds"] == 30.0
    assert EXECUTION_BUDGET_DEFAULTS["max_argument_bytes"] == 4096
    assert EXECUTION_BUDGET_DEFAULTS["max_result_bytes"] == 8192


# ---------------------------------------------------------------------------
# resolve_runtime_settings — module defaults
# ---------------------------------------------------------------------------

def test_resolve_runtime_settings_returns_module_defaults_when_nothing_saved(tmp_path):
    from cauldron_ai_admin.service_factory import (
        EXECUTION_BUDGET_DEFAULTS,
        resolve_runtime_settings,
    )
    store, reset = _make_store(tmp_path)
    try:
        result = resolve_runtime_settings(store, {})
        assert result["max_model_turns"] == EXECUTION_BUDGET_DEFAULTS["max_model_turns"]
        assert result["max_tool_calls"] == EXECUTION_BUDGET_DEFAULTS["max_tool_calls"]
        assert result["tool_timeout_seconds"] == EXECUTION_BUDGET_DEFAULTS["tool_timeout_seconds"]
        assert result["run_timeout_seconds"] == EXECUTION_BUDGET_DEFAULTS["run_timeout_seconds"]
        assert result["max_argument_bytes"] == EXECUTION_BUDGET_DEFAULTS["max_argument_bytes"]
        assert result["max_result_bytes"] == EXECUTION_BUDGET_DEFAULTS["max_result_bytes"]
    finally:
        reset(path=None)


def test_resolve_runtime_settings_include_content_tools_defaults_true(tmp_path):
    from cauldron_ai_admin.service_factory import resolve_runtime_settings
    store, reset = _make_store(tmp_path)
    try:
        result = resolve_runtime_settings(store, {})
        assert result["include_content_tools"] is True
    finally:
        reset(path=None)


# ---------------------------------------------------------------------------
# resolve_runtime_settings — deployment cfg overrides defaults
# ---------------------------------------------------------------------------

def test_resolve_runtime_settings_cfg_overrides_max_model_turns(tmp_path):
    from cauldron_ai_admin.service_factory import resolve_runtime_settings
    store, reset = _make_store(tmp_path)
    try:
        result = resolve_runtime_settings(store, {"max_model_turns": 4})
        assert result["max_model_turns"] == 4
    finally:
        reset(path=None)


def test_resolve_runtime_settings_cfg_overrides_max_tool_calls(tmp_path):
    from cauldron_ai_admin.service_factory import resolve_runtime_settings
    store, reset = _make_store(tmp_path)
    try:
        result = resolve_runtime_settings(store, {"max_tool_calls": 6})
        assert result["max_tool_calls"] == 6
    finally:
        reset(path=None)


def test_resolve_runtime_settings_cfg_include_content_tools_false(tmp_path):
    from cauldron_ai_admin.service_factory import resolve_runtime_settings
    store, reset = _make_store(tmp_path)
    try:
        result = resolve_runtime_settings(store, {"include_content_tools": False})
        assert result["include_content_tools"] is False
    finally:
        reset(path=None)


# ---------------------------------------------------------------------------
# resolve_runtime_settings — saved settings override everything
# ---------------------------------------------------------------------------

def test_resolve_runtime_settings_saved_overrides_defaults(tmp_path):
    from cauldron_ai_admin.service_factory import resolve_runtime_settings
    store, reset = _make_store(tmp_path)
    try:
        store.set_runtime({"max_model_turns": 15, "max_tool_calls": 20})
        result = resolve_runtime_settings(store, {})
        assert result["max_model_turns"] == 15
        assert result["max_tool_calls"] == 20
    finally:
        reset(path=None)


def test_resolve_runtime_settings_saved_overrides_cfg(tmp_path):
    from cauldron_ai_admin.service_factory import resolve_runtime_settings
    store, reset = _make_store(tmp_path)
    try:
        store.set_runtime({"max_model_turns": 10})
        result = resolve_runtime_settings(store, {"max_model_turns": 4})
        assert result["max_model_turns"] == 10
    finally:
        reset(path=None)


def test_resolve_runtime_settings_saved_include_content_tools_false_is_honoured(tmp_path):
    from cauldron_ai_admin.service_factory import resolve_runtime_settings
    store, reset = _make_store(tmp_path)
    try:
        store.set_runtime({"include_content_tools": False})
        result = resolve_runtime_settings(store, {})
        assert result["include_content_tools"] is False
    finally:
        reset(path=None)


def test_resolve_runtime_settings_full_precedence_chain(tmp_path):
    """Saved beats cfg beats defaults — verified per-key."""
    from cauldron_ai_admin.service_factory import EXECUTION_BUDGET_DEFAULTS, resolve_runtime_settings
    store, reset = _make_store(tmp_path)
    try:
        # Only max_model_turns is saved; max_tool_calls comes from cfg;
        # everything else falls through to EXECUTION_BUDGET_DEFAULTS.
        store.set_runtime({"max_model_turns": 7})
        cfg = {"max_tool_calls": 9}
        result = resolve_runtime_settings(store, cfg)

        assert result["max_model_turns"] == 7   # from saved
        assert result["max_tool_calls"] == 9    # from cfg
        assert result["tool_timeout_seconds"] == EXECUTION_BUDGET_DEFAULTS["tool_timeout_seconds"]
        assert result["run_timeout_seconds"] == EXECUTION_BUDGET_DEFAULTS["run_timeout_seconds"]
        assert result["max_argument_bytes"] == EXECUTION_BUDGET_DEFAULTS["max_argument_bytes"]
        assert result["max_result_bytes"] == EXECUTION_BUDGET_DEFAULTS["max_result_bytes"]
    finally:
        reset(path=None)


def test_resolve_runtime_settings_store_failure_falls_back(tmp_path):
    """When the store raises, cfg/defaults still apply."""
    from cauldron_ai_admin.service_factory import EXECUTION_BUDGET_DEFAULTS, resolve_runtime_settings

    class _BrokenStore:
        def get_runtime(self):
            raise OSError("disk error")

    result = resolve_runtime_settings(_BrokenStore(), {})
    assert result["max_model_turns"] == EXECUTION_BUDGET_DEFAULTS["max_model_turns"]
    assert result["max_tool_calls"] == EXECUTION_BUDGET_DEFAULTS["max_tool_calls"]


# ---------------------------------------------------------------------------
# RuntimeSettingsForm defaults reflect the module constants
# ---------------------------------------------------------------------------

def test_runtime_form_initial_max_model_turns_is_8():
    from cauldron_ai_admin.forms import RuntimeSettingsForm
    form = RuntimeSettingsForm()
    assert form.fields["max_model_turns"].initial == 8


def test_runtime_form_initial_max_tool_calls_is_12():
    from cauldron_ai_admin.forms import RuntimeSettingsForm
    form = RuntimeSettingsForm()
    assert form.fields["max_tool_calls"].initial == 12


def test_runtime_form_accepts_module_defaults_as_valid():
    from cauldron_ai_admin.service_factory import EXECUTION_BUDGET_DEFAULTS
    from cauldron_ai_admin.forms import RuntimeSettingsForm
    data = {
        "max_model_turns": str(EXECUTION_BUDGET_DEFAULTS["max_model_turns"]),
        "max_tool_calls": str(EXECUTION_BUDGET_DEFAULTS["max_tool_calls"]),
        "tool_timeout_seconds": str(EXECUTION_BUDGET_DEFAULTS["tool_timeout_seconds"]),
        "run_timeout_seconds": str(EXECUTION_BUDGET_DEFAULTS["run_timeout_seconds"]),
        "max_argument_bytes": str(EXECUTION_BUDGET_DEFAULTS["max_argument_bytes"]),
        "max_result_bytes": str(EXECUTION_BUDGET_DEFAULTS["max_result_bytes"]),
        "include_content_tools": "on",
    }
    form = RuntimeSettingsForm(data=data)
    assert form.is_valid(), form.errors


# ---------------------------------------------------------------------------
# Settings persistence round-trip
# ---------------------------------------------------------------------------

def test_runtime_settings_saved_and_loaded_back(tmp_path):
    store, reset = _make_store(tmp_path)
    try:
        store.set_runtime({
            "max_model_turns": 10,
            "max_tool_calls": 15,
            "tool_timeout_seconds": 45.0,
            "run_timeout_seconds": 200.0,
            "max_argument_bytes": 16384,
            "max_result_bytes": 32768,
            "include_content_tools": False,
        })
        loaded = store.get_runtime()
        assert loaded["max_model_turns"] == 10
        assert loaded["max_tool_calls"] == 15
        assert loaded["tool_timeout_seconds"] == 45.0
        assert loaded["run_timeout_seconds"] == 200.0
        assert loaded["max_argument_bytes"] == 16384
        assert loaded["max_result_bytes"] == 32768
        assert loaded["include_content_tools"] is False
    finally:
        reset(path=None)


def test_runtime_settings_are_used_without_restart(tmp_path):
    """Saving new settings is reflected on the next call to resolve_runtime_settings."""
    from cauldron_ai_admin.service_factory import resolve_runtime_settings
    store, reset = _make_store(tmp_path)
    try:
        r1 = resolve_runtime_settings(store, {})
        assert r1["max_model_turns"] == 8  # module default

        store.set_runtime({"max_model_turns": 5})
        r2 = resolve_runtime_settings(store, {})
        assert r2["max_model_turns"] == 5  # saved setting applied immediately
    finally:
        reset(path=None)


# ---------------------------------------------------------------------------
# Service receives budget from resolve_runtime_settings
# ---------------------------------------------------------------------------

def test_service_built_with_module_defaults_from_resolve_runtime(tmp_path):
    """Service attributes match what resolve_runtime_settings returns."""
    from cauldron_ai_admin.service_factory import EXECUTION_BUDGET_DEFAULTS, resolve_runtime_settings
    from cauldron_ai_admin.service import AdminAIService
    from cauldron_ai.testing import FakeAIModelProvider
    from cauldron_ai_admin.tools import AdminAIToolRegistry
    from helpers import make_assembly_service_for_tools

    store, reset = _make_store(tmp_path)
    try:
        runtime = resolve_runtime_settings(store, {})
        reg = AdminAIToolRegistry()
        asm = make_assembly_service_for_tools()
        svc = AdminAIService(
            provider=FakeAIModelProvider(),
            tool_registry=reg,
            max_model_turns=int(runtime["max_model_turns"]),
            max_tool_calls=int(runtime["max_tool_calls"]),
            max_argument_bytes=int(runtime["max_argument_bytes"]),
            max_result_bytes=int(runtime["max_result_bytes"]),
            prompt_assembly_service=asm,
        )
        assert svc._max_model_turns == EXECUTION_BUDGET_DEFAULTS["max_model_turns"]
        assert svc._max_tool_calls == EXECUTION_BUDGET_DEFAULTS["max_tool_calls"]
    finally:
        reset(path=None)


# ---------------------------------------------------------------------------
# >5 tool calls complete when max_tool_calls=12
# ---------------------------------------------------------------------------

def test_six_tool_calls_complete_with_default_limit():
    """Six sequential tool calls succeed under the module default of 12."""
    from cauldron_ai.contracts import AIModelResponse, AIModelToolCall
    from cauldron_ai.testing import FakeAIModelProvider
    from cauldron_ai_admin.tools import AdminAIToolRegistry

    reg = AdminAIToolRegistry()
    defn, handler = _read_tool()
    reg.register(defn, handler)

    fake = FakeAIModelProvider()
    # Round 1: 6 tool calls in a single response
    fake.queue_response(AIModelResponse(
        provider_request_id="r1",
        tool_calls=tuple(
            AIModelToolCall(id=f"c{i}", name="t.read", arguments={})
            for i in range(1, 7)
        ),
        stop_reason="tool_use",
    ))
    # Round 2: model signals done
    fake.queue_response(AIModelResponse(
        provider_request_id="r2",
        content="All done.",
        stop_reason="end_turn",
    ))

    svc = _make_service(fake, reg, max_tool_calls=12)
    user = _make_user()
    run = svc.run(user, "Run 6 reads.")
    assert run.status == "completed", f"Expected completed, got {run.status!r} ({run.error_code})"
    assert run.tool_call_count == 6


def test_tool_calls_across_multiple_turns_complete_within_limit():
    """Tool calls spread across turns all count toward the budget."""
    from cauldron_ai.contracts import AIModelResponse, AIModelToolCall
    from cauldron_ai.testing import FakeAIModelProvider
    from cauldron_ai_admin.tools import AdminAIToolRegistry

    reg = AdminAIToolRegistry()
    defn, handler = _read_tool()
    reg.register(defn, handler)

    fake = FakeAIModelProvider()
    # 3 calls in turn 1, 3 calls in turn 2 = 6 total < 12 default
    fake.queue_response(AIModelResponse(
        provider_request_id="r1",
        tool_calls=tuple(
            AIModelToolCall(id=f"c{i}", name="t.read", arguments={})
            for i in range(1, 4)
        ),
        stop_reason="tool_use",
    ))
    fake.queue_response(AIModelResponse(
        provider_request_id="r2",
        tool_calls=tuple(
            AIModelToolCall(id=f"c{i}", name="t.read", arguments={})
            for i in range(4, 7)
        ),
        stop_reason="tool_use",
    ))
    fake.queue_response(AIModelResponse(
        provider_request_id="r3",
        content="Done.",
        stop_reason="end_turn",
    ))

    svc = _make_service(fake, reg, max_tool_calls=12, max_model_turns=8)
    user = _make_user()
    run = svc.run(user, "Multi-turn reads.")
    assert run.status == "completed"
    assert run.tool_call_count == 6


# ---------------------------------------------------------------------------
# Enforcement at configured limit
# ---------------------------------------------------------------------------

def test_tool_call_limit_enforced_at_configured_value():
    """Configured max_tool_calls=3 stops a run that requests 4 calls."""
    from cauldron_ai.contracts import AIModelResponse, AIModelToolCall
    from cauldron_ai.testing import FakeAIModelProvider
    from cauldron_ai_admin.tools import AdminAIToolRegistry

    reg = AdminAIToolRegistry()
    defn, handler = _read_tool()
    reg.register(defn, handler)

    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="r1",
        tool_calls=tuple(
            AIModelToolCall(id=f"c{i}", name="t.read", arguments={})
            for i in range(1, 5)
        ),
        stop_reason="tool_use",
    ))

    svc = _make_service(fake, reg, max_tool_calls=3)
    user = _make_user()
    run = svc.run(user, "Try 4 reads with limit 3.")
    assert run.status == "failed"
    assert run.error_code == "run.max_tool_calls_exceeded"


def test_oversized_argument_rejected_at_configured_limit():
    """Arguments exceeding max_argument_bytes are rejected."""
    from cauldron_ai.contracts import AIModelResponse, AIModelToolCall
    from cauldron_ai.testing import FakeAIModelProvider
    from cauldron_ai_admin.tools import AdminAIToolRegistry

    reg = AdminAIToolRegistry()
    defn, handler = _read_tool()
    reg.register(defn, handler)

    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="r1",
        tool_calls=(
            AIModelToolCall(id="c1", name="t.read", arguments={"x": "a" * 5000}),
        ),
        stop_reason="tool_use",
    ))

    svc = _make_service(fake, reg, max_argument_bytes=1024)
    user = _make_user()
    run = svc.run(user, "Huge arguments.")
    assert run.status == "failed"
    assert run.error_code == "tool.arguments_too_large"


# ---------------------------------------------------------------------------
# coerce_execution_budget — unit tests for the validation function
# ---------------------------------------------------------------------------

def test_coerce_execution_budget_returns_coerced_values():
    from cauldron_ai_admin.service_factory import coerce_execution_budget
    result = coerce_execution_budget({"max_model_turns": "5", "max_tool_calls": 8})
    assert result["max_model_turns"] == 5
    assert isinstance(result["max_model_turns"], int)
    assert result["max_tool_calls"] == 8


def test_coerce_execution_budget_ignores_unrecognised_keys():
    from cauldron_ai_admin.service_factory import coerce_execution_budget
    result = coerce_execution_budget({"max_model_turns": 3, "unknown_key": "x"})
    assert "unknown_key" not in result
    assert result["max_model_turns"] == 3


def test_coerce_execution_budget_rejects_wrong_type():
    from cauldron_ai_admin.service_factory import ExecutionBudgetError, coerce_execution_budget
    with pytest.raises(ExecutionBudgetError) as exc_info:
        coerce_execution_budget({"max_model_turns": "not-a-number"})
    assert "max_model_turns" in str(exc_info.value)
    assert "int" in str(exc_info.value)


def test_coerce_execution_budget_rejects_value_below_minimum():
    from cauldron_ai_admin.service_factory import ExecutionBudgetError, coerce_execution_budget
    with pytest.raises(ExecutionBudgetError) as exc_info:
        coerce_execution_budget({"max_model_turns": 0})  # min is 1
    assert "max_model_turns" in str(exc_info.value)
    assert "between" in str(exc_info.value)


def test_coerce_execution_budget_rejects_value_above_maximum():
    from cauldron_ai_admin.service_factory import ExecutionBudgetError, coerce_execution_budget
    with pytest.raises(ExecutionBudgetError) as exc_info:
        coerce_execution_budget({"max_model_turns": 999})  # max is 20
    assert "max_model_turns" in str(exc_info.value)


def test_coerce_execution_budget_rejects_cross_field_timeout_violation():
    from cauldron_ai_admin.service_factory import ExecutionBudgetError, coerce_execution_budget
    with pytest.raises(ExecutionBudgetError) as exc_info:
        coerce_execution_budget({
            "tool_timeout_seconds": 60.0,
            "run_timeout_seconds": 30.0,
        })
    msg = str(exc_info.value)
    assert "tool_timeout_seconds" in msg
    assert "run_timeout_seconds" in msg


def test_coerce_execution_budget_equal_timeouts_also_rejected():
    """tool_timeout == run_timeout must be rejected (must be strictly less)."""
    from cauldron_ai_admin.service_factory import ExecutionBudgetError, coerce_execution_budget
    with pytest.raises(ExecutionBudgetError):
        coerce_execution_budget({
            "tool_timeout_seconds": 30.0,
            "run_timeout_seconds": 30.0,
        })


def test_coerce_execution_budget_skips_cross_field_check_when_only_one_timeout():
    """No cross-field error when only one timeout key is present."""
    from cauldron_ai_admin.service_factory import coerce_execution_budget
    # Only tool_timeout, no run_timeout — no cross-field check should fire.
    result = coerce_execution_budget({"tool_timeout_seconds": 25.0})
    assert result["tool_timeout_seconds"] == 25.0


def test_coerce_execution_budget_rejects_argument_bytes_below_minimum():
    from cauldron_ai_admin.service_factory import ExecutionBudgetError, coerce_execution_budget
    with pytest.raises(ExecutionBudgetError) as exc_info:
        coerce_execution_budget({"max_argument_bytes": 512})  # min is 1024
    assert "max_argument_bytes" in str(exc_info.value)


def test_coerce_execution_budget_error_message_is_actionable():
    """Error messages name the key and state the allowed range."""
    from cauldron_ai_admin.service_factory import ExecutionBudgetError, coerce_execution_budget
    with pytest.raises(ExecutionBudgetError) as exc_info:
        coerce_execution_budget({"run_timeout_seconds": 9999})  # max is 600
    msg = str(exc_info.value)
    assert "run_timeout_seconds" in msg
    assert "600" in msg  # upper bound visible in message


# ---------------------------------------------------------------------------
# Deployment override rejection (ImproperlyConfigured path)
# ---------------------------------------------------------------------------

def test_deployment_override_wrong_type_raises_improperly_configured(tmp_path):
    from django.core.exceptions import ImproperlyConfigured
    from cauldron_ai_admin.service_factory import resolve_runtime_settings
    store, reset = _make_store(tmp_path)
    try:
        with pytest.raises(ImproperlyConfigured) as exc_info:
            resolve_runtime_settings(store, {"max_model_turns": "not-a-number"})
        assert "max_model_turns" in str(exc_info.value)
    finally:
        reset(path=None)


def test_deployment_override_out_of_range_raises_improperly_configured(tmp_path):
    from django.core.exceptions import ImproperlyConfigured
    from cauldron_ai_admin.service_factory import resolve_runtime_settings
    store, reset = _make_store(tmp_path)
    try:
        with pytest.raises(ImproperlyConfigured) as exc_info:
            resolve_runtime_settings(store, {"max_tool_calls": 0})  # below min=1
        assert "max_tool_calls" in str(exc_info.value)
    finally:
        reset(path=None)


def test_deployment_override_cross_field_violation_raises_improperly_configured(tmp_path):
    from django.core.exceptions import ImproperlyConfigured
    from cauldron_ai_admin.service_factory import resolve_runtime_settings
    store, reset = _make_store(tmp_path)
    try:
        with pytest.raises(ImproperlyConfigured) as exc_info:
            resolve_runtime_settings(store, {
                "tool_timeout_seconds": 60.0,
                "run_timeout_seconds": 20.0,
            })
        assert "tool_timeout_seconds" in str(exc_info.value)
    finally:
        reset(path=None)


def test_deployment_override_error_message_is_actionable(tmp_path):
    """ImproperlyConfigured message includes the key name and the offending value."""
    from django.core.exceptions import ImproperlyConfigured
    from cauldron_ai_admin.service_factory import resolve_runtime_settings
    store, reset = _make_store(tmp_path)
    try:
        with pytest.raises(ImproperlyConfigured) as exc_info:
            resolve_runtime_settings(store, {"max_argument_bytes": 100})  # below 1024
        msg = str(exc_info.value)
        assert "max_argument_bytes" in msg
        assert "1024" in msg  # lower bound visible
    finally:
        reset(path=None)


def test_corrupted_saved_settings_fall_back_to_defaults(tmp_path):
    """Saved settings that fail coerce_execution_budget are discarded gracefully."""
    from cauldron_ai_admin.service_factory import EXECUTION_BUDGET_DEFAULTS, resolve_runtime_settings
    store, reset = _make_store(tmp_path)
    try:
        # Write valid settings first, then simulate corruption by bypassing the store.
        import json
        store_path = tmp_path / "ai.json"
        data = json.loads(store_path.read_text()) if store_path.exists() else {}
        # Inject an out-of-range runtime value directly into the JSON file.
        data.setdefault("runtime", {})["max_model_turns"] = 999  # above max=20
        store_path.write_text(json.dumps(data))

        # resolve_runtime_settings should fall back to defaults rather than crashing.
        result = resolve_runtime_settings(store, {})
        assert result["max_model_turns"] == EXECUTION_BUDGET_DEFAULTS["max_model_turns"]
    finally:
        reset(path=None)


# ---------------------------------------------------------------------------
# Form uses coerce_execution_budget for the cross-field check
# ---------------------------------------------------------------------------

def test_form_cross_field_check_uses_shared_validation():
    """RuntimeSettingsForm.clean() rejects equal timeouts via coerce_execution_budget."""
    from cauldron_ai_admin.forms import RuntimeSettingsForm
    form = RuntimeSettingsForm(data={
        "max_model_turns": "8",
        "max_tool_calls": "12",
        "tool_timeout_seconds": "30",   # == run_timeout → must fail
        "run_timeout_seconds": "30",
        "max_argument_bytes": "4096",
        "max_result_bytes": "8192",
    })
    assert not form.is_valid()
    assert "tool_timeout_seconds" in form.errors


# ---------------------------------------------------------------------------
# Absence of execution-budget keys from cauldron_site/settings.py
# ---------------------------------------------------------------------------

def test_settings_py_does_not_contain_execution_budget_keys():
    """The 6 execution-budget keys must not appear in cauldron_site/settings.py."""
    from pathlib import Path
    # Resolve relative to this test file's location in the mono-repo:
    # tests/ → cauldron-ai-admin/ → packages/ → cauldron/ (repo root)
    settings_path = (
        Path(__file__).resolve().parents[3]
        / "cauldron-app" / "cauldron_site" / "settings.py"
    )
    if not settings_path.exists():
        pytest.skip(f"settings.py not found at {settings_path}")

    text = settings_path.read_text()
    budget_keys = (
        "max_model_turns",
        "max_tool_calls",
        "tool_timeout_seconds",
        "run_timeout_seconds",
        "max_argument_bytes",
        "max_result_bytes",
    )
    for key in budget_keys:
        assert key not in text, (
            f"settings.py still contains {key!r} — "
            "execution-budget config must live in cauldron-ai-admin, not core"
        )
