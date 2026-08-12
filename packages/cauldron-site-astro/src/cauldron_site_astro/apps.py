"""Django AppConfig for cauldron_site_astro."""
import threading

from django.apps import AppConfig


# Thread-local flag used by SiteChangeSetService.publish() to suppress the
# canonical_content_changed signal-driven rebuild during a controlled publish
# (that publish already performs its own scoped build and promotion — an
# additional signal-triggered build would race with it and rebuild the full
# site including drafts that were not part of this change set).
_suppress_rebuild = threading.local()


class CauldronSiteAstroConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "cauldron_site_astro"
    verbose_name = "Cauldron Site Astro"

    def ready(self) -> None:
        from . import checks  # noqa: F401

        _connect_signals()
        _register_site_tool_prompts()
        _register_public_url_provider()
        _register_site_tools()
        _connect_style_publication_handler()


def _register_site_tools() -> None:
    try:
        from cauldron_ai_admin.tools import get_tool_registry
        from cauldron_site_astro.site_tools import register
        register(get_tool_registry())
    except ImportError:
        pass  # cauldron-ai-admin not installed


def _register_public_url_provider() -> None:
    from cauldron_content.site import register_public_url_provider
    from cauldron_site_astro.public_url import AstroPublicUrlProvider
    register_public_url_provider(AstroPublicUrlProvider(), owning_module="cauldron.site.astro")


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


def _connect_style_publication_handler() -> None:
    """Connect the style-request post-publication handler (optional ai-admin integration)."""
    try:
        from cauldron_site_astro.signals import site_changeset_published
        site_changeset_published.connect(_handle_changeset_published, weak=False)
    except Exception:
        pass


def _handle_changeset_published(
    sender, changeset_id, staged_theme_css, style_request_id,
    style_committed_hash="", **kwargs
):
    """Mark a pages-scope style request applied after its changeset publishes.

    When style_request_id is empty, returns immediately — no dependency on
    cauldron-ai-admin is required for non-style changesets.

    When style_request_id is present, the UIStyleChangeRequest lifecycle
    transition is required.  All failures propagate — including ImportError
    (style lifecycle owner unavailable) and any error from mark_style_applied()
    — so send_robust() in publish() captures them and rolls back the full
    coordinated publication.
    """
    if not style_request_id:
        return

    try:
        from cauldron_ai_admin.style_service import get_style_service
    except ImportError as exc:
        raise RuntimeError(
            f"cauldron-ai-admin unavailable; cannot finalize style request {style_request_id!r}"
        ) from exc

    get_style_service().mark_style_applied(
        request_id=style_request_id,
        changeset_id=changeset_id,
        committed_hash=style_committed_hash,
    )


def _handle_content_changed(sender, change_type, change_id, provider_name, changed_by, **kwargs):
    import logging
    logger = logging.getLogger(__name__)
    # If a SiteChangeSet publish is currently active on this thread, skip the
    # signal-driven rebuild — that publish already builds and promotes the site
    # atomically as part of its own 7-step workflow, so a concurrent rebuild
    # would race with it and re-include unrelated draft content.
    if getattr(_suppress_rebuild, "active", False):
        logger.debug(
            "cauldron.site.astro: suppressed signal-driven rebuild after %s of %s "
            "(inside SiteChangeSet publish)",
            change_type,
            change_id,
        )
        return
    try:
        from cauldron_site_astro.dispatcher import get_dispatcher
        get_dispatcher().dispatch()
        logger.info("cauldron.site.astro: build dispatched after %s of %s", change_type, change_id)
    except Exception:
        logger.exception("cauldron.site.astro: error dispatching build after %s of %s", change_type, change_id)
