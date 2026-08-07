"""Django AppConfig for cauldron_django_admin."""
from django.apps import AppConfig


class CauldronDjangoAdminConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "cauldron_django_admin"
    verbose_name = "Cauldron Technical Admin"

    def ready(self) -> None:
        from . import checks  # noqa: F401 — registers @checks.register decorators
        self._configure_admin_site()
        self._register_navigation()

    def _configure_admin_site(self) -> None:
        from django.contrib import admin
        admin.site.site_header = "Cauldron Administration"
        admin.site.site_title = "Cauldron Administration"
        admin.site.index_title = "Cauldron Administration"

    def _register_navigation(self) -> None:
        # Exact re-registration of the same (section, item) is idempotent
        # inside the registry; a genuine conflict (same key, different
        # attributes) is a programming error and MUST propagate.
        from .navigation import get_navigation_registry, AdminNavigationSection, AdminNavigationItem
        registry = get_navigation_registry()
        registry.register_section(AdminNavigationSection(
            key="overview",
            label="Overview",
            order=10,
            owning_module="cauldron.django.admin",
        ))
        registry.register_item(AdminNavigationItem(
            key="cauldron.dashboard",
            label="Dashboard",
            url_name="cauldron:dashboard",
            section="overview",
            order=10,
            permission="",
            url_prefix="/cauldron/",
            # Dashboard shares its prefix with every nested cauldron page;
            # match exactly so it does not remain active on Modules etc.
            url_prefix_exact=True,
            owning_module="cauldron.django.admin",
        ))
        registry.register_section(AdminNavigationSection(
            key="system",
            label="System",
            order=900,
            owning_module="cauldron.django.admin",
        ))
        registry.register_item(AdminNavigationItem(
            key="cauldron.modules",
            label="Modules",
            url_name="cauldron:modules",
            section="system",
            order=10,
            permission="",
            url_prefix="/cauldron/modules/",
            description="Active Cauldron modules and capabilities",
            owning_module="cauldron.django.admin",
        ))
