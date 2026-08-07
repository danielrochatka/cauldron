"""Django AppConfig for cauldron_cms_flatfile."""
from django.apps import AppConfig

_OWNING_MODULE = "cauldron.cms.flatfile"


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
    Idempotent for repeated ready() calls when the provider is already owned by
    cauldron.cms.flatfile. Raises RegistrationError if PROVIDER_NAME is already
    occupied by a different or unknown owner.
    """
    from pathlib import Path

    from django.conf import settings
    from django.core.exceptions import ImproperlyConfigured

    from cauldron_content.registry import RegistrationError, registry

    from .config import FlatFileCMSConfig
    from .repository import PROVIDER_NAME, FlatFileRepository

    modules = getattr(settings, "CAULDRON_MODULES", None) or {}
    if _OWNING_MODULE not in modules:
        return

    cfg = modules[_OWNING_MODULE] or {}
    site_root = cfg.get("site_root")
    if not site_root:
        return

    site_root_path = Path(site_root)
    if not site_root_path.is_absolute():
        raise ImproperlyConfigured(
            f"{_OWNING_MODULE}.site_root must be an absolute path."
        )

    existing = registry.get(PROVIDER_NAME)
    if existing is not None:
        existing_owner = registry.get_owning_module(PROVIDER_NAME)
        if existing_owner == _OWNING_MODULE:
            return  # idempotent: already registered by us
        owner_desc = f"owned by {existing_owner!r}" if existing_owner else "has no owning module"
        raise RegistrationError(
            provider_name=PROVIDER_NAME,
            message=(
                f"Cannot register content provider {PROVIDER_NAME!r} for "
                f"{_OWNING_MODULE!r}: a provider already exists that {owner_desc}."
            ),
        )

    try:
        cms_cfg = FlatFileCMSConfig(
            site_root=site_root,
            content_root=cfg.get("content_root", "content"),
            schema_root=cfg.get("schema_root", "schemas"),
        )
    except ValueError as exc:
        raise ImproperlyConfigured(
            f"{_OWNING_MODULE} misconfiguration: {exc}"
        ) from exc

    registry.register(PROVIDER_NAME, FlatFileRepository(cms_cfg), owning_module=_OWNING_MODULE)
