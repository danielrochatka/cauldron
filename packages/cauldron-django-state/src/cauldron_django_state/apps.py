"""Django AppConfig for cauldron_django_state."""
from django.apps import AppConfig


class CauldronDjangoStateConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "cauldron_django_state"
    verbose_name = "Cauldron Django State"

    def ready(self) -> None:
        from . import checks  # noqa: F401  — registers @checks.register decorators
