"""Build the AdminAIService from Django settings + registered providers."""
from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from cauldron_ai.providers import get_default_provider, get_provider

from .service import AdminAIService
from .tools import get_tool_registry


def _admin_ai_config() -> dict:
    modules = getattr(settings, "CAULDRON_MODULES", {}) or {}
    cfg = modules.get("cauldron.ai.admin") or {}
    return cfg if isinstance(cfg, dict) else {}


def get_admin_ai_service() -> AdminAIService:
    """Return the Admin AI service configured for the running site.

    Provider selection order:
    1. The ``preferred_provider`` name in
       ``CAULDRON_MODULES['cauldron.ai.admin']``.
    2. Otherwise the single registered provider (via
       :func:`cauldron_ai.get_default_provider`).
    """
    cfg = _admin_ai_config()
    preferred = cfg.get("preferred_provider")
    try:
        if preferred:
            provider = get_provider(preferred)
        else:
            provider = get_default_provider()
    except Exception as exc:
        raise ImproperlyConfigured(
            f"Admin AI cannot resolve a provider: {exc}"
        ) from exc

    # Optionally attach the content-operations service so PROPOSE tools
    # can call it. We fetch it lazily to avoid tying admin-ai to a
    # specific content-provider stack at import time.
    content_service = None
    try:
        from cauldron_admin_content.service_factory import get_service as _get_cs
        content_service = _get_cs()
    except Exception:
        content_service = None

    return AdminAIService(
        provider=provider,
        tool_registry=get_tool_registry(),
        content_service=content_service,
        max_model_turns=int(cfg.get("max_model_turns", 6)),
        max_tool_calls=int(cfg.get("max_tool_calls", 10)),
        tool_timeout_seconds=float(cfg.get("tool_timeout_seconds", 30.0)),
        run_timeout_seconds=float(cfg.get("run_timeout_seconds", 120.0)),
        max_argument_bytes=int(cfg.get("max_argument_bytes", 32768)),
        max_result_bytes=int(cfg.get("max_result_bytes", 65536)),
    )
