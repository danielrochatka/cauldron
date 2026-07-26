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
    """Return (provider_or_marker, error_id_or_none).

    The behaviour mirrors ``service_factory.get_admin_ai_service``:

    * If ``cfg["provider"]`` is set → look it up by name across both
      static instances and factories (E002 on miss).
    * Otherwise fall back to the single registered provider (E003 when
      ambiguous, E001 when empty).

    When the configured selection resolves to a factory-only registration,
    the returned "provider" is a synthetic placeholder exposing ``name``,
    ``display_name``, and ``version`` — enough for the settings page /
    system-check surfaces without triggering a factory ``build()``.
    """
    from cauldron_ai.providers import (
        descriptor_for,
        factory_names,
        get_default_provider,
        get_provider,
    )

    configured = cfg.get("provider")
    if configured:
        try:
            return get_provider(configured), None
        except Exception:
            if configured in set(factory_names()):
                # Factory-only registration: expose a light-weight placeholder
                # so callers don't need to distinguish the two backends when
                # they only want display metadata.
                try:
                    desc = descriptor_for(configured)
                except Exception:
                    return None, "admin_ai.E002"
                return _FactoryProviderMarker(
                    name=configured,
                    display_name=desc.display_name or configured,
                    version=desc.version or "",
                ), None
            return None, "admin_ai.E002"
    if not names:
        return None, "admin_ai.E001"
    if len(names) > 1:
        return None, "admin_ai.E003"
    try:
        return get_default_provider(), None
    except Exception:
        # A single factory-only registration lands here — surface it via
        # the placeholder so the settings page can render its display name.
        only_name = names[0]
        if only_name in set(factory_names()):
            try:
                desc = descriptor_for(only_name)
            except Exception:
                return None, "admin_ai.E003"
            return _FactoryProviderMarker(
                name=only_name,
                display_name=desc.display_name or only_name,
                version=desc.version or "",
            ), None
        return None, "admin_ai.E003"


class _FactoryProviderMarker:
    """Lightweight stand-in used by ``_resolve_provider`` for factory-only providers.

    It exposes just enough surface (``name``, ``display_name``, ``version``)
    for the settings-page / check-runner to render provider metadata
    without materialising the underlying provider (which would require
    valid credentials).
    """

    __slots__ = ("name", "display_name", "version")

    def __init__(self, *, name: str, display_name: str, version: str) -> None:
        self.name = name
        self.display_name = display_name
        self.version = version


@checks.register(checks.Tags.compatibility)
def check_cauldron_django_admin_installed(app_configs, **kwargs):
    """cauldron.ai_admin.E200: cauldron_django_admin must be in INSTALLED_APPS."""
    if not _is_admin_ai_active():
        return []
    from django.conf import settings
    installed = list(getattr(settings, "INSTALLED_APPS", []) or [])
    if "cauldron_django_admin" not in installed:
        return [checks.Error(
            "cauldron.ai.admin requires 'cauldron_django_admin' in INSTALLED_APPS.",
            hint="Add 'cauldron_django_admin' to INSTALLED_APPS.",
            id="cauldron.ai_admin.E200",
        )]
    return []


@checks.register(checks.Tags.compatibility)
def check_admin_shell_capability_provided(app_configs, **kwargs):
    """cauldron.ai_admin.E201: admin.shell capability must be provided."""
    if not _is_admin_ai_active():
        return []
    try:
        from cauldron.modules.registry import registry as module_registry
    except Exception:
        return []
    if not getattr(module_registry, "is_populated", False):
        return []
    try:
        capabilities = module_registry.capabilities()
        if "admin.shell" not in capabilities:
            return [checks.Error(
                "cauldron.ai.admin requires the 'admin.shell' capability to be provided by an active module.",
                hint="Ensure cauldron_django_admin (or equivalent) is in INSTALLED_APPS and CAULDRON_MODULES.",
                id="cauldron.ai_admin.E201",
            )]
    except Exception:
        return []
    return []


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
def check_ai_config_file_permissions(app_configs, **kwargs):
    """admin_ai.W002: config file exists but is not mode 0600."""
    if not _is_admin_ai_active():
        return []
    try:
        from .provider_config import get_store
        store = get_store()
        if store.file_exists() and not store.file_permissions_ok():
            return [checks.Warning(
                f"AI config file {store.path} exists but is not mode 0600. "
                "Credentials may be readable by other users.",
                hint=f"Run: chmod 0600 {store.path}",
                id="admin_ai.W002",
            )]
    except Exception:
        pass
    return []


