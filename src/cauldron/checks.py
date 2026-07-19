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
def cauldron_module_graph_check(app_configs, **kwargs):
    """Validate the module dependency graph and report active modules."""
    from .modules.registry import registry

    messages = []

    # Discovery errors -------------------------------------------------------
    _discovery_id_map = {
        "load_failure": "cauldron.E020",
        "duplicate_slug": "cauldron.E021",
        "manifest_validation": "cauldron.E022",
    }
    for err in registry.discovery_errors():
        messages.append(
            Error(
                err.message,
                hint="Fix the entry-point registration or module package before starting.",
                obj=err.entry_point_name,
                id=_discovery_id_map.get(err.kind, "cauldron.E029"),
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

    return messages
