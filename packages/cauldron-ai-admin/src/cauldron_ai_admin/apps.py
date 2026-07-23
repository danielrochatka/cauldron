"""Django AppConfig for cauldron_ai_admin."""
from django.apps import AppConfig


class CauldronAIAdminConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "cauldron_ai_admin"
    verbose_name = "Cauldron Admin AI"

    def ready(self) -> None:
        # Register system checks.
        from . import checks  # noqa: F401
        # Register the six built-in tools with the shared registry.
        # This runs exactly once per process at Django startup.
        from . import builtin_tools
        builtin_tools.register_builtin_tools()
