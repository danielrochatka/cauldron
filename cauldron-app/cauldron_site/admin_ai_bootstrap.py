"""Register a deterministic fake AI provider for the Cauldron self-hosted instance.

Production sites replace this with a real provider package (Anthropic,
OpenAI, etc.). The default ships with the fake provider so ``manage.py check``
and ``manage.py migrate`` succeed without external credentials.

To use a real AI provider, install the appropriate vendor package and update
CAULDRON_AI_PROVIDER in config.env.
"""
from django.apps import AppConfig


class AdminAIBootstrapConfig(AppConfig):
    name = "cauldron_site.admin_ai_bootstrap"
    label = "cauldron_site_admin_ai_bootstrap"
    verbose_name = "Cauldron Admin AI bootstrap"

    def ready(self) -> None:
        try:
            from cauldron_ai.providers import provider_names, register_provider
            from cauldron_ai.testing import FakeAIModelProvider
        except Exception:
            return
        if "fake" in provider_names():
            return
        register_provider(FakeAIModelProvider(name="fake"))
