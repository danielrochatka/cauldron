"""Cauldron Admin AI module definition."""
from cauldron.modules import (
    BaseModule,
    ModuleManifest,
    ModuleMigrationDeclaration,
    ModuleNavigationDeclaration,
    ModulePermissionDeclaration,
    ModuleRequirement,
    ModuleSettingsDeclaration,
)

_manifest = ModuleManifest(
    slug="cauldron.ai.admin",
    label="Cauldron Admin AI",
    version="0.1.0",
    cauldron_version=">=0.1.0",
    django_apps=("cauldron_ai_admin",),
    requires=(
        ModuleRequirement(slug="ai.model.providers", kind="capability"),
        ModuleRequirement(slug="content.operations", kind="capability"),
        ModuleRequirement(slug="admin.interface", kind="capability"),
        # Admin AI views/templates depend on the shell chrome and CSS
        # override plumbing. Declare it explicitly so resolution fails if
        # the shell is not present in the deployment.
        ModuleRequirement(slug="admin.shell", kind="capability"),
        ModuleRequirement(slug="django.state", kind="capability"),
        ModuleRequirement(slug="identity.authentication", kind="capability"),
        ModuleRequirement(slug="identity.permissions", kind="capability"),
        ModuleRequirement(slug="cauldron.ai", kind="module"),
        ModuleRequirement(slug="cauldron.django.admin", kind="module"),
        ModuleRequirement(slug="cauldron.admin.content", kind="module"),
    ),
    optional=(
        ModuleRequirement(slug="cauldron.cms.flatfile", kind="module"),
    ),
    provides=(
        "admin.ai",
        "admin.ai.orchestration",
        "admin.ai.tools",
        "admin.ai.audit",
        "admin.ai.health",
    ),
    namespaces=("cauldron_ai_admin",),
    public_api=(
        "cauldron_ai_admin.tools",
        "cauldron_ai_admin.views",
        "cauldron_ai_admin.service",
        "cauldron_ai_admin.builtin_tools",
    ),
    settings_declarations=(
        ModuleSettingsDeclaration(
            key="provider",
            required=False,
            description="Active AI provider name, e.g. 'openai'. Admin AI is disabled when absent.",
        ),
        ModuleSettingsDeclaration(
            key="provider_config",
            required=False,
            description="Provider-specific configuration dict keyed by provider name.",
        ),
    ),
    migration_apps=(
        ModuleMigrationDeclaration(app_label="cauldron_ai_admin"),
    ),
    permissions=(
        ModulePermissionDeclaration(
            codename="use_admin_ai",
            name="Can invoke the Admin AI assistant",
            app_label="cauldron_ai_admin",
        ),
        ModulePermissionDeclaration(
            codename="view_admin_ai_runs",
            name="Can view Admin AI run history",
            app_label="cauldron_ai_admin",
        ),
        ModulePermissionDeclaration(
            codename="view_admin_ai_audit",
            name="Can view Admin AI audit records",
            app_label="cauldron_ai_admin",
        ),
        ModulePermissionDeclaration(
            codename="manage_admin_ai_settings",
            name="Can manage Admin AI settings",
            app_label="cauldron_ai_admin",
        ),
        ModulePermissionDeclaration(
            codename="view_ui_styles",
            name="Can view UI style overrides",
            app_label="cauldron_ai_admin",
        ),
        ModulePermissionDeclaration(
            codename="propose_ui_style_changes",
            name="Can propose UI style changes",
            app_label="cauldron_ai_admin",
        ),
        ModulePermissionDeclaration(
            codename="approve_ui_style_changes",
            name="Can approve UI style changes",
            app_label="cauldron_ai_admin",
        ),
        ModulePermissionDeclaration(
            codename="view_ui_style_audit",
            name="Can view UI style change audit",
            app_label="cauldron_ai_admin",
        ),
    ),
    navigation=(
        ModuleNavigationDeclaration(key="ai", label="Admin AI"),
        ModuleNavigationDeclaration(key="cauldron.ai.admin.page", label="AI Assistant", section="ai"),
        ModuleNavigationDeclaration(key="cauldron.ai.admin.runs", label="AI Runs", section="ai"),
        ModuleNavigationDeclaration(key="cauldron.ui.styles", label="Style Proposals", section="ai"),
        ModuleNavigationDeclaration(key="cauldron.ai.admin.settings", label="Settings", section="ai"),
    ),
    ai_tools=(
        "content.list_collections",
        "content.list_items",
        "content.get_item",
        "content.create_proposal",
        "content.preview_change_request",
        "system.django_checks",
        "system.module_status",
        "system.admin_ai_inventory",
        "ui.styles.list_files",
        "ui.styles.read_file",
        "ui.styles.create_proposal",
        "ui.styles.preview_proposal",
    ),
    prompt_templates=(
        "content.list_collections",
        "content.list_items",
        "content.get_item",
        "content.create_proposal",
        "content.preview_change_request",
        "system.django_checks",
        "system.module_status",
        "system.admin_ai_inventory",
        "ui.styles.list_files",
        "ui.styles.read_file",
        "ui.styles.create_proposal",
        "ui.styles.preview_proposal",
    ),
)

module = BaseModule(_manifest)
