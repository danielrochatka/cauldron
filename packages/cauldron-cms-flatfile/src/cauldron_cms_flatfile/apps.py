"""Django AppConfig for cauldron_cms_flatfile."""
from django.apps import AppConfig


class CauldronCmsFlatfileConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "cauldron_cms_flatfile"
    verbose_name = "Cauldron Flat-File CMS"

    def ready(self) -> None:
        from . import checks  # noqa: F401
