"""Django AppConfig for cauldron_cms_flatfile."""
from django.apps import AppConfig


class CauldronCmsFlatfileConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "cauldron_cms_flatfile"
    verbose_name = "Cauldron Flat-File CMS"

    def ready(self) -> None:
        from . import checks  # noqa: F401
        _register_provider()


def _register_provider() -> None:
    """Register FlatFileRepository in the global content registry at Django startup.

    Reads CAULDRON_MODULES["cauldron.cms.flatfile"] for site_root. No-op when
    the module is absent or site_root is not configured (Django checks emit I600).
    Idempotent: safe to call on repeated ready() invocations.
    """
    from pathlib import Path

    from django.conf import settings
    from django.core.exceptions import ImproperlyConfigured

    from cauldron_content.registry import RegistrationError, registry

    from .config import FlatFileCMSConfig
    from .repository import PROVIDER_NAME, FlatFileRepository

    modules = getattr(settings, "CAULDRON_MODULES", None) or {}
    if "cauldron.cms.flatfile" not in modules:
        return

    cfg = modules["cauldron.cms.flatfile"] or {}
    site_root = cfg.get("site_root")
    if not site_root:
        return

    site_root_path = Path(site_root)
    if not site_root_path.is_absolute():
        raise ImproperlyConfigured(
            "cauldron.cms.flatfile.site_root must be an absolute path."
        )

    if registry.get(PROVIDER_NAME) is not None:
        return

    try:
        cms_cfg = FlatFileCMSConfig(
            site_root=site_root,
            content_root=cfg.get("content_root", "content"),
            schema_root=cfg.get("schema_root", "schemas"),
        )
    except ValueError as exc:
        raise ImproperlyConfigured(
            f"cauldron.cms.flatfile misconfiguration: {exc}"
        ) from exc

    repo = FlatFileRepository(cms_cfg)
    try:
        registry.register(PROVIDER_NAME, repo)
    except RegistrationError:
        pass
