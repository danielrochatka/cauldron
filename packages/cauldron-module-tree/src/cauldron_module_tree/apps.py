"""Django AppConfig for cauldron_module_tree."""
from django.apps import AppConfig


class CauldronModuleTreeConfig(AppConfig):
    name = "cauldron_module_tree"
    verbose_name = "Cauldron Module Tree"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from . import navigation as _nav  # noqa: F401 — triggers registration
