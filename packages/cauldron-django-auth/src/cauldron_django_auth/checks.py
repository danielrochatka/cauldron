"""Django system checks for cauldron.django.auth."""
from __future__ import annotations

from django.core import checks

_REQUIRED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
]
_REQUIRED_MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
]
_REQUIRED_CONTEXT_PROCESSORS = [
    "django.contrib.auth.context_processors.auth",
]


def _is_auth_active() -> bool:
    try:
        from django.conf import settings
        modules = getattr(settings, "CAULDRON_MODULES", None)
        if modules is None:
            return False
        return "cauldron.django.auth" in modules
    except Exception:
        return False


@checks.register()
def check_auth_config(app_configs, **kwargs):
    """Validate the cauldron.django.auth configuration."""
    if not _is_auth_active():
        return []

    from django.conf import settings

    messages = []
    installed_apps = list(getattr(settings, "INSTALLED_APPS", []))
    middleware = list(getattr(settings, "MIDDLEWARE", []))
    templates = getattr(settings, "TEMPLATES", [])

    # Gather all context processors from all template backends.
    all_cp: list[str] = []
    for tmpl in templates:
        all_cp.extend(tmpl.get("OPTIONS", {}).get("context_processors", []))

    auth_user_model = getattr(settings, "AUTH_USER_MODEL", "auth.User")
    if "." not in auth_user_model:
        messages.append(
            checks.Error(
                f"AUTH_USER_MODEL {auth_user_model!r} must be in 'app_label.ModelName' format.",
                id="cauldron.auth.E200",
            )
        )

    for app in _REQUIRED_APPS:
        if app not in installed_apps:
            messages.append(
                checks.Error(
                    f"cauldron.django.auth requires {app!r} in INSTALLED_APPS.",
                    hint=f"Add '{app}' to your INSTALLED_APPS setting.",
                    id="cauldron.auth.E201",
                )
            )

    for mw in _REQUIRED_MIDDLEWARE:
        if mw not in middleware:
            messages.append(
                checks.Error(
                    f"cauldron.django.auth requires {mw!r} in MIDDLEWARE.",
                    hint=f"Add '{mw}' to your MIDDLEWARE setting.",
                    id="cauldron.auth.E202",
                )
            )

    for cp in _REQUIRED_CONTEXT_PROCESSORS:
        if cp not in all_cp:
            messages.append(
                checks.Error(
                    f"cauldron.django.auth requires context processor {cp!r} in TEMPLATES.",
                    hint=f"Add '{cp}' to the context_processors in your TEMPLATES setting.",
                    id="cauldron.auth.E203",
                )
            )

    if not messages:
        messages.append(
            checks.Info(
                "cauldron.django.auth: auth configuration looks healthy.",
                id="cauldron.auth.I001",
            )
        )

    return messages
