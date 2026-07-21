"""Django system checks for cauldron.admin.content."""
from __future__ import annotations

from django.core import checks


def _is_admin_content_active() -> bool:
    try:
        from django.conf import settings
        modules = getattr(settings, "CAULDRON_MODULES", None)
        return modules is not None and "cauldron.admin.content" in modules
    except Exception:
        return False


@checks.register(checks.Tags.compatibility)
def check_admin_content_dependencies(app_configs, **kwargs):
    if not _is_admin_content_active():
        return []
    errors = []
    from django.conf import settings
    installed = list(getattr(settings, "INSTALLED_APPS", []))
    required = [
        "django.contrib.admin",
        "django.contrib.contenttypes",
        "django.contrib.auth",
        "cauldron_content_operations",
        "cauldron_admin_content",
    ]
    for app in required:
        if app not in installed:
            errors.append(checks.Error(
                f"cauldron.admin.content requires {app!r} in INSTALLED_APPS.",
                id="cauldron.admin.content.E900",
            ))
    if not errors:
        errors.append(checks.Info(
            "cauldron.admin.content configuration looks healthy.",
            id="cauldron.admin.content.I001",
        ))
    return errors
