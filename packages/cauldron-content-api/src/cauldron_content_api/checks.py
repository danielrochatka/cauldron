"""Django system checks for cauldron.content.api."""
from __future__ import annotations

from django.core import checks


def _is_api_active() -> bool:
    try:
        from django.conf import settings
        modules = getattr(settings, "CAULDRON_MODULES", None)
        return modules is not None and "cauldron.content.api" in modules
    except Exception:
        return False


@checks.register(checks.Tags.compatibility)
def check_api_dependencies(app_configs, **kwargs):
    if not _is_api_active():
        return []
    errors = []
    from django.conf import settings
    installed = list(getattr(settings, "INSTALLED_APPS", []))
    required = ["cauldron_content_operations", "cauldron_content_api"]
    for app in required:
        if app not in installed:
            errors.append(checks.Error(
                f"cauldron.content.api requires {app!r} in INSTALLED_APPS.",
                id="cauldron.content.api.E800",
            ))
    if not errors:
        errors.append(checks.Info(
            "cauldron.content.api configuration looks healthy.",
            id="cauldron.content.api.I001",
        ))
    return errors
