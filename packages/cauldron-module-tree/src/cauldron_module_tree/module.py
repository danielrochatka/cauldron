"""Cauldron Module Dependency Tree module manifest."""
from cauldron.modules import (
    BaseModule,
    ModuleManifest,
    ModuleMigrationDeclaration,
    ModuleNavigationDeclaration,
    ModulePermissionDeclaration,
    ModulePresentation,
    ModuleRequirement,
)

_ICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="1.5" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true">'
    # Left node
    '<circle cx="4" cy="12" r="2.5"/>'
    # Centre node
    '<circle cx="12" cy="6" r="2.5"/>'
    # Right node
    '<circle cx="20" cy="12" r="2.5"/>'
    # Bottom node
    '<circle cx="12" cy="18" r="2.5"/>'
    # Edges: left-centre, right-centre, bottom-centre
    '<line x1="6.2" y1="10.8" x2="9.8" y2="7.2"/>'
    '<line x1="14.2" y1="7.2" x2="17.8" y2="10.8"/>'
    '<line x1="12" y1="8.5" x2="12" y2="15.5"/>'
    '</svg>'
)

_manifest = ModuleManifest(
    slug="cauldron.module.tree",
    label="Module Dependency Tree",
    version="0.1.0",
    django_apps=("cauldron_module_tree",),
    requires=(
        ModuleRequirement(slug="admin.shell", kind="capability"),
        ModuleRequirement(slug="cauldron.django.admin", kind="module"),
    ),
    provides=("module.tree",),
    namespaces=("cauldron_module_tree",),
    public_api=(
        "cauldron_module_tree.urls",
        "cauldron_module_tree.views",
    ),
    migration_apps=(
        ModuleMigrationDeclaration(app_label="cauldron_module_tree"),
    ),
    permissions=(
        ModulePermissionDeclaration(
            codename="view_module_tree",
            name="Can view the module dependency tree",
            app_label="cauldron_module_tree",
        ),
        ModulePermissionDeclaration(
            codename="change_module_state",
            name="Can enable and disable modules",
            app_label="cauldron_module_tree",
        ),
    ),
    navigation=(
        ModuleNavigationDeclaration(
            key="cauldron.module.tree",
            label="Dependency Tree",
            section="cauldron.modules",
            url_name="cauldron_module_tree:tree",
            order=10,
            permission="cauldron_module_tree.view_module_tree",
            url_prefix="/cauldron/module-tree/",
            description="Interactive module dependency graph",
        ),
    ),
    presentation=ModulePresentation(
        title="Module Dependency Tree",
        summary=(
            "Visualise the full Cauldron module graph — dependencies, capabilities, "
            "and activation state — in an interactive diagram."
        ),
        icon_svg=_ICON_SVG,
        group="System",
        display_order=20,
    ),
)

module = BaseModule(_manifest)
