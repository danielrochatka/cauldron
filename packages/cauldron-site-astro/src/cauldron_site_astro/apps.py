"""Django AppConfig for cauldron_site_astro."""
from django.apps import AppConfig


class CauldronSiteAstroConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "cauldron_site_astro"
    verbose_name = "Cauldron Site Astro"

    def ready(self) -> None:
        from . import checks  # noqa: F401

        _connect_signals()
        _register_site_tools()
        _register_site_tool_prompts()


def _register_site_tools() -> None:
    try:
        from cauldron_ai_admin.tools import get_tool_registry
        from cauldron_site_astro import site_tools
        site_tools.register(get_tool_registry())
    except ImportError:
        pass  # cauldron-ai-admin not installed


def _register_site_tool_prompts() -> None:
    try:
        import cauldron_ai  # noqa: F401
    except ImportError:
        return  # cauldron-ai optional dependency not installed
    from cauldron_site_astro.site_tool_prompts import register_builtin_site_tool_prompts
    register_builtin_site_tool_prompts()


def _connect_signals() -> None:
    try:
        from cauldron_content_operations.signals import canonical_content_changed
        canonical_content_changed.connect(_handle_content_changed, weak=False)
    except ImportError:
        pass


def _handle_content_changed(sender, change_type, change_id, provider_name, changed_by, **kwargs):
    import logging
    logger = logging.getLogger(__name__)
    try:
        from cauldron_site_astro.dispatcher import get_dispatcher
        get_dispatcher().dispatch()
        logger.info("cauldron.site.astro: build dispatched after %s of %s", change_type, change_id)
    except Exception:
        logger.exception("cauldron.site.astro: error dispatching build after %s of %s", change_type, change_id)
