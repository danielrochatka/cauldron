"""Django AppConfig for cauldron_ai_attachments."""
from django.apps import AppConfig


class CauldronAIAttachmentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "cauldron_ai_attachments"
    verbose_name = "Cauldron AI Attachments"

    def ready(self) -> None:
        from . import checks  # noqa: F401

        _register_tools()
        _register_tool_prompts()


def _register_tools() -> None:
    try:
        from cauldron_ai_admin.tools import get_tool_registry
        from cauldron_ai_attachments.tools import register
        register(get_tool_registry())
    except ImportError:
        pass


def _register_tool_prompts() -> None:
    try:
        import cauldron_ai  # noqa: F401
    except ImportError:
        return
    from cauldron_ai_attachments.tool_prompts import register_tool_prompts
    register_tool_prompts()