@checks.register(checks.Tags.compatibility)
def check_selected_provider_has_factory_or_instance(app_configs, **kwargs):
    """admin_ai.E010: the provider selected in the config file must be registered.

    Only validates an explicit selection persisted in the config store;
    CAULDRON_MODULES selections are covered by E001/E002/E003.
    """
    if not _is_admin_ai_active():
        return []
    try:
        from .provider_config import get_store
        from cauldron_ai.providers import factory_names, provider_names

        store = get_store()
        name = store.get_selected_provider()
        if not name:
            return []  # No config-file selection; existing checks handle CAULDRON_MODULES.
        all_known = set(provider_names()) | set(factory_names())
        if name not in all_known:
            return [checks.Error(
                f"AI provider {name!r} is selected in the config store but is "
                "not registered as a provider instance or factory. "
                f"Available: {sorted(all_known)!r}.",
                hint=(
                    "Install the provider package, add it to INSTALLED_APPS, "
                    "or update the AI settings page."
                ),
                id="admin_ai.E010",
            )]
    except Exception:
        pass
    return []


@checks.register(checks.Tags.compatibility)
def check_provider_factory_contracts(app_configs, **kwargs):
    """admin_ai.E011: every registered provider factory implements the required contract."""
    if not _is_admin_ai_active():
        return []
    try:
        from cauldron_ai.providers import factory_names, get_provider_factory
    except Exception:
        return []
    offenders: list[str] = []
    try:
        for name in factory_names():
            try:
                factory = get_provider_factory(name)
            except Exception:
                offenders.append(name)
                continue
            if not callable(getattr(factory, "build", None)):
                offenders.append(f"{name} (missing build)")
                continue
            if not callable(getattr(factory, "test_connection", None)):
                offenders.append(f"{name} (missing test_connection)")
                continue
    except Exception:
        return []
    if not offenders:
        return []
    return [checks.Error(
        f"AI provider factories with contract violations: {offenders!r}",
        id="admin_ai.E011",
    )]


@checks.register(checks.Tags.compatibility)
def check_configuration_spec_validity(app_configs, **kwargs):
    """admin_ai.E012: every registered factory exposes a valid configuration spec."""
    if not _is_admin_ai_active():
        return []
    try:
        from cauldron_ai.provider_configuration import (
            AIProviderConfigurationSpec,
        )
        from cauldron_ai.providers import factory_names, get_configuration_spec
    except Exception:
        return []
    offenders: list[str] = []
    try:
        for name in factory_names():
            try:
                spec = get_configuration_spec(name)
            except Exception:
                offenders.append(f"{name} (spec raise)")
                continue
            if not isinstance(spec, AIProviderConfigurationSpec):
                offenders.append(f"{name} (wrong spec type)")
                continue
            if spec.provider_name != name:
                offenders.append(
                    f"{name} (spec provider_name={spec.provider_name!r})"
                )
    except Exception:
        return []
    if not offenders:
        return []
    return [checks.Error(
        f"AI provider factories with invalid configuration specs: {offenders!r}",
        id="admin_ai.E012",
    )]


@checks.register(checks.Tags.compatibility)
def check_selected_provider_has_required_config(app_configs, **kwargs):
    """admin_ai.W003: selected provider has required non-credential config missing."""
    if not _is_admin_ai_active():
        return []
    try:
        from cauldron_ai.provider_configuration import (
            FIELD_TYPE_PASSWORD,
        )
        from cauldron_ai.providers import factory_names, get_configuration_spec
        from .provider_config import (
            AIProviderStoreError,
            get_store,
            resolve_provider_config,
            resolve_provider_name,
        )
    except Exception:
        return []
    warnings_out: list = []
    try:
        store = get_store()
        provider_name = resolve_provider_name(store)
        if not provider_name or provider_name not in set(factory_names()):
            return []
        spec = get_configuration_spec(provider_name)
        try:
            config = resolve_provider_config(provider_name, store)
        except AIProviderStoreError:
            return []
        missing: list[str] = []
        import os as _os
        for f in spec.fields:
            if not f.required:
                continue
            if f.field_type == FIELD_TYPE_PASSWORD:
                continue  # covered by W004
            has_value = bool(config.get(f.name))
            if not has_value and f.environment_variable:
                has_value = bool(
                    _os.environ.get(f.environment_variable, "").strip()
                )
            if not has_value:
                missing.append(f.name)
        if missing:
            warnings_out.append(checks.Warning(
                f"AI provider {provider_name!r} is missing required config: "
                f"{missing!r}. Visit the AI settings page to complete setup.",
                id="admin_ai.W003",
            ))
    except Exception:
        return []
    return warnings_out


