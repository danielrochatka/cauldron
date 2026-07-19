"""Django system checks for cauldron.django.admin."""
from __future__ import annotations

from django.core import checks

_REQUIRED_APPS = [
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.admin",
]
_REQUIRED_MIDDLEWARE = [
    "django.contrib.messages.middleware.MessageMiddleware",
]
_REQUIRED_CONTEXT_PROCESSORS = [
    "django.contrib.messages.context_processors.messages",
]


def _is_admin_active() -> bool:
    try:
        from django.conf import settings
        modules = getattr(settings, "CAULDRON_MODULES", None)
        if modules is None:
            return False
        return "cauldron.django.admin" in modules
    except Exception:
        return False


@checks.register()
def check_admin_config(app_configs, **kwargs):
    """Validate the cauldron.django.admin configuration."""
    if not _is_admin_active():
        return []

    from django.conf import settings

    messages_list = []
    installed_apps = list(getattr(settings, "INSTALLED_APPS", []))
    middleware = list(getattr(settings, "MIDDLEWARE", []))
    templates = getattr(settings, "TEMPLATES", [])

    all_cp: list[str] = []
    for tmpl in templates:
        all_cp.extend(tmpl.get("OPTIONS", {}).get("context_processors", []))

    for app in _REQUIRED_APPS:
        if app not in installed_apps:
            messages_list.append(
                checks.Error(
                    f"cauldron.django.admin requires {app!r} in INSTALLED_APPS.",
                    hint=f"Add '{app}' to your INSTALLED_APPS setting.",
                    id="cauldron.admin.E300",
                )
            )

    for mw in _REQUIRED_MIDDLEWARE:
        if mw not in middleware:
            messages_list.append(
                checks.Error(
                    f"cauldron.django.admin requires {mw!r} in MIDDLEWARE.",
                    hint=f"Add '{mw}' to your MIDDLEWARE setting.",
                    id="cauldron.admin.E301",
                )
            )

    for cp in _REQUIRED_CONTEXT_PROCESSORS:
        if cp not in all_cp:
            messages_list.append(
                checks.Error(
                    f"cauldron.django.admin requires context processor {cp!r} in TEMPLATES.",
                    hint=f"Add '{cp}' to the context_processors in your TEMPLATES setting.",
                    id="cauldron.admin.E302",
                )
            )

    if not messages_list:
        messages_list.append(
            checks.Info(
                "cauldron.django.admin: admin configuration looks healthy.",
                id="cauldron.admin.I001",
            )
        )

    return messages_list
