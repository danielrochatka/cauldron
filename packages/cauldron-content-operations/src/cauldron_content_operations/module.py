"""Cauldron Content Operations module definition."""
from cauldron.modules import (
    BaseModule,
    ModuleManifest,
    ModuleMigrationDeclaration,
    ModulePermissionDeclaration,
    ModulePresentation,
    ModuleRequirement,
)

_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14M4.93 19.07a10 10 0 0 1 0-14.14"/><line x1="12" y1="2" x2="12" y2="4"/><line x1="12" y1="20" x2="12" y2="22"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="2" y1="12" x2="4" y2="12"/><line x1="20" y1="12" x2="22" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>"""

_manifest = ModuleManifest(
    slug="cauldron.content.operations",
    label="Cauldron Content Operations",
    version="0.1.0",
    cauldron_version=">=0.1.0",
    django_apps=("cauldron_content_operations",),
    requires=(
        ModuleRequirement(slug="content.routing", kind="capability"),
        ModuleRequirement(slug="content.changesets", kind="capability"),
        ModuleRequirement(slug="workspace.changesets", kind="capability"),
        ModuleRequirement(slug="workspace.snapshots", kind="capability"),
        ModuleRequirement(slug="django.state", kind="capability"),
        ModuleRequirement(slug="identity.authentication", kind="capability"),
        ModuleRequirement(slug="identity.permissions", kind="capability"),
        ModuleRequirement(slug="cauldron.content", kind="module"),
        ModuleRequirement(slug="cauldron.workspace.flatfile", kind="module"),
    ),
    provides=(
        "content.operations",
        "content.authorization",
        "content.approvals",
        "content.audit",
        "content.reconciliation",
    ),
    namespaces=("cauldron_content_operations",),
    public_api=(
        "cauldron_content_operations.service",
        "cauldron_content_operations.config",
        "cauldron_content_operations.lifecycle",
        "cauldron_content_operations.models",
        "cauldron_content_operations.reversible",
        "cauldron_content_operations.signals",
        "cauldron_content_operations.audit",
        "cauldron_content_operations.results",
    ),
    migration_apps=(
        ModuleMigrationDeclaration(app_label="cauldron_content_operations"),
    ),
    permissions=(
        ModulePermissionDeclaration(
            codename="view_published_content",
            name="Can view published content",
            app_label="cauldron_content_operations",
        ),
        ModulePermissionDeclaration(
            codename="view_draft_content",
            name="Can view draft content",
            app_label="cauldron_content_operations",
        ),
        ModulePermissionDeclaration(
            codename="view_content_change_requests",
            name="Can view content change requests",
            app_label="cauldron_content_operations",
        ),
        ModulePermissionDeclaration(
            codename="propose_content_changes",
            name="Can propose content changes",
            app_label="cauldron_content_operations",
        ),
        ModulePermissionDeclaration(
            codename="validate_content_changes",
            name="Can validate content changes",
            app_label="cauldron_content_operations",
        ),
        ModulePermissionDeclaration(
            codename="approve_content_changes",
            name="Can approve content changes",
            app_label="cauldron_content_operations",
        ),
        ModulePermissionDeclaration(
            codename="reject_content_changes",
            name="Can reject content changes",
            app_label="cauldron_content_operations",
        ),
        ModulePermissionDeclaration(
            codename="apply_content_changes",
            name="Can apply content changes",
            app_label="cauldron_content_operations",
        ),
        ModulePermissionDeclaration(
            codename="rollback_content_changes",
            name="Can roll back content changes",
            app_label="cauldron_content_operations",
        ),
        ModulePermissionDeclaration(
            codename="view_content_audit",
            name="Can view content audit history",
            app_label="cauldron_content_operations",
        ),
    ),
    presentation=ModulePresentation(
        title="Content Operations",
        summary="Content mutation operations — create, update, delete, publish, and approval workflows.",
        icon_svg=_ICON_SVG,
        group="Content",
        display_order=20,
    ),
)

module = BaseModule(_manifest)