@checks.register(checks.Tags.compatibility)
def check_selected_provider_has_credentials(app_configs, **kwargs):
    """admin_ai.W004: selected provider is missing a required credential."""
    if not _is_admin_ai_active():
        return []
    try:
        from cauldron_ai.provider_configuration import FIELD_TYPE_PASSWORD
        from cauldron_ai.providers import factory_names, get_configuration_spec
        from .provider_config import (
            AIProviderStoreError,
            get_store,
            resolve_provider_name,
        )
    except Exception:
        return []
    warnings_out: list = []
    try:
        store = get_store()
        provider_name = resolve_provider_name(store)
        if not provider_name or provider_name not in set(factory_names()):
            return []
        spec = get_configuration_spec(provider_name)
        try:
            stored_secrets = store.get_secrets(provider_name)
        except AIProviderStoreError:
            return []
        import os as _os
        missing: list[str] = []
        for f in spec.fields:
            if f.field_type != FIELD_TYPE_PASSWORD or not f.required:
                continue
            has_value = bool(stored_secrets.get(f.name))
            if not has_value and f.environment_variable:
                has_value = bool(
                    _os.environ.get(f.environment_variable, "").strip()
                )
            if not has_value:
                # Never emit the secret NAME with any surrounding value —
                # message stays generic on purpose.
                missing.append(f.name)
        if missing:
            warnings_out.append(checks.Warning(
                f"AI provider {provider_name!r} is missing required "
                f"credentials: {missing!r}. Configure them in AI settings "
                "or via the provider's environment variables.",
                id="admin_ai.W004",
            ))
    except Exception:
        return []
    return warnings_out


@checks.register(checks.Tags.compatibility)
def check_config_file_readable(app_configs, **kwargs):
    """admin_ai.E013/E014/E015/E016/W005/W006: config file health checks.

    Reads the config file (bounded to 64 KB) and surfaces any structural
    problem as a stable check id.  All messages are credential-safe —
    contents of the file are never included.
    """
    if not _is_admin_ai_active():
        return []
    try:
        from .provider_config import (
            AIProviderStoreCorruptError,
            AIProviderStoreUnsafePathError,
            AIProviderStoreVersionError,
            get_store,
        )
    except Exception:
        return []
    out: list = []
    try:
        store = get_store()
    except Exception:
        return []
    if not store.file_exists():
        return []
    path = store.path
    # E015: symlink refusal
    try:
        if path.is_symlink():
            return [checks.Error(
                "AI config path is a symlink — refusing to load. "
                "Replace with a regular file.",
                id="admin_ai.E015",
            )]
    except OSError:
        pass
    # E016: non-regular file
    try:
        if path.exists() and not path.is_file():
            return [checks.Error(
                "AI config path is not a regular file — refusing to load.",
                id="admin_ai.E016",
            )]
    except OSError:
        pass
    # W005: oversized file
    try:
        if path.stat().st_size > 64 * 1024:
            out.append(checks.Warning(
                "AI config file exceeds 64 KB. Cauldron refuses to load "
                "oversized files at request time.",
                id="admin_ai.W005",
            ))
    except OSError:
        pass
    # W006: parent directory not 0700
    try:
        if not store.parent_permissions_ok():
            out.append(checks.Warning(
                "AI config parent directory is not mode 0700; other "
                "users may enumerate credential file names.",
                hint=f"Run: chmod 0700 {path.parent}",
                id="admin_ai.W006",
            ))
    except Exception:
        pass
    # E013 / E014: attempt a controlled load
    try:
        store.load()
    except AIProviderStoreVersionError as exc:
        return out + [checks.Error(
            f"AI config file version is not supported: {exc}",
            id="admin_ai.E014",
        )]
    except AIProviderStoreCorruptError as exc:
        return out + [checks.Error(
            f"AI config file is corrupt: {exc}",
            id="admin_ai.E013",
        )]
    except AIProviderStoreUnsafePathError as exc:
        # Symlink / non-regular file — already handled above, but if the
        # granular checks missed (e.g. race), surface here too.
        return out + [checks.Error(
            f"AI config path is unsafe: {exc}",
            id="admin_ai.E016",
        )]
    except Exception:
        # An unexpected error at load time is not fatal for `check`; the
        # runtime paths will surface a clearer message when the file is
        # actually needed.
        pass
    return out


