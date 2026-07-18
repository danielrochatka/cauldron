"""Django application configuration for Cauldron."""

from django.apps import AppConfig


class CauldronConfig(AppConfig):
    """Minimal Django app config for the Cauldron core package."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "cauldron"
    verbose_name = "Cauldron"

    def ready(self) -> None:
        """Register Cauldron system checks when Django initializes."""

        from . import checks  # noqa: F401
