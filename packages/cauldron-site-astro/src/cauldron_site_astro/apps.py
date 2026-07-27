"""Django AppConfig for cauldron_site_astro."""
from django.apps import AppConfig


class CauldronSiteAstroConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "cauldron_site_astro"
    verbose_name = "Cauldron Site Astro"

    def ready(self) -> None:
        from . import checks  # noqa: F401

        _connect_signals()


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
