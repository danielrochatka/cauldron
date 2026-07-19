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

        # Resolve enabled slugs and per-module configs from CAULDRON_MODULES.
        # CAULDRON_MODULES = {"slug": {config dict}, ...}
        # If the setting is absent, no modules are activated (opt-in model).
        modules_setting: dict | None = getattr(settings, "CAULDRON_MODULES", None)
        if modules_setting is not None:
            enabled: set[str] = set(modules_setting.keys())
            module_configs: dict = {
                slug: (cfg if isinstance(cfg, dict) else {})
                for slug, cfg in modules_setting.items()
            }
        else:
            enabled = set()
            module_configs = {}

        capability_overrides: dict = getattr(settings, "CAULDRON_CAPABILITY_PROVIDERS", {})

        result = discover_modules()
        registry.populate(
            result.modules,
            enabled=enabled,
            module_configs=module_configs,
            discovery_errors=result.errors,
            capability_overrides=capability_overrides,
        )
        registry.activate()
