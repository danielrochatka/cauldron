"""Cauldron Django State module definition."""
from cauldron.modules import BaseModule, ModuleManifest, ModulePresentation, ModuleSettingsDeclaration

_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14c0 1.66 4.03 3 9 3s9-1.34 9-3V5"/><path d="M3 12c0 1.66 4.03 3 9 3s9-1.34 9-3"/></svg>"""

_manifest = ModuleManifest(
    slug="cauldron.django.state",
    label="Cauldron Django State",
    version="0.1.0",
    cauldron_version=">=0.1.0",
    django_apps=("cauldron_django_state",),
    provides=(
        "django.state",
        "django.database",
        "django.transactions",
        "django.migrations",
    ),
    namespaces=("cauldron_django_state",),
    public_api=(
        "cauldron_django_state.checks",
        "cauldron_django_state.config",
    ),
    settings_declarations=(
        ModuleSettingsDeclaration(
            key="database_alias",
            required=False,
            description="Override the database alias used for Cauldron state tables. Defaults to 'default'.",
        ),
    ),
    presentation=ModulePresentation(
        title="Django State",
        summary="Provides Django database, sessions, and content-type foundations required by all other modules.",
        icon_svg=_ICON_SVG,
        group="Foundation",
        display_order=10,
    ),
)

module = BaseModule(_manifest)
