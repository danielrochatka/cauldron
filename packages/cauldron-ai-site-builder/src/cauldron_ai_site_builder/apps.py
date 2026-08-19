"""Django AppConfig for cauldron_ai_site_builder."""
from django.apps import AppConfig


class CauldronAISiteBuilderConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "cauldron_ai_site_builder"
    verbose_name = "Cauldron AI Site Builder"

    def ready(self) -> None:
        from . import checks  # noqa: F401
