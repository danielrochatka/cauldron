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
    """Connect the post-apply signal handler."""
    try:
        from cauldron_content_operations.signals import content_change_applied

        content_change_applied.connect(_handle_content_applied, weak=False)
    except ImportError:
        pass  # cauldron-content-operations not installed


def _handle_content_applied(sender, request_id, provider_name, applied_by, **kwargs):
    """Rebuild the public site after a successful content apply."""
    import logging

    logger = logging.getLogger(__name__)
    try:
        from cauldron_site_astro.service import get_build_service

        svc = get_build_service()
        result = svc.build()
        if result.ok:
            logger.info(
                "Site rebuilt after apply of %s: %d page(s) generated.",
                request_id,
                result.pages_built,
            )
        else:
            logger.error(
                "Site build failed after apply of %s: %s",
                request_id,
                result.error,
            )
    except Exception:
        logger.exception(
            "Unexpected error during site build after apply of %s", request_id
        )
