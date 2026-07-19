"""Django AppConfig for cauldron_workspace_flatfile."""
from django.apps import AppConfig


class CauldronWorkspaceFlatfileConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "cauldron_workspace_flatfile"
    verbose_name = "Cauldron Flat-File Workspace"

    def ready(self) -> None:
        from . import checks  # noqa: F401
