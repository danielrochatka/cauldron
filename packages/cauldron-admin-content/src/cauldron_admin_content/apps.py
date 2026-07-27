"""Django AppConfig for cauldron_admin_content."""
from django.apps import AppConfig


class CauldronAdminContentConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "cauldron_admin_content"
    verbose_name = "Cauldron Admin Content"

    def ready(self) -> None:
        from . import checks  # noqa: F401
        self._register_navigation()

    def _register_navigation(self) -> None:
        try:
            from cauldron_django_admin.navigation import (
                get_navigation_registry,
                AdminNavigationSection,
                AdminNavigationItem,
            )
        except ImportError:
            return
        registry = get_navigation_registry()
        registry.register_section(AdminNavigationSection(
            key="content",
            label="Content",
            order=200,
        ))
        registry.register_item(AdminNavigationItem(
            key="cauldron.admin.content.browser",
            label="Content Browser",
            url_name="cauldron_admin_content:content-browser",
            section="content",
            order=10,
            permission="cauldron_content_operations.view_published_content",
            url_prefix="/cauldron/content/",
            description="Browse published and draft content",
        ))
        registry.register_item(AdminNavigationItem(
            key="cauldron.admin.content.homepage",
            label="Homepage",
            url_name="cauldron_admin_content:homepage",
            section="content",
            order=15,
            permission="cauldron_content_operations.view_published_content",
            url_prefix="/cauldron/content/homepage/",
            url_prefix_exact=True,
            description="Edit the Homepage singleton",
        ))
        registry.register_item(AdminNavigationItem(
            key="cauldron.admin.content.page-create",
            label="New Page",
            url_name="cauldron_admin_content:page-create",
            section="content",
            order=20,
            permission="cauldron_content_operations.propose_content_changes",
            url_prefix="/cauldron/content/pages/new/",
            url_prefix_exact=True,
            description="Create a new page proposal",
        ))
        registry.register_item(AdminNavigationItem(
            key="cauldron.admin.content.change-requests",
            label="Change Requests",
            url_name="cauldron_admin_content:change-request-list",
            section="content",
            order=30,
            permission="cauldron_content_operations.view_content_change_requests",
            url_prefix="/cauldron/content/change-requests/",
            description="Review content change requests",
        ))
        registry.register_item(AdminNavigationItem(
            key="cauldron.admin.content.audit",
            label="Audit Log",
            url_name="cauldron_admin_content:audit-list",
            section="content",
            order=40,
            permission="cauldron_content_operations.view_content_audit",
            url_prefix="/cauldron/content/audit/",
            description="View content audit history",
        ))
