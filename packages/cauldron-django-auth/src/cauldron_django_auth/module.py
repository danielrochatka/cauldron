"""Cauldron Django Auth module definition."""
from cauldron.modules import BaseModule, ModuleManifest, ModuleMigrationDeclaration, ModulePresentation, ModuleRequirement

_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>"""

_manifest = ModuleManifest(
    slug="cauldron.django.auth",
    label="Cauldron Django Auth",
    version="0.1.0",
    cauldron_version=">=0.1.0",
    django_apps=(
        "django.contrib.contenttypes",
        "django.contrib.auth",
        "django.contrib.sessions",
        "cauldron_django_auth",
    ),
    django_middleware=(
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
    ),
    django_context_processors=(
        "django.contrib.auth.context_processors.auth",
    ),
    requires=(ModuleRequirement(slug="cauldron.django.state"),),
    provides=(
        "identity.users",
        "identity.roles",
        "identity.permissions",
        "identity.sessions",
        "identity.authentication",
        "identity.password.reset",
    ),
    namespaces=("cauldron_django_auth",),
    public_api=(
        "cauldron_django_auth.apps",
    ),
    migration_apps=(
        # Django's built-in app labels differ from their dotted app paths.
        # "contenttypes" → django.contrib.contenttypes
        # "auth"         → django.contrib.auth
        # "sessions"     → django.contrib.sessions
        ModuleMigrationDeclaration(app_label="contenttypes"),
        ModuleMigrationDeclaration(app_label="auth"),
        ModuleMigrationDeclaration(app_label="sessions"),
    ),
    presentation=ModulePresentation(
        title="Django Auth",
        summary="Authentication, authorisation, user management, and login flows.",
        icon_svg=_ICON_SVG,
        group="Foundation",
        display_order=20,
    ),
)

module = BaseModule(_manifest)
