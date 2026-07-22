"""Build a ContentOperationService from Django settings."""
from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured


def get_service():
    """Return a configured ContentOperationService from current Django settings.

    Raises :class:`ImproperlyConfigured` when the workspace cannot be built —
    the caller (view) is responsible for translating this into a bounded
    ``internal_error`` envelope. Filesystem paths are never leaked in the
    exception message.
    """
    from django.conf import settings
    from cauldron_content.registry import registry
    from cauldron_content.router import ContentRouter, RouterConfig
    from cauldron_content_operations.service import ContentOperationService
    from cauldron_content_operations.config import get_operations_config

    modules = getattr(settings, "CAULDRON_MODULES", {}) or {}
    content_cfg = modules.get("cauldron.content") or {}
    routing_cfg = content_cfg.get("routing") or {}

    router_config = RouterConfig(
        default_provider=routing_cfg.get("default_provider", ""),
        collections=routing_cfg.get("collections", {}),
    )
    router = ContentRouter(registry, router_config)

    ws_cfg_dict = modules.get("cauldron.workspace.flatfile") or {}
    wp = ws_cfg_dict.get("workspace_root", "")
    if not wp:
        raise ImproperlyConfigured(
            "Workspace root is required for cauldron.content.api; "
            "configure CAULDRON_MODULES['cauldron.workspace.flatfile']['workspace_root']."
        )
    try:
        from cauldron_workspace_flatfile.config import WorkspaceConfig
        from cauldron_workspace_flatfile.store import ChangeSetStore
        from cauldron_workspace_flatfile.snapshots import SnapshotService
        workspace_config = WorkspaceConfig(workspace_root=wp)
        workspace = ChangeSetStore(workspace_config)
        snapshots = SnapshotService(workspace_config)
        locks_dir = workspace_config.locks_dir
    except Exception as exc:
        # Bounded message; no path leakage.
        raise ImproperlyConfigured(
            f"Failed to initialize workspace for cauldron.content.api: {type(exc).__name__}"
        ) from exc

    # Best-effort adapter registration for the flatfile provider.
    try:
        cms_cfg = modules.get("cauldron.cms.flatfile") or {}
        content_root = cms_cfg.get("content_root", "")
        if content_root:
            from cauldron_workspace_flatfile.reversible import (
                FlatFileReversibleMutationAdapter,
            )
            from cauldron_content_operations.reversible import register_adapter, get_adapter
            if get_adapter("flatfile") is None:
                register_adapter(
                    "flatfile",
                    FlatFileReversibleMutationAdapter(workspace_config, content_root),
                )
    except Exception:
        # Adapter registration is best-effort; missing registration should not
        # prevent the API from serving unrelated endpoints.
        pass

    return ContentOperationService(
        router=router,
        workspace=workspace,
        snapshots=snapshots,
        config=get_operations_config(),
        locks_dir=locks_dir,
    )
