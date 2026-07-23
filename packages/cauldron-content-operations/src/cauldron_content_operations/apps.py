"""Django AppConfig for cauldron_content_operations."""
from django.apps import AppConfig


class CauldronContentOperationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "cauldron_content_operations"
    verbose_name = "Cauldron Content Operations"
    label = "cauldron_content_operations"

    def ready(self) -> None:
        from . import checks  # noqa: F401
