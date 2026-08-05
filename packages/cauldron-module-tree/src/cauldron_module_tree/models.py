"""Django models for cauldron_module_tree."""
from django.db import models


class ModuleEnabledOverride(models.Model):
    """Persists a desired enabled-state override for a module.

    This records the operator's intent, separate from the current runtime state
    (which comes from CAULDRON_MODULES in Django settings). After changing an
    override, a server restart is required for the change to take effect.
    """

    slug = models.CharField(max_length=200, unique=True, db_index=True)
    enabled = models.BooleanField()
    changed_at = models.DateTimeField(auto_now=True)
    changed_by = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="module_overrides",
    )
    reason = models.TextField(blank=True)

    class Meta:
        app_label = "cauldron_module_tree"
        verbose_name = "Module enabled override"
        verbose_name_plural = "Module enabled overrides"
        ordering = ["slug"]
        permissions = [
            ("view_module_tree", "Can view module dependency tree"),
            ("change_module_state", "Can enable or disable modules"),
        ]

    def __str__(self) -> str:
        state = "enabled" if self.enabled else "disabled"
        return f"{self.slug} ({state})"
