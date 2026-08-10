"""Cauldron Admin Content module definition."""
from cauldron.modules import (
    BaseModule,
    ModuleManifest,
    ModuleNavigationDeclaration,
    ModulePresentation,
    ModuleRequirement,
)

_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>"""

_manifest = ModuleManifest(
    slug="cauldron.admin.content",
    label="Cauldron Admin Content",
    version="0.1.0",
    cauldron_version=">=0.1.0",
    django_apps=("cauldron_admin_content",),
    requires=(
        ModuleRequirement(slug="content.operations", kind="capability"),
        ModuleRequirement(slug="admin.interface", kind="capability"),
        # admin-content pages extend the Cauldron Admin Shell templates;
        # depend on that capability explicitly so misconfigured deployments
        # fail loudly at resolution time.
        ModuleRequirement(slug="admin.shell", kind="capability"),
        ModuleRequirement(slug="cauldron.django.admin", kind="module"),
        ModuleRequirement(slug="cauldron.content", kind="module"),
        ModuleRequirement(slug="cauldron.content.operations", kind="module"),
        ModuleRequirement(slug="cauldron.workspace.flatfile", kind="module"),
    ),
    optional=(),
    provides=(
        "admin.content",
        "admin.content.changerequests",
        "admin.content.audit",
    ),
    namespaces=("cauldron_admin_content",),
    public_api=(
        "cauldron_admin_content.views",
        "cauldron_admin_content.urls",
        "cauldron_admin_content.forms",
        "cauldron_admin_content.service_factory",
        "cauldron_admin_content.checks",
    ),
    navigation=(
        ModuleNavigationDeclaration(key="content", label="Content", order=200),
        ModuleNavigationDeclaration(
            key="cauldron.admin.content.browser",
            label="Content Browser",
            section="content",
            url_name="cauldron_admin_content:content-browser",
            order=10,
            permission="cauldron_content_operations.view_published_content",
            url_prefix="/cauldron/content/",
            description="Browse published and draft content",
        ),
        ModuleNavigationDeclaration(
            key="cauldron.admin.content.homepage",
            label="Homepage",
            section="content",
            url_name="cauldron_admin_content:homepage",
            order=15,
            permission="cauldron_content_operations.view_published_content",
            url_prefix="/cauldron/content/homepage/",
            url_prefix_exact=True,
            description="Edit the Homepage singleton",
        ),
        ModuleNavigationDeclaration(
            key="cauldron.admin.content.page-create",
            label="New Page",
            section="content",
            url_name="cauldron_admin_content:page-create",
            order=20,
            permission="cauldron_content_operations.propose_content_changes",
            url_prefix="/cauldron/content/pages/new/",
            url_prefix_exact=True,
            description="Create a new page proposal",
        ),
        ModuleNavigationDeclaration(
            key="cauldron.admin.content.change-requests",
            label="Change Requests",
            section="content",
            url_name="cauldron_admin_content:change-request-list",
            order=30,
            permission="cauldron_content_operations.view_content_change_requests",
            url_prefix="/cauldron/content/change-requests/",
            description="Review content change requests",
        ),
        ModuleNavigationDeclaration(
            key="cauldron.admin.content.audit",
            label="Audit Log",
            section="content",
            url_name="cauldron_admin_content:audit-list",
            order=40,
            permission="cauldron_content_operations.view_content_audit",
            url_prefix="/cauldron/content/audit/",
            description="View content audit history",
        ),
    ),
    presentation=ModulePresentation(
        title="Admin Content",
        summary="Admin interface for browsing, editing, and publishing content from the operator shell.",
        icon_svg=_ICON_SVG,
        group="Content",
        display_order=40,
    ),
)

module = BaseModule(_manifest)
