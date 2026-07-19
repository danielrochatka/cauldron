"""Django AppConfig for cauldron_django_auth."""
from django.apps import AppConfig


class CauldronDjangoAuthConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "cauldron_django_auth"
    verbose_name = "Cauldron Django Auth"

    def ready(self) -> None:
        from . import checks  # noqa: F401  — registers @checks.register decorators
