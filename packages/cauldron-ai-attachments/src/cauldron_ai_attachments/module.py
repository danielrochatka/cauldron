"""Cauldron AI Attachments module definition."""
from cauldron.modules import (
    BaseModule,
    ModuleManifest,
    ModuleMigrationDeclaration,
    ModulePermissionDeclaration,
    ModulePresentation,
    ModuleRequirement,
)

_manifest = ModuleManifest(
    slug="cauldron.ai.attachments",
    label="Cauldron AI Attachments",
    version="0.1.0",
    cauldron_version=">=0.1.0",
    django_apps=("cauldron_ai_attachments",),
    requires=(),
    optional=(
        ModuleRequirement(slug="cauldron.ai", kind="module"),
        ModuleRequirement(slug="cauldron.ai.admin", kind="module"),
    ),
    provides=("admin.ai.attachments",),
    namespaces=("cauldron_ai_attachments",),
    public_api=(
        "cauldron_ai_attachments.module",
        "cauldron_ai_attachments.models",
        "cauldron_ai_attachments.service",
        "cauldron_ai_attachments.extractors",
        "cauldron_ai_attachments.tools",
        "cauldron_ai_attachments.views",
        "cauldron_ai_attachments.urls",
    ),
    migration_apps=(
        ModuleMigrationDeclaration(app_label="cauldron_ai_attachments"),
    ),
    permissions=(
        ModulePermissionDeclaration(
            codename="upload_attachment",
            name="Can upload Admin AI attachments",
            app_label="cauldron_ai_attachments",
        ),
        ModulePermissionDeclaration(
            codename="read_attachment",
            name="Can read Admin AI attachment content",
            app_label="cauldron_ai_attachments",
        ),
    ),
    ai_tools=("attachments.read",),
    prompt_templates=("attachments.read",),
    presentation=ModulePresentation(
        title="AI Attachments",
        summary="File attachment ingestion for Admin AI — extracts text from PDF, DOCX, and plain text documents.",
        group="AI",
        display_order=41,
    ),
)

module = BaseModule(_manifest)
