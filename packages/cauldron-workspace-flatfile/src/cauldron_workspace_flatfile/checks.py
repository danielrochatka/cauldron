"""Django system checks for cauldron.workspace.flatfile."""
from __future__ import annotations

from pathlib import Path

from django.core import checks


def _is_active() -> bool:
    try:
        from django.conf import settings
        modules = getattr(settings, "CAULDRON_MODULES", None)
        return bool(modules) and "cauldron.workspace.flatfile" in modules
    except Exception:
        return False


@checks.register(checks.Tags.compatibility)
def check_workspace_config(app_configs, **kwargs):
    if not _is_active():
        return []
    from django.conf import settings

    errors: list = []
    modules_setting = getattr(settings, "CAULDRON_MODULES", {}) or {}
    cfg = modules_setting.get("cauldron.workspace.flatfile", {}) or {}
    if not isinstance(cfg, dict):
        errors.append(
            checks.Error(
                "CAULDRON_MODULES['cauldron.workspace.flatfile'] must be a dict.",
                id="cauldron.workspace.flatfile.E500",
            )
        )
        return errors

    workspace_root = cfg.get("workspace_root")
    if workspace_root is None:
        errors.append(
            checks.Info(
                "cauldron.workspace.flatfile: no workspace_root configured "
                "(runtime callers must supply one).",
                id="cauldron.workspace.flatfile.I500",
            )
        )
        return errors

    root = Path(workspace_root)
    if not root.is_absolute():
        errors.append(
            checks.Error(
                "workspace_root must be an absolute path.",
                id="cauldron.workspace.flatfile.E501",
            )
        )
        return errors

    if not errors:
        errors.append(
            checks.Info(
                "cauldron.workspace.flatfile: workspace configuration looks healthy.",
                id="cauldron.workspace.flatfile.I500",
            )
        )
    return errors
