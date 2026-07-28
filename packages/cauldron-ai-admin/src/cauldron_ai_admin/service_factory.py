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
    "tool_timeout_seconds": 30.0,
    "run_timeout_seconds": 120.0,
    "max_argument_bytes": 32768,
    "max_result_bytes": 65536,
    "include_content_tools": True,
}


def resolve_runtime_settings(store, cfg: dict) -> dict[str, Any]:
    """Return the effective runtime settings.

    Precedence per key:
    1. ``AIProviderSettingsStore.get_runtime()``  (values saved via settings UI)
    2. ``CAULDRON_MODULES["cauldron.ai.admin"]`` values (from Django settings)
    3. ``EXECUTION_BUDGET_DEFAULTS`` module defaults.

    ``include_content_tools`` uses ``.get(..., default)`` semantics rather
    than truthiness so an explicit ``False`` in either layer is honoured.
    """
    try:
        saved = dict(store.get_runtime() or {})
    except Exception:
        saved = {}

    def _numeric(name: str) -> Any:
        return saved.get(name) or cfg.get(name) or EXECUTION_BUDGET_DEFAULTS[name]

    return {
        "max_model_turns": _numeric("max_model_turns"),
        "max_tool_calls": _numeric("max_tool_calls"),
        "tool_timeout_seconds": _numeric("tool_timeout_seconds"),
        "run_timeout_seconds": _numeric("run_timeout_seconds"),
        "max_argument_bytes": _numeric("max_argument_bytes"),
        "max_result_bytes": _numeric("max_result_bytes"),
        "include_content_tools": (
            saved["include_content_tools"]
            if "include_content_tools" in saved
            else cfg.get(
                "include_content_tools",
                EXECUTION_BUDGET_DEFAULTS["include_content_tools"],
            )
        ),
    }


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