@checks.register(checks.Tags.compatibility)
def check_registered_tools_have_prompt_templates(app_configs, **kwargs):
    """admin_ai.E017: every registered tool must have a prompt template."""
    if not _is_admin_ai_active():
        return []
    try:
        from .tools import get_tool_registry
        from cauldron_ai.prompt_templates import get_prompt_template_registry
        tool_names = {d.name for d in get_tool_registry().all_definitions()}
        registry = get_prompt_template_registry()
        missing = sorted(name for name in tool_names
                         if registry.get_tool_template(name) is None)
    except Exception:
        return []
    if not missing:
        return []
    return [checks.Error(
        f"Admin AI tools missing prompt templates: {missing!r}",
        hint="Register an AIToolPromptTemplate for each listed tool.",
        id="admin_ai.E017",
    )]


@checks.register(checks.Tags.compatibility)
def check_no_orphan_prompt_templates(app_configs, **kwargs):
    """admin_ai.E018: every prompt template must match a registered tool."""
    if not _is_admin_ai_active():
        return []
    try:
        from .tools import get_tool_registry
        from cauldron_ai.prompt_templates import get_prompt_template_registry
        tool_names = {d.name for d in get_tool_registry().all_definitions()}
        registry = get_prompt_template_registry()
        orphans = sorted(
            t.tool_name for t in registry.all_tool_templates()
            if t.tool_name not in tool_names
        )
    except Exception:
        return []
    if not orphans:
        return []
    return [checks.Error(
        f"Prompt templates registered for unknown tools: {orphans!r}",
        id="admin_ai.E018",
    )]


@checks.register(checks.Tags.compatibility)
def check_prompt_template_versions_valid(app_configs, **kwargs):
    """admin_ai.E019: every template version must be non-empty and valid."""
    if not _is_admin_ai_active():
        return []
    try:
        import re
        from cauldron_ai.prompt_templates import get_prompt_template_registry
        VERSION_RE = re.compile(r"^(?:v\d+|\d+\.\d+(?:\.\d+)?(?:[-.][a-z0-9]+)*)$")
        registry = get_prompt_template_registry()
        invalid = sorted(
            t.tool_name for t in registry.all_tool_templates()
            if not t.template_version or not VERSION_RE.match(t.template_version)
        )
    except Exception:
        return []
    if not invalid:
        return []
    return [checks.Error(
        f"Prompt templates with invalid version: {invalid!r}",
        id="admin_ai.E019",
    )]


@checks.register(checks.Tags.compatibility)
def check_prompt_template_permission_alignment(app_configs, **kwargs):
    """admin_ai.E021: template required_permission must match tool definition."""
    if not _is_admin_ai_active():
        return []
    try:
        from .tools import get_tool_registry
        from cauldron_ai.prompt_templates import get_prompt_template_registry
        registry = get_prompt_template_registry()
        tool_registry = get_tool_registry()
        mismatches = []
        for defn in tool_registry.all_definitions():
            tmpl = registry.get_tool_template(defn.name)
            if tmpl is None:
                continue
            if (tmpl.required_permission is not None
                    and tmpl.required_permission != defn.required_permission):
                mismatches.append(
                    f"{defn.name!r}: template has {tmpl.required_permission!r}, "
                    f"tool has {defn.required_permission!r}"
                )
    except Exception:
        return []
    if not mismatches:
        return []
    return [checks.Error(
        f"Prompt template permission mismatches: {mismatches!r}",
        id="admin_ai.E021",
    )]


@checks.register(checks.Tags.compatibility)
def check_global_operating_prompt_present(app_configs, **kwargs):
    """admin_ai.W007: global operating prompt should be registered."""
    if not _is_admin_ai_active():
        return []
    try:
        from cauldron_ai.prompt_templates import get_prompt_template_registry
        registry = get_prompt_template_registry()
        if registry.get_global_prompt() is not None:
            return []
    except Exception:
        return []
    return [checks.Warning(
        "No global operating prompt is registered. "
        "AdminAIService will fall back to the default system prompt.",
        hint="Call register_global_prompt() at app-ready time.",
        id="admin_ai.W007",
    )]


@checks.register(checks.Tags.compatibility)
def check_global_prompt_version_valid(app_configs, **kwargs):
    """admin_ai.W008: global prompt version must be non-empty and valid."""
    if not _is_admin_ai_active():
        return []
    try:
        import re
        from cauldron_ai.prompt_templates import get_prompt_template_registry
        VERSION_RE = re.compile(r"^(?:v\d+|\d+\.\d+(?:\.\d+)?(?:[-.][a-z0-9]+)*)$")
        registry = get_prompt_template_registry()
        gp = registry.get_global_prompt()
        if gp is None:
            return []
        if gp.version and VERSION_RE.match(gp.version):
            return []
    except Exception:
        return []
    return [checks.Warning(
        "Global operating prompt has an invalid or empty version.",
        id="admin_ai.W008",
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
