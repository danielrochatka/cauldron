"""Register a deterministic fake AI provider for the example site.

Production sites replace this with a real provider package (Anthropic,
OpenAI, etc.). The example ships with the fake so ``manage.py check`` and
``manage.py migrate`` succeed without external credentials.
"""
from django.apps import AppConfig


class AdminAIBootstrapConfig(AppConfig):
    name = "config.admin_ai_bootstrap"
    label = "config_admin_ai_bootstrap"
    verbose_name = "Admin AI example bootstrap"

    def ready(self) -> None:
        try:
            from cauldron_ai.providers import provider_names, register_provider
            from cauldron_ai.testing import FakeAIModelProvider
        except Exception:
            return
        if "fake" in provider_names():
            return
        register_provider(FakeAIModelProvider(name="fake"))
