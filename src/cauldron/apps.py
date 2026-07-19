"""Django application configuration for Cauldron."""

from django.apps import AppConfig


class CauldronConfig(AppConfig):
    """Minimal Django app config for the Cauldron core package."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "cauldron"
    verbose_name = "Cauldron"

    def ready(self) -> None:
        """Register system checks and activate the module runtime."""
        from django.conf import settings

        from . import checks  # noqa: F401
        from .modules.discovery import discover_modules
        from .modules.registry import registry

        disabled = set(getattr(settings, "CAULDRON_DISABLED_MODULES", []))
        modules = discover_modules()
        registry.populate(modules, disabled=disabled)
        registry.activate()
