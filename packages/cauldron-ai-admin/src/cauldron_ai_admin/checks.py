"""Django system checks for cauldron.ai.admin."""
from __future__ import annotations

from django.core import checks


def _is_admin_ai_active() -> bool:
    try:
        from django.conf import settings
    except Exception:
        return False
    modules = getattr(settings, "CAULDRON_MODULES", None)
    return modules is not None and "cauldron.ai.admin" in modules


def _admin_ai_config() -> dict:
    from django.conf import settings
    modules = getattr(settings, "CAULDRON_MODULES", {}) or {}
    cfg = modules.get("cauldron.ai.admin") or {}
    return cfg if isinstance(cfg, dict) else {}


@checks.register(checks.Tags.compatibility)
def check_ai_provider_registered(app_configs, **kwargs):
    """admin_ai.E001 / E002: an AI provider must be registered.

    We don't force sites to pin a provider name at import time — a
    consuming app registers one at ``AppConfig.ready()``. The check
    fires when Admin AI is active but the registry is empty; if the
    site opts into a specific provider via ``preferred_provider`` in
    ``CAULDRON_MODULES['cauldron.ai.admin']`` and the name is missing
    we surface E002.
    """
    if not _is_admin_ai_active():
        return []
    errors: list = []
    try:
        from cauldron_ai.providers import provider_names, get_provider
    except Exception as exc:  # pragma: no cover - defensive
        errors.append(checks.Error(
            f"cauldron.ai package is unavailable: {type(exc).__name__}",
            id="admin_ai.E001",
        ))
        return errors

    names = provider_names()
    if not names:
        errors.append(checks.Error(
            "No AI provider is registered. Install a Cauldron AI provider "
            "package and register it at Django startup.",
            id="admin_ai.E001",
        ))
        return errors

    preferred = _admin_ai_config().get("preferred_provider")
    if preferred:
        try:
            get_provider(preferred)
        except Exception:
            errors.append(checks.Error(
                f"Preferred AI provider {preferred!r} is not registered. "
                f"Registered providers: {names!r}.",
                id="admin_ai.E002",
            ))
    return errors


@checks.register(checks.Tags.compatibility)
def check_limits_are_positive(app_configs, **kwargs):
    """admin_ai.E003: numeric limits must all be positive integers/floats."""
    if not _is_admin_ai_active():
        return []
    cfg = _admin_ai_config()
    errors = []
    positive_int_keys = (
        "max_model_turns",
        "max_tool_calls",
        "max_argument_bytes",
        "max_result_bytes",
    )
    positive_float_keys = (
        "tool_timeout_seconds",
        "run_timeout_seconds",
    )
    for key in positive_int_keys:
        if key not in cfg:
            continue
        value = cfg.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            errors.append(checks.Error(
                f"cauldron.ai.admin config {key!r} must be a positive integer.",
                id="admin_ai.E003",
            ))
    for key in positive_float_keys:
        if key not in cfg:
            continue
        value = cfg.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            errors.append(checks.Error(
                f"cauldron.ai.admin config {key!r} must be a positive number.",
                id="admin_ai.E003",
            ))
    return errors


@checks.register(checks.Tags.compatibility)
def check_no_duplicate_tool_names(app_configs, **kwargs):
    """admin_ai.E004: the tool registry never holds duplicate names.

    The registry's ``register()`` refuses duplicates, so this check is
    a belt-and-braces observation for operators who inspect the graph.
    """
    if not _is_admin_ai_active():
        return []
    try:
        from .tools import get_tool_registry
        duplicates = get_tool_registry().duplicate_names()
    except Exception:
        return []
    if not duplicates:
        return []
    return [checks.Error(
        f"Duplicate tool names in Admin AI registry: {sorted(duplicates)!r}",
        id="admin_ai.E004",
    )]


@checks.register(checks.Tags.compatibility)
def check_required_dependencies(app_configs, **kwargs):
    """admin_ai.E005: dependent modules must be installed and active.

    Admin AI's proposals go through ``cauldron.content.operations``, so
    the Django app must be present. We don't require the Cauldron module
    itself to be enabled in ``CAULDRON_MODULES`` — but if it isn't,
    proposals will fail at runtime and we surface a warning.
    """
    if not _is_admin_ai_active():
        return []
    errors = []
    from django.conf import settings
    installed = list(getattr(settings, "INSTALLED_APPS", []) or [])
    if "cauldron_content_operations" not in installed:
        errors.append(checks.Error(
            "cauldron.ai.admin requires 'cauldron_content_operations' in "
            "INSTALLED_APPS to persist proposals.",
            id="admin_ai.E005",
        ))
    if "cauldron_ai_admin" not in installed:
        errors.append(checks.Error(
            "cauldron.ai.admin requires 'cauldron_ai_admin' in INSTALLED_APPS.",
            id="admin_ai.E005",
        ))
    return errors
