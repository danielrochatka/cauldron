"""Register the module tree navigation item into the Cauldron admin shell."""
from cauldron_django_admin.navigation import (
    AdminNavigationItem,
    get_navigation_registry,
)


def _register() -> None:
    registry = get_navigation_registry()
    registry.register_item(AdminNavigationItem(
        key="cauldron.module.tree",
        label="Dependency Tree",
        section="cauldron.modules",
        url_name="cauldron_module_tree:tree",
        order=10,
        permission="cauldron_module_tree.view_module_tree",
        url_prefix="/cauldron/module-tree/",
        description="Interactive module dependency graph",
    ))


_register()
