"""Build a ContentOperationService from Django settings."""
from __future__ import annotations


def get_service():
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

    workspace = None
    snapshots = None
    locks_dir = None
    ws_cfg_dict = modules.get("cauldron.workspace.flatfile") or {}
    wp = ws_cfg_dict.get("workspace_root", "")
    if wp:
        try:
            from cauldron_workspace_flatfile.config import WorkspaceConfig
            from cauldron_workspace_flatfile.store import ChangeSetStore
            from cauldron_workspace_flatfile.snapshots import SnapshotService
            workspace_config = WorkspaceConfig(workspace_root=wp)
            workspace = ChangeSetStore(workspace_config)
            snapshots = SnapshotService(workspace_config)
            locks_dir = workspace_config.locks_dir
        except Exception:
            pass

    return ContentOperationService(
        router=router,
        workspace=workspace,
        snapshots=snapshots,
        config=get_operations_config(),
        locks_dir=locks_dir,
    )
