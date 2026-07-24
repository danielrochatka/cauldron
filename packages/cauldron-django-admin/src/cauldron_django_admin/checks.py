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
    "django.template.context_processors.request",
]
_RECOMMENDED_CONTEXT_PROCESSORS = [
    "django.template.context_processors.debug",
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

    # W309: recommended context processors
    for cp in _RECOMMENDED_CONTEXT_PROCESSORS:
        if cp not in all_cp:
            messages_list.append(
                checks.Warning(
                    f"cauldron.django.admin recommends context processor {cp!r} in TEMPLATES.",
                    hint=f"Add '{cp}' to the context_processors in your TEMPLATES setting.",
                    id="cauldron.admin.W309",
                )
            )

    # E303: cauldron_django_admin must be in INSTALLED_APPS
    if "cauldron_django_admin" not in installed_apps:
        messages_list.append(
            checks.Error(
                "cauldron.django.admin requires 'cauldron_django_admin' in INSTALLED_APPS.",
                hint="Add 'cauldron_django_admin' to your INSTALLED_APPS setting.",
                id="cauldron.admin.E303",
            )
        )

    # E304: check that CAULDRON_UI_OVERRIDES_DIR is readable if set
    override_dir = getattr(settings, "CAULDRON_UI_OVERRIDES_DIR", None)
    if override_dir is not None:
        from pathlib import Path
        od = Path(override_dir)
        if od.exists() and not od.is_dir():
            messages_list.append(
                checks.Error(
                    "CAULDRON_UI_OVERRIDES_DIR exists but is not a directory.",
                    hint="Set CAULDRON_UI_OVERRIDES_DIR to a valid directory path.",
                    id="cauldron.admin.E304",
                )
            )

    # E305: shell templates present
    try:
        import importlib.resources as pkg_resources
        from pathlib import Path
        import cauldron_django_admin
        pkg_path = Path(cauldron_django_admin.__file__).parent
        template_path = pkg_path / "templates" / "cauldron_admin" / "base.html"
        if not template_path.is_file():
            messages_list.append(
                checks.Error(
                    "cauldron.django.admin shell template 'cauldron_admin/base.html' is missing.",
                    hint="Re-install the cauldron-django-admin package.",
                    id="cauldron.admin.E305",
                )
            )
    except Exception:
        pass

    # E306: packaged static assets present
    try:
        from pathlib import Path
        import cauldron_django_admin
        pkg_path = Path(cauldron_django_admin.__file__).parent
        token_css = pkg_path / "static" / "cauldron_admin" / "css" / "tokens.css"
        if not token_css.is_file():
            messages_list.append(
                checks.Error(
                    "cauldron.django.admin packaged static asset 'cauldron_admin/css/tokens.css' is missing.",
                    hint="Re-install the cauldron-django-admin package.",
                    id="cauldron.admin.E306",
                )
            )
    except Exception:
        pass

    # E307: override root readable if configured
    if override_dir is not None:
        from pathlib import Path
        od = Path(override_dir)
        if od.exists() and od.is_dir():
            try:
                list(od.iterdir())
            except PermissionError:
                messages_list.append(
                    checks.Error(
                        "CAULDRON_UI_OVERRIDES_DIR exists but is not readable.",
                        hint="Ensure the process has read permissions on CAULDRON_UI_OVERRIDES_DIR.",
                        id="cauldron.admin.E307",
                    )
                )

    # W310: cauldron-overrides dir writability warning if dir exists
    override_dir_check = override_dir
    if override_dir_check is None:
        base_dir = getattr(settings, "BASE_DIR", None)
        if base_dir is not None:
            from pathlib import Path
            override_dir_check = str(Path(base_dir) / "cauldron-overrides")
    if override_dir_check is not None:
        from pathlib import Path
        od = Path(override_dir_check)
        if od.exists() and od.is_dir():
            import os
            if not os.access(str(od), os.W_OK):
                messages_list.append(
                    checks.Warning(
                        "cauldron-overrides directory exists but is not writable by the current process.",
                        hint="Ensure write permissions on the override directory for CSS override uploads.",
                        id="cauldron.admin.W310",
                    )
                )

    # Only add the healthy info if there are no errors (warnings are OK)
    from django.core.checks import Error as _Error
    has_errors = any(isinstance(m, _Error) for m in messages_list)
    if not has_errors:
        messages_list.append(
            checks.Info(
                "cauldron.django.admin: admin configuration looks healthy.",
                id="cauldron.admin.I001",
            )
        )

    return messages_list
