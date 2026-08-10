"""Django AppConfig for cauldron_ai_admin."""
from django.apps import AppConfig


class CauldronAIAdminConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "cauldron_ai_admin"
    verbose_name = "Cauldron Admin AI"

    def ready(self) -> None:
        # Register system checks.
        from . import checks  # noqa: F401
        # Register the six built-in tools with the shared registry.
        # This runs exactly once per process at Django startup.
        from . import builtin_tools
        builtin_tools.register_builtin_tools()
        # Register the global operating prompt and per-tool prompt templates.
        from . import builtin_tool_prompts
        builtin_tool_prompts.register_builtin_tool_prompts()
        # Register navigation items with the admin shell.
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
        # Exact re-registration is idempotent inside the registry; a real
        # conflict is a programming error and MUST propagate.
        registry.register_section(AdminNavigationSection(
            key="ai",
            label="Admin AI",
            order=500,
            owning_module="cauldron.ai.admin",
        ))
        registry.register_item(AdminNavigationItem(
            key="cauldron.ai.admin.page",
            label="AI Assistant",
            url_name="cauldron_ai_admin:ai-page",
            section="ai",
            order=10,
            permission="cauldron_ai_admin.use_admin_ai",
            url_prefix="/cauldron/admin/ai/",
            description="Interact with the Admin AI assistant",
            owning_module="cauldron.ai.admin",
        ))
        registry.register_item(AdminNavigationItem(
            key="cauldron.ai.admin.runs",
            label="AI Runs",
            url_name="cauldron_ai_admin:run-list",
            section="ai",
            order=20,
            permission="cauldron_ai_admin.view_admin_ai_runs",
            url_prefix="/cauldron/admin/ai/runs/",
            description="View Admin AI run history",
            owning_module="cauldron.ai.admin",
        ))
        registry.register_item(AdminNavigationItem(
            key="cauldron.ui.styles",
            label="Style Proposals",
            url_name="cauldron_ai_admin:style-list",
            section="ai",
            order=30,
            permission="cauldron_ai_admin.view_ui_styles",
            url_prefix="/cauldron/ui/style-changes/",
            description="Review AI-proposed CSS changes",
            owning_module="cauldron.ai.admin",
        ))
        # Settings spec — projects as kind="settings" at the end of the ai section.
        try:
            from cauldron_django_admin.module_settings import (
                ModuleSettingsSpec,
                register_module_settings,
            )
            register_module_settings(ModuleSettingsSpec(
                module_slug="cauldron.ai.admin",
                url_name="cauldron_ai_admin:settings",
                navigation_section="ai",
                permission="cauldron_ai_admin.manage_admin_ai_settings",
                description="Configure the Admin AI module",
            ))
        except ImportError:
            pass
