"""Cauldron Django Admin module definition."""
from cauldron.modules import (
    BaseModule,
    ModuleManifest,
    ModuleNavigationDeclaration,
    ModulePresentation,
    ModuleRequirement,
    ModuleSettingsDeclaration,
)

_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z"/></svg>"""

_manifest = ModuleManifest(
    slug="cauldron.django.admin",
    label="Cauldron Technical Admin",
    version="0.1.0",
    cauldron_version=">=0.1.0",
    django_apps=(
        "django.contrib.messages",
        "django.contrib.staticfiles",
        "django.contrib.admin",
        "cauldron_django_admin",
    ),
    django_middleware=(
        "django.contrib.messages.middleware.MessageMiddleware",
    ),
    django_context_processors=(
        "django.contrib.messages.context_processors.messages",
    ),
    requires=(ModuleRequirement(slug="cauldron.django.auth"),),
    provides=(
        "admin.interface",
        "admin.users",
        "admin.roles",
        "admin.permissions",
        "admin.shell",
        "admin.navigation",
        "admin.ui.overrides",
    ),
    namespaces=("cauldron_django_admin",),
    public_api=(
        "cauldron_django_admin.navigation",
        "cauldron_django_admin.override_store",
        "cauldron_django_admin.override_views",
        "cauldron_django_admin.module_settings",
        "cauldron_django_admin.views",
        "cauldron_django_admin.urls",
    ),
    settings_declarations=(
        ModuleSettingsDeclaration(
            key="ui_overrides_dir",
            required=False,
            description="Absolute path to a directory of admin CSS/JS override files.",
            setting_path="CAULDRON_UI_OVERRIDES_DIR",
        ),
    ),
    navigation=(
        ModuleNavigationDeclaration(key="overview", label="Overview", order=10),
        ModuleNavigationDeclaration(
            key="cauldron.dashboard",
            label="Dashboard",
            section="overview",
            url_name="cauldron:dashboard",
            order=10,
            permission="",
            url_prefix="/cauldron/",
            url_prefix_exact=True,
        ),
        ModuleNavigationDeclaration(key="system", label="System", order=900),
        ModuleNavigationDeclaration(
            key="cauldron.modules",
            label="Modules",
            section="system",
            url_name="cauldron:modules",
            order=10,
            permission="",
            url_prefix="/cauldron/modules/",
            description="Active Cauldron modules and capabilities",
        ),
    ),
    presentation=ModulePresentation(
        title="Django Admin",
        summary="Cauldron admin shell — navigation, breadcrumbs, sidebar, and the authenticated operator interface.",
        icon_svg=_ICON_SVG,
        group="Foundation",
        display_order=30,
    ),
)

module = BaseModule(_manifest)
