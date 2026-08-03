"""Django system checks for the Cauldron foundation and module runtime."""

from django.core.checks import Error, Info, Warning, register

from .modules.resolver import ErrorKind


@register()
def cauldron_foundation_check(app_configs, **kwargs):
    """Report that the Cauldron core package initialized successfully."""
    return [
        Info(
            "Cauldron core initialized successfully.",
            hint="Cauldron is installed as a dependency and ready for site extensions.",
            id="cauldron.I001",
        )
    ]


@register()
def cauldron_settings_check(app_configs, **kwargs):
    """Validate CAULDRON_MODULES and CAULDRON_CAPABILITY_PROVIDERS settings."""
    from django.conf import settings

    from .modules import _validate_slug

    messages = []

    modules_setting = getattr(settings, "CAULDRON_MODULES", None)
    if modules_setting is not None:
        if not isinstance(modules_setting, dict):
            messages.append(Error(
                "CAULDRON_MODULES must be a dict mapping module slugs to config dicts.",
                hint="Example: CAULDRON_MODULES = {'cauldron.content': {}}",
                id="cauldron.E001",
            ))
        else:
            for slug, config in modules_setting.items():
                if not isinstance(slug, str):
                    messages.append(Error(
                        f"CAULDRON_MODULES key {slug!r} must be a string.",
                        id="cauldron.E001",
                    ))
                else:
                    try:
                        _validate_slug(slug, "CAULDRON_MODULES key")
                    except ValueError as exc:
                        messages.append(Error(str(exc), id="cauldron.E001"))
                if not isinstance(config, dict):
                    messages.append(Error(
                        f"CAULDRON_MODULES[{slug!r}] must be a dict, got"
                        f" {type(config).__name__!r}.",
                        hint="Use an empty dict {{}} for a module with no config.",
                        id="cauldron.E001",
                    ))

    providers_setting = getattr(settings, "CAULDRON_CAPABILITY_PROVIDERS", None)
    if providers_setting is not None:
        if not isinstance(providers_setting, dict):
            messages.append(Error(
                "CAULDRON_CAPABILITY_PROVIDERS must be a dict mapping capability slugs"
                " to module slugs.",
                hint=(
                    "Example: CAULDRON_CAPABILITY_PROVIDERS ="
                    " {'cauldron.capability.auth': 'cauldron.oauth'}"
                ),
                id="cauldron.E002",
            ))
        else:
            for cap, provider in providers_setting.items():
                if not isinstance(cap, str):
                    messages.append(Error(
                        f"CAULDRON_CAPABILITY_PROVIDERS key {cap!r} must be a string.",
                        id="cauldron.E002",
                    ))
                else:
                    try:
                        _validate_slug(cap, "CAULDRON_CAPABILITY_PROVIDERS key")
                    except ValueError as exc:
                        messages.append(Error(str(exc), id="cauldron.E002"))
                if not isinstance(provider, str):
                    messages.append(Error(
                        f"CAULDRON_CAPABILITY_PROVIDERS[{cap!r}] must be a string module"
                        f" slug, got {type(provider).__name__!r}.",
                        id="cauldron.E002",
                    ))
                else:
                    try:
                        _validate_slug(provider, f"CAULDRON_CAPABILITY_PROVIDERS[{cap!r}]")
                    except ValueError as exc:
                        messages.append(Error(str(exc), id="cauldron.E002"))

    return messages


