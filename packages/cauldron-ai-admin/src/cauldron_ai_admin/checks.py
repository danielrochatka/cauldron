"""Django system checks for cauldron.ai.admin.

The checks are registered here and re-registered from
``CauldronAIAdminConfig.ready()`` — importing this module is enough to
put the checks into the process-wide registry.
"""
from __future__ import annotations

from typing import Any

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


def _resolve_provider(cfg: dict, names: list[str]) -> tuple[Any | None, str | None]:
    """Return (provider, error_id_or_none).

    The behaviour mirrors ``service_factory.get_admin_ai_service``:

    * If ``cfg["provider"]`` is set → look it up by name (E002 on miss).
    * Otherwise fall back to the single registered provider (E003 when
      ambiguous, E001 when empty).
    """
    from cauldron_ai.providers import get_provider, get_default_provider

    configured = cfg.get("provider")
    if configured:
        try:
            return get_provider(configured), None
        except Exception:
            return None, "admin_ai.E002"
    if not names:
        return None, "admin_ai.E001"
    if len(names) > 1:
        return None, "admin_ai.E003"
    try:
        return get_default_provider(), None
    except Exception:  # pragma: no cover - defensive
        return None, "admin_ai.E003"


@checks.register(checks.Tags.compatibility)
def check_ai_provider_registered(app_configs, **kwargs):
    """admin_ai.E001/E002/E003: an AI provider must resolve deterministically."""
    if not _is_admin_ai_active():
        return []
    errors: list = []
    try:
        from cauldron_ai.providers import provider_names
    except Exception as exc:  # pragma: no cover - defensive
        return [checks.Error(
            f"cauldron.ai package is unavailable: {type(exc).__name__}",
            id="admin_ai.E001",
        )]

    cfg = _admin_ai_config()
    names = provider_names()
    _, err_id = _resolve_provider(cfg, names)
    if err_id == "admin_ai.E001":
        errors.append(checks.Error(
            "No AI provider is registered. Install a Cauldron AI provider "
            "package and register it at Django startup.",
            id="admin_ai.E001",
        ))
    elif err_id == "admin_ai.E002":
        configured = cfg.get("provider", "")
        errors.append(checks.Error(
            f"Configured AI provider {configured!r} is not registered. "
            f"Registered providers: {names!r}.",
            id="admin_ai.E002",
        ))
    elif err_id == "admin_ai.E003":
        errors.append(checks.Error(
            "Multiple AI providers are registered without an explicit "
            "cauldron.ai.admin 'provider' selection. Registered: "
            f"{names!r}.",
            id="admin_ai.E003",
        ))
    return errors


@checks.register(checks.Tags.compatibility)
def check_limits_are_positive(app_configs, **kwargs):
    """admin_ai.E004: numeric limits must all be positive integers/floats."""
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
                id="admin_ai.E004",
            ))
    for key in positive_float_keys:
        if key not in cfg:
            continue
        value = cfg.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            errors.append(checks.Error(
                f"cauldron.ai.admin config {key!r} must be a positive number.",
                id="admin_ai.E004",
            ))
    return errors


