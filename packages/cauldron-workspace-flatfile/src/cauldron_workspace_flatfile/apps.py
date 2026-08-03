"""Django AppConfig for cauldron_workspace_flatfile."""
from django.apps import AppConfig

from cauldron_content.reversible import get_adapter, register_adapter

from .config import WorkspaceConfig
from .reversible import FlatFileReversibleMutationAdapter


class CauldronWorkspaceFlatfileConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "cauldron_workspace_flatfile"
    verbose_name = "Cauldron Flat-File Workspace"

    def ready(self) -> None:
        from . import checks  # noqa: F401
        self._maybe_register_reversible_adapter()

    def _maybe_register_reversible_adapter(self) -> None:
        """Register a FlatFileReversibleMutationAdapter when workspace/content roots are configured.

        Uses the canonical reversible adapter contract from ``cauldron_content.reversible``.

        Registration is skipped when either ``workspace_root`` (from
        ``CAULDRON_MODULES["cauldron.workspace.flatfile"]``) or ``content_root``
        (from ``CAULDRON_MODULES["cauldron.cms.flatfile"]``) is unconfigured.

        If a ``"flatfile"`` adapter is already registered the existing
        registration is preserved (idempotent re-entrant startup).

        Any other failure — invalid ``WorkspaceConfig``, adapter construction
        error, or ``AdapterVersionMismatch`` from ``register_adapter`` — is
        allowed to propagate so it surfaces as a visible startup failure rather
        than a silent no-op.
        """
        from django.conf import settings

        modules = getattr(settings, "CAULDRON_MODULES", {}) or {}
        ws_cfg = modules.get("cauldron.workspace.flatfile") or {}
        cms_cfg = modules.get("cauldron.cms.flatfile") or {}
        workspace_root = ws_cfg.get("workspace_root")
        content_root = cms_cfg.get("content_root")
        if not workspace_root or not content_root:
            return

        if get_adapter("flatfile") is not None:
            return

        adapter = FlatFileReversibleMutationAdapter(
            WorkspaceConfig(workspace_root=workspace_root),
            content_root,
        )
        register_adapter("flatfile", adapter)
