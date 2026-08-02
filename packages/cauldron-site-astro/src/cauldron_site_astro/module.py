"""Cauldron Site Astro module definition."""
from cauldron.modules import (
    BaseModule,
    ModuleManifest,
    ModuleMigrationDeclaration,
    ModuleRequirement,
    ModuleSettingsDeclaration,
)

_manifest = ModuleManifest(
    slug="cauldron.site.astro",
    label="Cauldron Site Astro",
    version="0.1.0",
    cauldron_version=">=0.1.0",
    django_apps=("cauldron_site_astro",),
    requires=(
        ModuleRequirement(slug="content.routing", kind="capability"),
        ModuleRequirement(slug="cauldron.content", kind="module"),
        ModuleRequirement(slug="cauldron.content.operations", kind="module"),
    ),
    optional=(
        ModuleRequirement(slug="cauldron.ai.admin"),
        ModuleRequirement(slug="cauldron.admin.content"),
        ModuleRequirement(slug="cauldron.ai", kind="module"),
    ),
    provides=("site.public", "site.build"),
    namespaces=("cauldron_site_astro",),
    public_api=(
        "cauldron_site_astro.service",
        "cauldron_site_astro.config",
        "cauldron_site_astro.public_url",
        "cauldron_site_astro.site_tools",
        "cauldron_site_astro.urls",
    ),
    capability_implementations=(
        # Concrete SitePublicUrlProvider — external callers use
        # cauldron_content.site.get_public_url() not this class directly.
        "cauldron_site_astro.public_url",
    ),
    settings_declarations=(
        ModuleSettingsDeclaration(
            key="frontend_root",
            required=True,
            description="Absolute path to the Astro frontend source directory.",
        ),
        ModuleSettingsDeclaration(
            key="output_root",
            required=True,
            description="Absolute path to the Astro build output directory.",
        ),
        ModuleSettingsDeclaration(
            key="homepage_item_id",
            required=False,
            description="Navigation item ID for the homepage; defaults to 'homepage'.",
        ),
        ModuleSettingsDeclaration(
            key="npm_command",
            required=False,
            description="npm CLI command to invoke for Astro builds; defaults to 'npm'.",
        ),
        ModuleSettingsDeclaration(
            key="build_timeout",
            required=False,
            description="Maximum seconds to wait for an Astro build before timing out; defaults to 120.",
        ),
        ModuleSettingsDeclaration(
            key="manage_py_path",
            required=False,
            description="Absolute path to manage.py, required when Astro builds invoke Django management commands.",
        ),
        ModuleSettingsDeclaration(
            key="build_log_file",
            required=False,
            description="Absolute path to write Astro build log output; defaults to stderr.",
        ),
        ModuleSettingsDeclaration(
            key="theme_root",
            required=False,
            description="Absolute path to the theme directory where active.css and staged.css are stored.",
        ),
        ModuleSettingsDeclaration(
            key="previews_root",
            required=False,
            description="Absolute path for preview build output. Required when preview builds are used.",
        ),
    ),
    migration_apps=(
        ModuleMigrationDeclaration(app_label="cauldron_site_astro"),
    ),
    ai_tools=(
        "site.verify_root",
        "site.inspect",
        "site.stage_theme",
        "site.propose_homepage",
        "site.prepare_change_set",
        "site.inspect_preview",
        "site.publish",
    ),
    prompt_templates=(
        "site.verify_root",
        "site.inspect",
        "site.stage_theme",
        "site.propose_homepage",
        "site.prepare_change_set",
        "site.inspect_preview",
        "site.publish",
    ),
)

module = BaseModule(_manifest)
