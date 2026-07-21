"""Django AppConfig for cauldron_admin_content."""
from django.apps import AppConfig


class CauldronAdminContentConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "cauldron_admin_content"
    verbose_name = "Cauldron Admin Content"

    def ready(self) -> None:
        from . import checks  # noqa: F401
