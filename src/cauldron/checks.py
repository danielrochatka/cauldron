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

    active = registry.all_active()
    if active:
        slugs = ", ".join(m.slug for m in active)
        messages.append(
            Info(
                f"{len(active)} Cauldron module(s) active: {slugs}.",
                hint="Disable modules with CAULDRON_DISABLED_MODULES in Django settings.",
                id="cauldron.I002",
            )
        )

    _error_id_map = {
        ErrorKind.MISSING_DEPENDENCY: "cauldron.E010",
        ErrorKind.MISSING_CAPABILITY: "cauldron.E011",
        ErrorKind.VERSION_CONSTRAINT: "cauldron.E012",
        ErrorKind.CAULDRON_VERSION: "cauldron.E013",
        ErrorKind.CIRCULAR_DEPENDENCY: "cauldron.E014",
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
