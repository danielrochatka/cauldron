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
            from cauldron_django_admin.navigation import get_navigation_registry, AdminNavigationSection, AdminNavigationItem
        except ImportError:
            return
        registry = get_navigation_registry()
        try:
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
                key="cauldron.admin.content.proposal",
                label="New Proposal",
                url_name="cauldron_admin_content:content-proposal",
                section="content",
                order=20,
                permission="cauldron_content_operations.propose_content_changes",
                url_prefix="/cauldron/content-proposal/",
                description="Create a new content change proposal",
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
        except ValueError:
            pass  # idempotent
