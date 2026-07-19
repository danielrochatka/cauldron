"""Django system checks for cauldron.cms.flatfile."""
from __future__ import annotations

from pathlib import Path

from django.core import checks


def _is_active() -> bool:
    try:
        from django.conf import settings
        modules = getattr(settings, "CAULDRON_MODULES", None)
        return bool(modules) and "cauldron.cms.flatfile" in modules
    except Exception:
        return False


def _get_config() -> dict:
    from django.conf import settings

    modules_setting = getattr(settings, "CAULDRON_MODULES", {}) or {}
    cfg = modules_setting.get("cauldron.cms.flatfile") or {}
    if not isinstance(cfg, dict):
        return {}
    return cfg


@checks.register(checks.Tags.compatibility)
def check_cms_config(app_configs, **kwargs):
    if not _is_active():
        return []

    errors: list = []
    cfg = _get_config()
    site_root = cfg.get("site_root")

    if site_root is None:
        errors.append(
            checks.Info(
                "cauldron.cms.flatfile: no site_root configured "
                "(runtime callers must supply one).",
                id="cauldron.cms.flatfile.I600",
            )
        )
        return errors

    root = Path(site_root)
    if not root.is_absolute():
        errors.append(
            checks.Error(
                "site_root must be an absolute path.",
                id="cauldron.cms.flatfile.E600",
            )
        )
        return errors
    if not root.exists():
        errors.append(
            checks.Error(
                f"site_root {site_root!r} does not exist.",
                id="cauldron.cms.flatfile.E601",
            )
        )
        return errors

    content_root = cfg.get("content_root", "content")
    if not isinstance(content_root, str) or Path(content_root).is_absolute():
        errors.append(
            checks.Error(
                "content_root must be a relative string path.",
                id="cauldron.cms.flatfile.E602",
            )
        )

    if not errors:
        errors.append(
            checks.Info(
                "cauldron.cms.flatfile: configuration looks healthy.",
                id="cauldron.cms.flatfile.I600",
            )
        )
    return errors
