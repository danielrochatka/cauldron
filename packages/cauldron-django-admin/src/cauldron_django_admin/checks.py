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

    # E308: override root must not be a symlink
    if override_dir is not None:
        from pathlib import Path
        od = Path(override_dir)
        if od.is_symlink():
            messages_list.append(checks.Error(
                "CAULDRON_UI_OVERRIDES_DIR is a symbolic link. "
                "Configure a real directory as the override root.",
                hint="Set CAULDRON_UI_OVERRIDES_DIR to a real directory, not a symlink.",
                id="cauldron.admin.E308",
            ))

    # E305: shell templates present
    try:
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
    except Exception as exc:
        messages_list.append(checks.Error(
            f"cauldron.django.admin: error inspecting shell templates: {exc}",
            hint="Re-install the cauldron-django-admin package.",
            id="cauldron.admin.E305",
        ))

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
    except Exception as exc:
        messages_list.append(checks.Error(
            f"cauldron.django.admin: error inspecting packaged static assets: {exc}",
            hint="Re-install the cauldron-django-admin package.",
            id="cauldron.admin.E306",
        ))

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

    # W313: raw-tree validation issues
    if override_dir is not None:
        from pathlib import Path
        od_check = Path(override_dir)
        if od_check.exists() and od_check.is_dir() and not od_check.is_symlink():
            try:
                from cauldron_django_admin.override_store import validate_override_tree
                tree_issues = validate_override_tree(od_check)
                for issue in tree_issues:
                    messages_list.append(checks.Warning(
                        f"Override directory: {issue}",
                        hint="Run 'manage.py cauldron_ui_init --check' for details.",
                        id="cauldron.admin.W313",
                    ))
            except Exception as exc:
                messages_list.append(checks.Warning(
                    f"Override directory validation failed: {exc}",
                    id="cauldron.admin.W313",
                ))

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

    # W312: scope directories must not be symlinks
    override_dir_w = override_dir if override_dir is not None else (
        str(Path(getattr(settings, "BASE_DIR", "")) / "cauldron-overrides")
        if getattr(settings, "BASE_DIR", None) is not None else None
    )
    if override_dir_w is not None:
        from pathlib import Path
        od_w = Path(override_dir_w)
        if od_w.exists() and od_w.is_dir() and not od_w.is_symlink():
            for scope in ("admin", "pages"):
                scope_path = od_w / scope
                if scope_path.exists() and scope_path.is_symlink():
                    messages_list.append(checks.Warning(
                        f"Override scope directory {scope!r} is a symbolic link. "
                        "The store will reject all writes through this scope.",
                        hint=f"Replace {scope_path} with a real directory.",
                        id="cauldron.admin.W312",
                    ))

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


@checks.register()
def check_navigation_urls(app_configs, **kwargs):
    """W311: every registered navigation item must have a reversible URL name."""
    if not _is_admin_active():
        return []

    from django.urls import reverse, NoReverseMatch
    from .navigation import get_navigation_registry

    messages_list: list = []
    registry = get_navigation_registry()

    try:
        with registry._lock:
            items = list(registry._items.values())
    except Exception:
        return []

    for item in items:
        try:
            reverse(item.url_name)
        except NoReverseMatch:
            messages_list.append(
                checks.Warning(
                    f"Navigation item {item.key!r} has URL name {item.url_name!r} "
                    "that cannot be reversed.",
                    hint=(
                        "Ensure the URL pattern is registered and the "
                        "namespace is correct."
                    ),
                    id="cauldron.admin.W311",
                )
            )
        except Exception:
            # A reverse() failure other than NoReverseMatch (e.g. import
            # error inside a URL module) is surfaced by the URL system
            # itself; do not double-report here.
            continue
    return messages_list


@checks.register(checks.Tags.compatibility)
def check_manifest_navigation_registered(app_configs, **kwargs):
    """cauldron.admin.E309-E312: manifest nav declarations must match NavigationRegistry.

    For each active Cauldron module that declares navigation entries:
    - E309: Section declared in manifest but not registered.
    - E310: Item declared in manifest but not registered.
    - E311: Section registered but owned by a different module.
    - E312: Item registered but owned by a different module.
    """
    messages_list: list = []
    try:
        from cauldron.modules.registry import registry as module_registry
    except Exception:
        return []

    if not module_registry.is_ready:
        return []

    from .navigation import get_navigation_registry
    nav_registry = get_navigation_registry()

    with nav_registry._lock:
        registered_sections = dict(nav_registry._sections)
        registered_items = dict(nav_registry._items)

    for module in module_registry.all_active():
        manifest = module.manifest
        for nav in manifest.navigation:
            is_section = not nav.section
            if is_section:
                registered = registered_sections.get(nav.key)
                if registered is None:
                    messages_list.append(checks.Error(
                        f"Module {manifest.slug!r} declares navigation section "
                        f"{nav.key!r} but it is not registered in NavigationRegistry.",
                        hint=(
                            f"Ensure {manifest.slug!r} registers {nav.key!r} "
                            "as an AdminNavigationSection in AppConfig.ready()."
                        ),
                        obj=manifest.slug,
                        id="cauldron.admin.E309",
                    ))
                elif registered.owning_module and registered.owning_module != manifest.slug:
                    messages_list.append(checks.Error(
                        f"Module {manifest.slug!r} declares navigation section "
                        f"{nav.key!r} but it is registered with "
                        f"owning_module={registered.owning_module!r}.",
                        hint=(
                            f"Only the registering module should declare the section "
                            f"in its manifest. Check {manifest.slug!r} and "
                            f"{registered.owning_module!r}."
                        ),
                        obj=manifest.slug,
                        id="cauldron.admin.E311",
                    ))
            else:
                registered = registered_items.get(nav.key)
                if registered is None:
                    messages_list.append(checks.Error(
                        f"Module {manifest.slug!r} declares navigation item "
                        f"{nav.key!r} but it is not registered in NavigationRegistry.",
                        hint=(
                            f"Ensure {manifest.slug!r} registers {nav.key!r} "
                            "as an AdminNavigationItem in AppConfig.ready()."
                        ),
                        obj=manifest.slug,
                        id="cauldron.admin.E310",
                    ))
                elif registered.owning_module and registered.owning_module != manifest.slug:
                    messages_list.append(checks.Error(
                        f"Module {manifest.slug!r} declares navigation item "
                        f"{nav.key!r} but it is registered with "
                        f"owning_module={registered.owning_module!r}.",
                        hint=(
                            f"Only the registering module should declare the item "
                            f"in its manifest. Check {manifest.slug!r} and "
                            f"{registered.owning_module!r}."
                        ),
                        obj=manifest.slug,
                        id="cauldron.admin.E312",
                    ))

    return messages_list
