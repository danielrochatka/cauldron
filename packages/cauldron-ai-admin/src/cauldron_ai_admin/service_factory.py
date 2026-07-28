"""Build the AdminAIService from Django settings + registered providers."""
from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from cauldron_ai.providers import (
    build_provider,
    factory_names,
    provider_names,
)

from .checks import _FactoryProviderMarker, _resolve_provider
from .service import AdminAIService
from .tools import get_tool_registry


def _admin_ai_config() -> dict:
    modules = getattr(settings, "CAULDRON_MODULES", {}) or {}
    cfg = modules.get("cauldron.ai.admin") or {}
    return cfg if isinstance(cfg, dict) else {}


def _resolve_provider_with_factory(provider_name: str, store=None):
    """Return a live provider for ``provider_name``.

    ``build_provider`` unifies dispatch across static instance and factory
    registrations: if the name is registered as an instance the shared
    instance is returned as-is; if it is registered as a factory the
    factory's ``build(config, secrets)`` is invoked with values pulled from
    the config store.  Callers therefore never need to distinguish between
    the two backends.

    Exceptions raised while constructing the provider are collapsed into a
    fixed, credential-safe ``ImproperlyConfigured`` message —
    ``AIProviderConfigurationError`` is re-raised untouched because its
    message is already scrubbed for user display.
    """
    from cauldron_ai.provider_configuration import AIProviderConfigurationError

    from .provider_config import get_store, resolve_provider_config

    if store is None:
        store = get_store()

    try:
        config = resolve_provider_config(provider_name, store)
        secrets = store.get_secrets(provider_name)
    except Exception:
        # Reading the config store must never expose credentials in errors.
        raise ImproperlyConfigured(
            f"AI provider {provider_name!r} could not be constructed. "
            "Check your AI settings."
        )

    try:
        return build_provider(provider_name, config, secrets)
    except AIProviderConfigurationError:
        # Already carries a fixed, credential-safe message.
        raise
    except Exception:
        # Never embed raw exception strings — SDKs echo request metadata
        # (and occasionally credentials) into their exception messages.
        raise ImproperlyConfigured(
            f"AI provider {provider_name!r} could not be constructed. "
            "Check your AI settings."
        )


EXECUTION_BUDGET_DEFAULTS: dict[str, Any] = {
    "max_model_turns": 8,
    "max_tool_calls": 12,
    "tool_timeout_seconds": 10.0,
    "run_timeout_seconds": 30.0,
    "max_argument_bytes": 4096,
    "max_result_bytes": 8192,
    "include_content_tools": True,
}

# Per-key (type, min, max) — mirrors RuntimeSettingsForm field constraints.
_BUDGET_BOUNDS: dict[str, tuple] = {
    "max_model_turns":      (int,   1,      20),
    "max_tool_calls":       (int,   1,      50),
    "tool_timeout_seconds": (float, 1.0,    300.0),
    "run_timeout_seconds":  (float, 10.0,   600.0),
    "max_argument_bytes":   (int,   1024,   1048576),
    "max_result_bytes":     (int,   1024,   1048576),
}


class ExecutionBudgetError(Exception):
    """An execution-budget value is invalid: wrong type, out of range, or
    violates the tool_timeout < run_timeout cross-field invariant."""


def coerce_execution_budget(values: dict) -> dict[str, Any]:
    """Validate and coerce a (possibly partial) execution-budget mapping.

    Accepts any recognised subset of the six numeric keys plus
    ``include_content_tools``.  Coerces each numeric key to its declared
    Python type, validates per-key min/max bounds, and enforces the
    ``tool_timeout_seconds < run_timeout_seconds`` invariant when both keys
    are present.

    Raises ``ExecutionBudgetError`` for any invalid value with an actionable
    message naming the key and the allowed range.  Unrecognised keys are
    silently ignored.  Returns a new dict of the recognised, coerced values.
    """
    out: dict[str, Any] = {}
    for key, (typ, lo, hi) in _BUDGET_BOUNDS.items():
        if key not in values:
            continue
        raw = values[key]
        try:
            val = typ(raw)
        except (TypeError, ValueError) as exc:
            raise ExecutionBudgetError(
                f"cauldron.ai.admin: {key!r} must be a {typ.__name__} "
                f"(received {type(raw).__name__} {raw!r})"
            ) from exc
        if not (lo <= val <= hi):
            raise ExecutionBudgetError(
                f"cauldron.ai.admin: {key!r} must be between {lo} and {hi} "
                f"(received {val!r})"
            )
        out[key] = val
    if "include_content_tools" in values:
        out["include_content_tools"] = bool(values["include_content_tools"])
    # Cross-field invariant — only enforced when both timeout keys are present.
    tool_t = out.get("tool_timeout_seconds")
    run_t = out.get("run_timeout_seconds")
    if tool_t is not None and run_t is not None and tool_t >= run_t:
        raise ExecutionBudgetError(
            f"cauldron.ai.admin: tool_timeout_seconds ({tool_t}) "
            f"must be less than run_timeout_seconds ({run_t})"
        )
    return out


