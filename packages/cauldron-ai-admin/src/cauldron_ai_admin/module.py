"""Cauldron Admin AI module definition."""
from cauldron.modules import (
    BaseModule,
    ModuleManifest,
    ModuleMigrationDeclaration,
    ModuleNavigationDeclaration,
    ModulePermissionDeclaration,
    ModulePresentation,
    ModuleRequirement,
    ModuleSettingsDeclaration,
)

_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="10" rx="2"/><path d="M12 11V5"/><circle cx="12" cy="4" r="1"/><path d="M8 11V9"/><path d="M16 11V9"/><path d="M8 15h.01"/><path d="M12 15h.01"/><path d="M16 15h.01"/></svg>"""

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
        ModuleRequirement(slug="cauldron.content.operations", kind="module"),
    ),
    optional=(
        ModuleRequirement(slug="cauldron.cms.flatfile", kind="module"),
        ModuleRequirement(slug="cauldron.ai.attachments", kind="module"),
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
        "cauldron_ai_admin.models",
        "cauldron_ai_admin.style_service",
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
        ModuleSettingsDeclaration(
            key="max_model_turns",
            required=False,
            description="Maximum number of model turns per run before the run is aborted.",
        ),
        ModuleSettingsDeclaration(
            key="max_tool_calls",
            required=False,
            description="Maximum number of tool calls per run before the run is aborted.",
        ),
        ModuleSettingsDeclaration(
            key="tool_timeout_seconds",
            required=False,
            description="Per-tool execution timeout in seconds.",
        ),
        ModuleSettingsDeclaration(
            key="run_timeout_seconds",
            required=False,
            description="Wall-clock timeout for an entire run in seconds.",
        ),
        ModuleSettingsDeclaration(
            key="max_argument_bytes",
            required=False,
            description="Maximum byte size of a single tool call argument payload.",
        ),
        ModuleSettingsDeclaration(
            key="max_result_bytes",
            required=False,
            description="Maximum byte size of a single tool call result payload.",
        ),
        ModuleSettingsDeclaration(
            key="include_content_tools",
            required=False,
            description="Whether to include content-management tools in the AI tool set.",
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
        ModuleNavigationDeclaration(key="ai", label="Admin AI", order=500),
        ModuleNavigationDeclaration(
            key="cauldron.ai.admin.page",
            label="AI Assistant",
            section="ai",
            url_name="cauldron_ai_admin:ai-page",
            order=10,
            permission="cauldron_ai_admin.use_admin_ai",
            url_prefix="/cauldron/admin/ai/",
            description="Interact with the Admin AI assistant",
        ),
        ModuleNavigationDeclaration(
            key="cauldron.ai.admin.runs",
            label="AI Runs",
            section="ai",
            url_name="cauldron_ai_admin:run-list",
            order=20,
            permission="cauldron_ai_admin.view_admin_ai_runs",
            url_prefix="/cauldron/admin/ai/runs/",
            description="View Admin AI run history",
        ),
        ModuleNavigationDeclaration(
            key="cauldron.ui.styles",
            label="Style Proposals",
            section="ai",
            url_name="cauldron_ai_admin:style-list",
            order=30,
            permission="cauldron_ai_admin.view_ui_styles",
            url_prefix="/cauldron/ui/style-changes/",
            description="Review AI-proposed CSS changes",
        ),
        ModuleNavigationDeclaration(
            key="cauldron.ai.admin.settings",
            label="Settings",
            section="ai",
            url_name="cauldron_ai_admin:settings",
            order=40,
            permission="cauldron_ai_admin.manage_admin_ai_settings",
            url_prefix="/cauldron/admin/ai/settings/",
            description="Configure the Admin AI module",
        ),
    ),
    presentation=ModulePresentation(
        title="Admin AI",
        summary="AI admin interface — AI-assisted content operations accessible from the operator shell.",
        icon_svg=_ICON_SVG,
        group="AI",
        display_order=30,
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