@checks.register(checks.Tags.compatibility)
def check_required_dependencies(app_configs, **kwargs):
    """admin_ai.E005: dependent Django apps must be installed.

    Admin AI's proposals go through ``cauldron.content.operations``, so
    the Django app must be present.
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


@checks.register(checks.Tags.compatibility)
def check_no_duplicate_tool_names(app_configs, **kwargs):
    """admin_ai.E006: the tool registry never holds duplicate names."""
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
        id="admin_ai.E006",
    )]


@checks.register(checks.Tags.compatibility)
def check_reserved_namespace_violation(app_configs, **kwargs):
    """admin_ai.E007: no non-server module may register a ``server.*`` tool."""
    if not _is_admin_ai_active():
        return []
    try:
        from .tools import get_tool_registry, SERVER_NAMESPACE, SERVER_OWNING_MODULE
        registry = get_tool_registry()
        offenders = []
        for defn in registry.all_definitions():
            if defn.name.startswith(SERVER_NAMESPACE) and (
                defn.owning_module != SERVER_OWNING_MODULE
            ):
                offenders.append((defn.name, defn.owning_module))
    except Exception:
        return []
    if not offenders:
        return []
    return [checks.Error(
        "Reserved namespace 'server.*' has non-server registrations: "
        f"{offenders!r}",
        id="admin_ai.E007",
    )]


@checks.register(checks.Tags.compatibility)
def check_tool_zero_timeouts(app_configs, **kwargs):
    """admin_ai.W001: a registered tool has zero-ish timeout — likely a bug."""
    if not _is_admin_ai_active():
        return []
    try:
        from .tools import get_tool_registry
        registry = get_tool_registry()
        zeroed = [
            defn.name for defn in registry.all_definitions()
            if defn.timeout_seconds <= 0
        ]
    except Exception:
        return []
    if not zeroed:
        return []
    return [checks.Warning(
        f"Admin AI tools with non-positive timeout_seconds: {zeroed!r}",
        id="admin_ai.W001",
    )]


@checks.register(checks.Tags.compatibility)
def check_required_capabilities_present(app_configs, **kwargs):
    """admin_ai.E008: every capability required by the Admin AI module must
    be provided by some active Cauldron module.
    """
    if not _is_admin_ai_active():
        return []
    try:
        from cauldron.modules.registry import registry as module_registry
        from cauldron_ai_admin.module import module as admin_ai_module
    except Exception:
        # Module system or admin-ai module not importable — nothing to
        # verify. Never crash the system check runner.
        return []
    if not module_registry.is_populated:
        # Under some test configurations the registry is empty; in that
        # case there is nothing meaningful to compare against.
        return []
    provided = set()
    try:
        provided = set(module_registry.capabilities().keys())
    except Exception:
        return []
    required = {
        r.slug for r in admin_ai_module.manifest.requires if r.kind == "capability"
    }
    missing = sorted(required - provided)
    if not missing:
        return []
    return [checks.Error(
        "Admin AI required capabilities are not provided by any active "
        f"Cauldron module: {missing!r}",
        id="admin_ai.E008",
    )]


@checks.register(checks.Tags.compatibility)
def check_registered_tool_contracts(app_configs, **kwargs):
    """admin_ai.E009: a registered tool has a contract violation.

    Re-runs the tool-level invariants (name pattern, version pattern,
    permission format, schema validity) so a corrupted or partially
    upgraded registry surface fails ``manage.py check`` rather than at
    request time.
    """
    if not _is_admin_ai_active():
        return []
    try:
        from .tools import (
            _NAME_RE, _VERSION_RE, _OWNING_MODULE_RE, _PERMISSION_RE,
            _check_schema, _to_plain, get_tool_registry,
        )
    except Exception:
        return []
    offenders: list[str] = []
    try:
        for defn in get_tool_registry().all_definitions():
            if not _NAME_RE.match(defn.name or ""):
                offenders.append(f"{defn.name!r} name")
                continue
            if not _VERSION_RE.match(defn.version or ""):
                offenders.append(f"{defn.name!r} version")
                continue
            if not _OWNING_MODULE_RE.match(defn.owning_module or ""):
                offenders.append(f"{defn.name!r} owning_module")
                continue
            if not _PERMISSION_RE.match(defn.required_permission or ""):
                offenders.append(f"{defn.name!r} required_permission")
                continue
            try:
                # jsonschema wants plain dict/list containers, so we
                # project the deep-frozen schema back before validation.
                _check_schema(_to_plain(defn.argument_schema))
            except Exception:
                offenders.append(f"{defn.name!r} argument_schema")
                continue
    except Exception:
        return []
    if not offenders:
        return []
    return [checks.Error(
        f"Admin AI tools with contract violations: {offenders!r}",
        id="admin_ai.E009",
    )]