def resolve_runtime_settings(store, cfg: dict) -> dict[str, Any]:
    """Return the effective runtime settings.

    Precedence per key:
    1. ``AIProviderSettingsStore.get_runtime()``  (values saved via settings UI)
    2. ``CAULDRON_MODULES["cauldron.ai.admin"]`` values (from Django settings)
    3. ``EXECUTION_BUDGET_DEFAULTS`` module defaults.

    Validation is applied at two levels so that individually valid partial
    settings from different sources cannot produce an invalid combined result:

    * The *deployment baseline* (EXECUTION_BUDGET_DEFAULTS merged with
      deployment overrides) is validated after merging.  A cross-source
      violation (e.g. a cfg ``tool_timeout`` that exceeds the default
      ``run_timeout``) raises ``ImproperlyConfigured`` at startup.
    * The *final config* (baseline merged with saved settings) is validated
      again.  If saved settings create an invalid combination (e.g. a saved
      ``tool_timeout`` that exceeds a deployment-override ``run_timeout``),
      the saved settings are silently discarded and the valid baseline is
      returned instead.
    """
    # Step 1 — validate deployment overrides individually; any single-key
    # violation (wrong type, out-of-range) is a deployment error.
    try:
        coerced_cfg = coerce_execution_budget(cfg)
    except ExecutionBudgetError as exc:
        raise ImproperlyConfigured(str(exc)) from exc

    # Step 2 — build the complete deployment baseline and validate it as a
    # whole so cross-source violations (cfg vs defaults) are caught here.
    baseline = {**EXECUTION_BUDGET_DEFAULTS, **coerced_cfg}
    try:
        baseline = coerce_execution_budget(baseline)
    except ExecutionBudgetError as exc:
        raise ImproperlyConfigured(str(exc)) from exc

    # Step 3 — load and individually validate saved settings; discard on any
    # error so a corrupt store or out-of-range manual edit never crashes the
    # service.
    try:
        raw_saved = dict(store.get_runtime() or {})
        coerced_saved = coerce_execution_budget(raw_saved)
    except Exception:
        coerced_saved = {}

    if not coerced_saved:
        return baseline

    # Step 4 — merge saved over baseline and validate the complete result.
    # If two individually valid partial settings produce a cross-source
    # violation (e.g. saved tool_timeout > deployment run_timeout), discard
    # the saved settings and return the safe, already-validated baseline.
    merged = {**baseline, **coerced_saved}
    try:
        return coerce_execution_budget(merged)
    except ExecutionBudgetError:
        return baseline


def get_admin_ai_service() -> AdminAIService:
    """Return the Admin AI service configured for the running site.

    Provider selection order:
    1. The provider name from the config store (``AIProviderSettingsStore``).
    2. The ``provider`` name in ``CAULDRON_MODULES['cauldron.ai.admin']``.
    3. Otherwise the single registered provider/factory (if unambiguous).
    """
    from .provider_config import get_store, resolve_provider_name

    store = get_store()
    cfg = _admin_ai_config()

    # Resolve provider name using config store → CAULDRON_MODULES precedence.
    provider_name = resolve_provider_name(store)

    if provider_name:
        provider = _resolve_provider_with_factory(provider_name, store)
    else:
        # Fall back to the single-provider check (existing E001/E003 path).
        # ``_resolve_provider`` now returns a lightweight marker for
        # factory-only registrations, which is only suitable for
        # metadata surfaces — running services always need a live
        # provider, so we route through the unified builder.
        from cauldron_ai.providers import factory_names as _factory_names
        all_names = sorted(
            set(provider_names()) | set(_factory_names())
        )
        provider, err = _resolve_provider(cfg, all_names)
        if err is not None or provider is None:
            raise ImproperlyConfigured(
                f"Admin AI cannot resolve a provider: {err or 'unknown'}"
            )
        # If we got a factory marker, materialise the real provider now.
        if isinstance(provider, _FactoryProviderMarker):
            provider = _resolve_provider_with_factory(provider.name, store)

    runtime = resolve_runtime_settings(store, cfg)

    # Optionally attach the content-operations service so PROPOSE tools
    # can call it. We fetch it lazily to avoid tying admin-ai to a
    # specific content-provider stack at import time.
    content_service = None
    if bool(runtime["include_content_tools"]):
        try:
            from cauldron_admin_content.service_factory import get_service as _get_cs
            content_service = _get_cs()
        except ImproperlyConfigured:
            raise
        except Exception as exc:
            raise ImproperlyConfigured(
                f"Admin AI could not initialize the content-operations "
                f"service: {type(exc).__name__}"
            ) from exc

    return AdminAIService(
        provider=provider,
        tool_registry=get_tool_registry(),
        content_service=content_service,
        max_model_turns=int(runtime["max_model_turns"]),
        max_tool_calls=int(runtime["max_tool_calls"]),
        tool_timeout_seconds=float(runtime["tool_timeout_seconds"]),
        run_timeout_seconds=float(runtime["run_timeout_seconds"]),
        max_argument_bytes=int(runtime["max_argument_bytes"]),
        max_result_bytes=int(runtime["max_result_bytes"]),
    )
