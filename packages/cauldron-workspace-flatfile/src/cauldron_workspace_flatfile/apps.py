"""Django AppConfig for cauldron_workspace_flatfile."""
from django.apps import AppConfig


class CauldronWorkspaceFlatfileConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "cauldron_workspace_flatfile"
    verbose_name = "Cauldron Flat-File Workspace"

    def ready(self) -> None:
        from . import checks  # noqa: F401
        self._maybe_register_reversible_adapter()

    def _maybe_register_reversible_adapter(self) -> None:
        """Register a FlatFileReversibleMutationAdapter if the CMS is configured.

        The registration is best-effort: it is skipped silently when the CMS
        flatfile provider or the content operations package are not installed.
        """
        try:
            from django.conf import settings
        except Exception:  # pragma: no cover - django must be present
            return
        try:
            from cauldron_content_operations.reversible import (
                get_adapter,
                register_adapter,
            )
        except Exception:
            return
        try:
            from .config import WorkspaceConfig
            from .reversible import FlatFileReversibleMutationAdapter
        except Exception:
            return

        modules = getattr(settings, "CAULDRON_MODULES", {}) or {}
        ws_cfg = modules.get("cauldron.workspace.flatfile") or {}
        cms_cfg = modules.get("cauldron.cms.flatfile") or {}
        workspace_root = ws_cfg.get("workspace_root")
        content_root = cms_cfg.get("content_root")
        if not workspace_root or not content_root:
            return

        provider_name = "flatfile"
        if get_adapter(provider_name) is not None:
            return
        try:
            adapter = FlatFileReversibleMutationAdapter(
                WorkspaceConfig(workspace_root=workspace_root),
                content_root,
            )
            register_adapter(provider_name, adapter)
        except Exception:
            return