@register()
def cauldron_module_graph_check(app_configs, **kwargs):
    """Validate the module dependency graph and report active modules.

    Discovery-error severity is scoped to the enabled module set:

    * Errors whose ``candidate_slug`` is in the enabled set retain their
      blocking ``cauldron.E0xx`` ID.
    * Errors whose ``candidate_slug`` is set but NOT in the enabled set are
      downgraded to ``cauldron.W0xx`` warnings, because a broken third-party
      package that is not enabled must not block a healthy installation.
    * Errors without a ``candidate_slug`` (where the module identity is
      unknown) are always treated as blocking Errors.
    """
    from .modules.registry import registry

    messages = []
    enabled = registry.enabled_slugs()

    _discovery_id_map = {
        "load_failure": ("cauldron.E020", "cauldron.W020"),
        "duplicate_slug": ("cauldron.E021", "cauldron.W021"),
        "manifest_validation": ("cauldron.E022", "cauldron.W022"),
    }

    for err in registry.discovery_errors():
        error_id, warning_id = _discovery_id_map.get(
            err.kind, ("cauldron.E029", "cauldron.W029")
        )

        candidate = err.candidate_slug
        # Relevant when: slug unknown, no enabled set, or slug is enabled.
        is_relevant = (
            candidate is None
            or not enabled
            or candidate in enabled
        )

        if is_relevant:
            messages.append(
                Error(
                    err.message,
                    hint=(
                        "Fix the entry-point registration or module package before"
                        " starting."
                    ),
                    obj=err.entry_point_name,
                    id=error_id,
                )
            )
        else:
            messages.append(
                Warning(
                    err.message,
                    hint=(
                        f"Module {candidate!r} is not enabled in CAULDRON_MODULES;"
                        " this discovery problem does not affect the running"
                        " application.  Fix or remove the broken package to suppress"
                        " this warning."
                    ),
                    obj=err.entry_point_name,
                    id=warning_id,
                )
            )

    # Unavailable configured slugs ---------------------------------------
    for unavail in registry.unavailable_modules():
        if unavail.reason == "not_discovered":
            messages.append(
                Error(
                    f"Module {unavail.slug!r} is listed in CAULDRON_MODULES but was"
                    " not found among installed entry points.",
                    hint=(
                        f"Install the package that provides the 'cauldron.modules'"
                        f" entry point for {unavail.slug!r}, or remove it from"
                        " CAULDRON_MODULES."
                    ),
                    obj=unavail.slug,
                    id="cauldron.E023",
                )
            )
        else:
            messages.append(
                Error(
                    f"Module {unavail.slug!r} is listed in CAULDRON_MODULES but could"
                    f" not be activated ({unavail.reason.replace('_', ' ')}).",
                    hint=(
                        "See the accompanying cauldron.E020 or cauldron.E022 message"
                        " for details."
                    ),
                    obj=unavail.slug,
                    id="cauldron.E023",
                )
            )

    # Active-module summary --------------------------------------------------
    active = registry.all_active()
    if active:
        slugs = ", ".join(m.slug for m in active)
        messages.append(
            Info(
                f"{len(active)} Cauldron module(s) active: {slugs}.",
                hint=(
                    "Add a slug to CAULDRON_MODULES to enable a module;"
                    " remove it to disable."
                ),
                id="cauldron.I002",
            )
        )

    # Resolution errors ------------------------------------------------------
    _error_id_map = {
        ErrorKind.MISSING_DEPENDENCY: "cauldron.E010",
        ErrorKind.MISSING_CAPABILITY: "cauldron.E011",
        ErrorKind.VERSION_CONSTRAINT: "cauldron.E012",
        ErrorKind.CAULDRON_VERSION: "cauldron.E013",
        ErrorKind.CIRCULAR_DEPENDENCY: "cauldron.E014",
        ErrorKind.CAPABILITY_CONFLICT: "cauldron.E015",
    }
    for err in registry.errors():
        messages.append(
            Error(
                err.message,
                hint="Resolve the module dependency issue before starting the application.",
                obj=err.module_slug,
                id=_error_id_map.get(err.kind, "cauldron.E019"),
            )
        )

    # Warnings ---------------------------------------------------------------
    for warn in registry.warnings():
        messages.append(
            Warning(
                warn.message,
                hint="Update the optional dependency or relax the version constraint.",
                obj=warn.module_slug,
                id="cauldron.W010",
            )
        )

    # Lifecycle errors -------------------------------------------------------
    for err in registry.lifecycle_errors():
        messages.append(
            Error(
                err.message,
                hint=(
                    f"Fix the exception in the module's {err.phase}() method."
                    " See server logs for the full traceback."
                ),
                obj=err.module_slug,
                id="cauldron.E030",
            )
        )

    return messages
