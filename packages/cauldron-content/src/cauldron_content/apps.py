"""Django AppConfig for cauldron_content."""
from django.apps import AppConfig


class CauldronContentConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "cauldron_content"
    verbose_name = "Cauldron Content"

    def ready(self) -> None:
        from . import checks  # noqa: F401  — registers @checks.register decorators
