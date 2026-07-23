"""Django AppConfig for cauldron_content_api."""
from django.apps import AppConfig


class CauldronContentApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "cauldron_content_api"
    verbose_name = "Cauldron Content API"

    def ready(self) -> None:
        from . import checks  # noqa: F401
