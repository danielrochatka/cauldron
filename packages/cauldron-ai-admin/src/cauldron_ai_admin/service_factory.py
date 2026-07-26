"""Build the AdminAIService from Django settings + registered providers."""
from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from cauldron_ai.providers import (
    build_provider,
    factory_names,
    get_provider,
    provider_names,
)

from .checks import _FactoryProviderMarker, _resolve_provider
from .service import AdminAIService
from .tools import get_tool_registry


def _admin_ai_config() -> dict:
    modules = getattr(settings, "CAULDRON_MODULES", {}) or {}
    cfg = modules.get("cauldron.ai.admin") or {}
    return cfg if isinstance(cfg, dict) else {}


def _resolve_provider_with_factory(provider_name: str):
    """Try to resolve a provider: first as a registered instance, then as a factory.

    Returns the resolved ``AIModelProvider`` or raises ``ImproperlyConfigured``.
    """
    # 1. Pre-built registered instance.
    if provider_name in provider_names():
        return get_provider(provider_name)

    # 2. Factory — build from stored config and secrets.
    if provider_name in factory_names():
        from .provider_config import get_store, resolve_provider_config
        store = get_store()
        config = resolve_provider_config(provider_name, store)
        secrets = store.get_secrets(provider_name)
        try:
            return build_provider(provider_name, config, secrets)
        except Exception as exc:
            raise ImproperlyConfigured(
                f"Failed to build AI provider {provider_name!r} from factory: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    raise ImproperlyConfigured(
        f"AI provider {provider_name!r} is not registered as an instance or factory. "
        f"Registered instances: {provider_names()!r}. "
        f"Registered factories: {factory_names()!r}."
    )


def _resolve_runtime(store, cfg: dict) -> dict[str, Any]:
    """Return the effective runtime settings.

    Precedence per key:
    1. ``AIProviderSettingsStore.get_runtime()``  (values saved via settings UI)
    2. ``CAULDRON_MODULES["cauldron.ai.admin"]`` values (from Django settings)
    3. Hard-coded defaults matching the pre-Phase-2 constants.

    ``include_content_tools`` uses ``.get(..., default)`` semantics rather
    than truthiness so an explicit ``False`` in either layer is honoured.
    """
    try:
        saved = dict(store.get_runtime() or {})
    except Exception:
        saved = {}

    def _numeric(name: str, default: Any) -> Any:
        return saved.get(name) or cfg.get(name) or default

    return {
        "max_model_turns": _numeric("max_model_turns", 6),
        "max_tool_calls": _numeric("max_tool_calls", 10),
        "tool_timeout_seconds": _numeric("tool_timeout_seconds", 30.0),
        "run_timeout_seconds": _numeric("run_timeout_seconds", 120.0),
        "max_argument_bytes": _numeric("max_argument_bytes", 32768),
        "max_result_bytes": _numeric("max_result_bytes", 65536),
        "include_content_tools": (
            saved["include_content_tools"]
            if "include_content_tools" in saved
            else cfg.get("include_content_tools", True)
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
        provider = _resolve_provider_with_factory(provider_name)
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
            provider = _resolve_provider_with_factory(provider.name)

    runtime = _resolve_runtime(store, cfg)

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
