"""Django system checks for the Cauldron foundation."""

from django.core.checks import Info, register


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
