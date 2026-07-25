"""Django AppConfig for cauldron_ai_openai."""
from django.apps import AppConfig


class CauldronAIOpenAIConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "cauldron_ai_openai"
    verbose_name = "Cauldron AI — OpenAI"

    def ready(self) -> None:
        from cauldron_ai.providers import register_provider_factory
        from .provider import OpenAIProviderFactory
        register_provider_factory(OpenAIProviderFactory())
